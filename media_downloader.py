"""Downloads media from telegram."""

import asyncio
import logging
import os
import random
import re
from datetime import date, datetime, timezone
from typing import List, Optional, Tuple, Union

from rich.logging import RichHandler
from telethon import TelegramClient, events
from telethon.errors import FileReferenceExpiredError, FloodWaitError
from telethon.tl.types import (Document, Message, MessageMediaDocument,
                               MessageMediaPhoto, Photo)
from telethon.utils import get_display_name
from tqdm import tqdm

import config_manager
import db
from utils.file_management import get_next_name, manage_duplicate_file
from utils.log import LogFilter
from utils.meta import print_meta
from utils.parsing import safe_int
from utils.telegram_client import build_telegram_client

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler()],
)
logging.getLogger("telethon.client.downloads").addFilter(LogFilter())
logging.getLogger("telethon.network").addFilter(LogFilter())
logger = logging.getLogger("media_downloader")

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
FAILED_IDS: dict = {}
DOWNLOADED_IDS: dict = {}
PROCESSED_IDS: dict = {}
CURRENT_BATCH_IDS: dict = {}

# Messages with media received in monitor mode but not yet downloaded.
PENDING_IDS: dict = {}

# History mode backlog tracking: messages iterated vs processed per chat.
BACKLOG_ITERATED: dict = {}
BACKLOG_DONE: dict = {}

# Resolved chat titles for display in download history
CHAT_TITLES: dict = {}

# Global hook for Web UI to receive progress updates
UI_PROGRESS_HOOK = None

# Mutex for chat entity resolution to prevent concurrent session access
_VERIFY_LOCK = asyncio.Lock()


def reset_runtime_state():
    """Clear all global state dictionaries.

    Call this before starting a new history download or monitor session
    to prevent cross-mode data leaks.
    """
    PENDING_IDS.clear()
    FAILED_IDS.clear()
    DOWNLOADED_IDS.clear()
    PROCESSED_IDS.clear()
    CURRENT_BATCH_IDS.clear()
    BACKLOG_ITERATED.clear()
    BACKLOG_DONE.clear()
    CHAT_TITLES.clear()


def _get_chats_to_process(config: dict, *, raise_on_missing: bool = True) -> list:
    """Extract the list of chat configs from the config dictionary.

    Parameters
    ----------
    config: dict
        Configuration dictionary.
    raise_on_missing: bool
        If ``True``, raises ``KeyError`` when neither ``chats`` list nor
        ``chat_id`` key is found. Use ``False`` when the config has already
        been validated (e.g. during graceful shutdown in ``main()``).

    Returns
    -------
    list
        List of per-chat config dictionaries.

    Raises
    ------
    KeyError
        If ``raise_on_missing`` is ``True`` and no chats configuration found.
    """
    chats_config = config.get("chats", [])
    if chats_config:
        return chats_config
    if "chat_id" not in config and raise_on_missing:
        raise KeyError(
            "chat_id must be specified either in a chats list or globally."
        )
    return [config]


def update_config(config: dict):
    """
    Update existing configuration file.

    Parameters
    ----------
    config: dict
        Configuration to be written into config file.
    """
    chats_config = config.get("chats", [])
    if chats_config:
        for chat_conf in chats_config:
            chat_id = chat_conf.get("chat_id")
            if chat_id and chat_id in DOWNLOADED_IDS and chat_id in FAILED_IDS:
                merged = set(chat_conf.get("ids_to_retry", []))
                merged -= set(DOWNLOADED_IDS[chat_id])
                merged |= set(FAILED_IDS[chat_id])
                chat_conf["ids_to_retry"] = sorted(merged)
    else:
        chat_id = config.get("chat_id")
        if chat_id and chat_id in DOWNLOADED_IDS and chat_id in FAILED_IDS:
            merged = set(config.get("ids_to_retry", []))
            merged -= set(DOWNLOADED_IDS[chat_id])
            merged |= set(FAILED_IDS[chat_id])
            config["ids_to_retry"] = sorted(merged)

    config_manager.save_config(config)
    logger.info("Updated last read message_id to config file")


def _can_download(_type: str, file_formats: dict, file_format: str | None) -> bool:
    """
    Check if the given file format can be downloaded.

    Parameters
    ----------
    _type: str
        Type of media object.
    file_formats: dict
        Dictionary containing the list of file_formats
        to be downloaded for `audio`, `document` & `video`
        media types
    file_format: str
        Format of the current file to be downloaded.

    Returns
    -------
    bool
        True if the file format can be downloaded else False.
    """
    if _type in ["audio", "document", "video"]:
        allowed_formats: list = file_formats[_type]
        if file_format not in allowed_formats and allowed_formats[0] != "all":
            return False
    return True


def _is_exist(file_path: str) -> bool:
    """
    Check if a file exists, is not a directory and has non-zero size.

    Parameters
    ----------
    file_path: str
        Absolute path of the file to be checked.

    Returns
    -------
    bool
        True if the file exists and has content, else False.
    """
    if os.path.isdir(file_path):
        return False
    if not os.path.exists(file_path):
        return False
    if os.path.getsize(file_path) == 0:
        return False
    return True


def _cleanup_partial(file_path: str) -> None:
    """Remove a zero-byte file left by an interrupted download.

    Parameters
    ----------
    file_path: str
        Absolute path of the possibly partial file.
    """
    if os.path.exists(file_path) and not os.path.isdir(file_path):
        if os.path.getsize(file_path) == 0:
            try:
                os.remove(file_path)
            except OSError:
                pass


def _resolve_download_directory(chat_conf: dict, global_config: dict) -> str | None:
    """Resolve download directory with chat->global fallback.

    Parameters
    ----------
    chat_conf: dict
        Per-chat configuration dictionary.
    global_config: dict
        Global configuration dictionary.

    Returns
    -------
    Optional[str]
        Absolute path to the download directory, or ``None``.
    """
    val = chat_conf.get(
        "download_directory", global_config.get("download_directory")
    )
    if isinstance(val, str) and val.strip():
        directory = val.strip()
        if not os.path.isabs(directory):
            directory = os.path.abspath(directory)
        os.makedirs(directory, exist_ok=True)
        return directory
    return None


def _resolve_monitor_settings(global_config: dict, chat_conf: dict) -> dict:
    """Resolve media_types, file_formats, max_concurrent_downloads and
    download_directory with fallback chat -> global.

    Parameters
    ----------
    global_config: dict
        Global configuration dictionary.
    chat_conf: dict
        Per-chat configuration dictionary.

    Returns
    -------
    dict
        Resolved settings for the chat.
    """
    media_types = chat_conf.get("media_types", global_config.get("media_types", []))
    file_formats = chat_conf.get("file_formats", global_config.get("file_formats", {}))

    raw_concurrent = chat_conf.get(
        "max_concurrent_downloads",
        global_config.get("max_concurrent_downloads", 1),
    )
    max_concurrent_downloads = safe_int(raw_concurrent, default=1, min_value=1)
    if max_concurrent_downloads == 1 and raw_concurrent not in (1, None):
        logger.warning(
            "Invalid max_concurrent_downloads %r; defaulting to 1.",
            raw_concurrent,
        )

    download_directory = _resolve_download_directory(chat_conf, global_config)

    return {
        "media_types": media_types,
        "file_formats": file_formats,
        "max_concurrent_downloads": max_concurrent_downloads,
        "download_directory": download_directory,
    }


def _progress_callback(current: int, total: int, pbar: tqdm) -> None:
    """
    Update progress bar for file downloads.

    Parameters
    ----------
    current: int
        Current number of bytes downloaded.
    total: int
        Total number of bytes to download.
    pbar: tqdm
        Progress bar instance to update.
    """
    global UI_PROGRESS_HOOK

    if pbar.total != total:
        pbar.total = total
        pbar.reset()
    pbar.update(current - pbar.n)

    if UI_PROGRESS_HOOK is not None:
        try:
            UI_PROGRESS_HOOK(pbar.desc, current, total)
        except RuntimeError:
            pass
        except Exception:
            UI_PROGRESS_HOOK = None
            logger.warning(
                "UI progress hook failed; disabling for remainder of session."
            )


async def _get_media_meta(  # NOSONAR
    media_obj: Document | Photo,
    _type: str,
    chat_id: int | str,
    download_directory: str | None = None,
) -> tuple[str, str | None]:
    """Extract file name and file id from media object.

    Parameters
    ----------
    media_obj: Union[Document, Photo]
        Media object to be extracted.
    _type: str
        Type of media object.
    chat_id: Union[int, str]
        ID of the chat, used for folder structuring.
    download_directory: Optional[str]
        Custom directory path for downloads. If None, uses default structure.

    Returns
    -------
    Tuple[str, Optional[str]]
        file_name, file_format
    """
    file_format: str | None = None
    if hasattr(media_obj, "mime_type") and media_obj.mime_type:
        file_format = media_obj.mime_type.split("/")[-1]
    elif _type == "photo":
        file_format = "jpg"

    # Determine base directory for downloads
    if download_directory:
        base_dir = download_directory
    else:
        base_dir = os.path.join(THIS_DIR, str(chat_id))

    if _type in ["voice", "video_note"]:
        file_name_base = f"{_type}_{media_obj.date.isoformat()}.{file_format}"
    else:
        file_name_base = ""
        if hasattr(media_obj, "attributes"):
            for attr in media_obj.attributes:
                if hasattr(attr, "file_name"):
                    file_name_base = attr.file_name
                    break
        if file_name_base == "" and hasattr(media_obj, "id"):
            file_name_base = f"{_type}_{media_obj.id}"

    # Sanitize the file name to remove invalid Windows characters
    file_name_base = re.sub(r'[<>:"/\\|?*]', "_", file_name_base)
    file_name = os.path.join(base_dir, _type, file_name_base)
    return file_name, file_format


def get_media_type(message: Message) -> str | None:  # NOSONAR
    """
    Determine the media type from the message's media attributes.

    Parameters
    ----------
    message: Message
        The Telethon message object.

    Returns
    -------
    Optional[str]
        The media type ('photo', 'video', 'audio', 'voice', 'video_note', 'document')
        or None.
    """
    if not message.media:
        return None
    if isinstance(message.media, MessageMediaPhoto):
        return "photo"
    if isinstance(message.media, MessageMediaDocument):
        doc = message.media.document
        for attr in doc.attributes:
            if hasattr(attr, "voice") and attr.voice is not None:
                return "voice" if attr.voice else "audio"
            if hasattr(attr, "round_message") and attr.round_message is not None:
                return "video_note" if attr.round_message else "video"
        return "document"
    return None


# pylint: disable=too-many-nested-blocks
async def download_media(  # pylint: disable=too-many-locals,too-many-branches,too-many-positional-arguments,too-many-statements  # NOSONAR
    client: TelegramClient,
    message: Message,
    media_types: list[str],
    file_formats: dict,
    chat_id: int | str,
    download_directory: str | None = None,
):
    """
    Download media from Telegram.

    Each of the files to download are retried 3 times with a
    delay of 5 seconds each.

    Parameters
    ----------
    client: TelegramClient
        Client to interact with Telegram APIs.
    message: Message
        Message object retrieved from telegram.
    media_types: list
        List of strings of media types to be downloaded.
        Ex : ["audio", "photo"]
    file_formats: dict
        Dictionary containing the list of file_formats
        to be downloaded.
    chat_id: Union[int, str]
        ID of the chat being processed.
    download_directory: Optional[str]
        Custom directory path for downloads. If None, uses default structure.

    Returns
    -------
    int
        Current message id.
    """
    for retry in range(3):
        file_name = None
        if chat_id not in FAILED_IDS:
            FAILED_IDS[chat_id] = []
        if chat_id not in DOWNLOADED_IDS:
            DOWNLOADED_IDS[chat_id] = []
        if chat_id not in PROCESSED_IDS:
            PROCESSED_IDS[chat_id] = []
        try:
            _type = get_media_type(message)
            logger.debug("Processing message %s of type %s", message.id, _type)
            if not _type or _type not in media_types:
                PROCESSED_IDS[chat_id].append(message.id)
                return message.id
            media_obj = message.photo if _type == "photo" else message.document
            if not media_obj:
                PROCESSED_IDS[chat_id].append(message.id)
                return message.id
            file_name, file_format = await _get_media_meta(
                media_obj, _type, chat_id, download_directory
            )
            if _can_download(_type, file_formats, file_format):
                file_size = getattr(media_obj, "size", 0)
                display_name = getattr(
                    media_obj, "file_name", os.path.basename(file_name)
                )
                desc = f"Downloading {display_name}"
                logger.info(desc)

                if _is_exist(file_name):
                    file_name = get_next_name(file_name)
                    with tqdm(
                        total=file_size, unit="B", unit_scale=True, desc=desc
                    ) as pbar:
                        download_path = await client.download_media(
                            message,
                            file=file_name,
                            progress_callback=lambda c, t, pbar=pbar: _progress_callback(
                                c, t, pbar
                            ),
                        )
                        download_path = manage_duplicate_file(
                            download_path
                        )  # type: ignore
                else:
                    with tqdm(
                        total=file_size, unit="B", unit_scale=True, desc=desc
                    ) as pbar:
                        download_path = await client.download_media(
                            message,
                            file=file_name,
                            progress_callback=lambda c, t, pbar=pbar: _progress_callback(
                                c, t, pbar
                            ),
                        )
                if download_path:
                    logger.info("Media downloaded - %s", download_path)
                    logger.debug("Successfully downloaded message %s", message.id)
                    abs_path = os.path.abspath(download_path)
                    actual_size = (
                        os.path.getsize(abs_path)
                        if os.path.exists(abs_path)
                        else file_size
                    )
                    actual_name = os.path.basename(abs_path)
                    db.record_download(
                        str(chat_id),
                        message.id,
                        actual_name,
                        actual_size,
                        abs_path,
                        _type,
                        CHAT_TITLES.get(str(chat_id)),
                    )
                    if UI_PROGRESS_HOOK is not None:
                        # Optional signature expansion for UI logic
                        try:
                            UI_PROGRESS_HOOK(
                                desc,
                                actual_size,
                                actual_size,
                                file_path=abs_path,
                                media_type=_type,
                            )
                        except TypeError:
                            pass
                DOWNLOADED_IDS[chat_id].append(message.id)

            PROCESSED_IDS[chat_id].append(message.id)
            break
        except FileReferenceExpiredError:
            logger.warning(
                "Message[%d]: file reference expired, refetching...", message.id
            )
            messages = await client.get_messages(message.chat.id, ids=message.id)
            message = messages[0] if messages else message
            if retry == 2:
                logger.error(
                    "Message[%d]: file reference expired, skipping download.",
                    message.id,
                )
                FAILED_IDS[chat_id].append(message.id)
        except TimeoutError:
            logger.warning(
                "Timeout Error occurred when downloading Message[%d], "
                "retrying after 5 seconds",
                message.id,
            )
            if file_name:
                _cleanup_partial(file_name)
            await asyncio.sleep(5)
            if retry == 2:
                logger.error(
                    "Message[%d]: Timing out after 3 retries, download skipped.",
                    message.id,
                )
                FAILED_IDS[chat_id].append(message.id)
        except FloodWaitError as e:
            logger.warning(
                "Message[%d]: flood wait %ds, sleeping...",
                message.id,
                e.seconds,
            )
            await asyncio.sleep(e.seconds)
            continue
        except (ConnectionError, TimeoutError) as conn_err:
            if file_name:
                _cleanup_partial(file_name)
            msg = str(conn_err)
            logger.info(
                "Message[%d]: connection lost (%s), waiting 3s "
                "for auto-reconnect (attempt %d/3)...",
                message.id,
                msg[:80],
                retry + 1,
            )
            await asyncio.sleep(3)
            if retry == 2:
                logger.error(
                    "Message[%d]: connection lost after 3 retries, "
                    "skipping.",
                    message.id,
                )
                FAILED_IDS[chat_id].append(message.id)
        except Exception as e:
            if file_name:
                _cleanup_partial(file_name)
            logger.error(
                "Message[%d]: could not be downloaded due to "
                "following exception:\n[%s].",
                message.id,
                e,
                exc_info=True,
            )
            FAILED_IDS[chat_id].append(message.id)
            break
    return message.id


def _resolve_download_delay(download_delay) -> float | None:
    """Parse and compute the download delay value.

    Parameters
    ----------
    download_delay : float, list, or None
        Delay configuration from config.

    Returns
    -------
    Optional[float]
        Computed delay in seconds, or None to skip.
    """
    if download_delay is None:
        return None
    if isinstance(download_delay, (list, tuple)):
        if len(download_delay) != 2:
            logger.warning(
                "download_delay list must have exactly 2 elements "
                "[min, max]; got %r. Skipping delay.",
                download_delay,
            )
            return None
        try:
            lo, hi = float(download_delay[0]), float(download_delay[1])
            return max(0.0, random.uniform(lo, hi))
        except (TypeError, ValueError):
            logger.warning(
                "download_delay list %r contains non-numeric values; "
                "skipping delay.",
                download_delay,
            )
            return None
    try:
        return max(0.0, float(download_delay))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        logger.warning(
            "Invalid download_delay value %r; skipping delay.",
            download_delay,
        )
    return None


async def process_messages(  # pylint: disable=too-many-positional-arguments
    client: TelegramClient,
    messages: list[Message],
    media_types: list[str],
    file_formats: dict,
    chat_id: int | str,
    download_directory: str | None = None,
    max_concurrent_downloads: int = 1,
    download_delay: float | list[float] | None = None,
    global_semaphore: asyncio.Semaphore | None = None,
) -> int:
    """
    Download media from Telegram.

    Parameters
    ----------
    client: TelegramClient
        Client to interact with Telegram APIs.
    messages: list
        List of telegram messages.
    media_types: list
        List of strings of media types to be downloaded.
    file_formats: dict
        Dictionary containing the list of file_formats
        to be downloaded.
    chat_id: Union[int, str]
        ID of the chat.
    download_directory: Optional[str]
        Custom directory path for downloads. If None, uses default structure.
    max_concurrent_downloads: int
        Max number of files to download simultaneously. 1 = fully sequential.
        Default 1. Higher values speed up downloads but increase ban risk.
    download_delay: Optional[Union[float, List[float]]]
        Delay between starting each file download (seconds).
        Pass a float for a fixed delay, or [min, max] for a random range.
        None means no delay.

    Returns
    -------
    int
        Max value of list of message ids.
    """
    semaphore = asyncio.Semaphore(max(1, max_concurrent_downloads))

    async def _download_with_limit(message: Message) -> int:
        lock = global_semaphore if global_semaphore is not None else semaphore
        async with lock:
            delay = _resolve_download_delay(download_delay)
            if delay is not None:
                if delay > 0:
                    logger.info("Waiting %.1fs before next download...", delay)
                await asyncio.sleep(delay)
            PENDING_IDS[chat_id] = PENDING_IDS.get(chat_id, 0) + 1
            msg_id = int(
                await download_media(
                    client,
                    message,
                    media_types,
                    file_formats,
                    chat_id,
                    download_directory,
                )
            )
            PENDING_IDS[chat_id] = max(0, PENDING_IDS.get(chat_id, 1) - 1)
            BACKLOG_DONE[chat_id] = BACKLOG_DONE.get(chat_id, 0) + 1
            return msg_id

    results = await asyncio.gather(
        *[_download_with_limit(message) for message in messages],
        return_exceptions=True,
    )
    logger.info("Processed batch of %d messages for chat %s", len(messages), chat_id)
    # Filter out exceptions — they were already logged in download_media()
    valid_ids: list[int] = [
        r for r in results if isinstance(r, (int, str)) and int(r) > 0
    ]
    last_message_id: int = max(valid_ids) if valid_ids else 0
    return last_message_id


def _resolve_date_filters(
    chat_conf: dict, global_config: dict
) -> tuple[datetime | None, datetime | None, int | None]:
    """Resolve date and message filters from chat and global config.

    Parameters
    ----------
    chat_conf: dict
        Per-chat configuration dictionary.
    global_config: dict
        Global configuration dictionary (fallback values).

    Returns
    -------
    tuple[Optional[datetime], Optional[datetime], Optional[int]]
        ``(start_date, end_date, max_messages)`` resolved with global
        fallback.  Dates are timezone-aware UTC, or ``None``.
    """
    start_date_val = chat_conf.get(
        "start_date", global_config.get("start_date")
    )
    if isinstance(start_date_val, str) and start_date_val.strip():
        start_date = datetime.fromisoformat(start_date_val)
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
    elif isinstance(start_date_val, date):
        start_date = datetime.combine(
            start_date_val, datetime.min.time(), tzinfo=timezone.utc
        )
    else:
        start_date = None

    end_date_val = chat_conf.get(
        "end_date", global_config.get("end_date")
    )
    if isinstance(end_date_val, str) and end_date_val.strip():
        end_date = datetime.fromisoformat(end_date_val)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
    elif isinstance(end_date_val, date):
        end_date = datetime.combine(
            end_date_val, datetime.min.time(), tzinfo=timezone.utc
        )
    else:
        end_date = None

    max_messages_val = chat_conf.get(
        "max_messages", global_config.get("max_messages")
    )
    if isinstance(max_messages_val, int):
        max_messages: int | None = max_messages_val
    elif isinstance(max_messages_val, str) and max_messages_val.strip():
        max_messages = int(max_messages_val)
    else:
        max_messages = None

    return start_date, end_date, max_messages


async def process_chat(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements  # NOSONAR
    client: TelegramClient,
    global_config: dict,
    chat_conf: dict,
    pagination_limit: int,
    config_write_lock: asyncio.Lock,
    global_semaphore: asyncio.Semaphore | None = None,
):
    """
    Process a single chat's media downloads.
    """
    chat_id = chat_conf["chat_id"]
    logger.info("Starting processing for chat_id: %s", chat_id)

    # Initialize state maps for this chat
    if chat_id not in FAILED_IDS:
        FAILED_IDS[chat_id] = []
    if chat_id not in DOWNLOADED_IDS:
        DOWNLOADED_IDS[chat_id] = []
    if chat_id not in PROCESSED_IDS:
        PROCESSED_IDS[chat_id] = []

    BACKLOG_ITERATED.setdefault(chat_id, 0)
    BACKLOG_DONE.setdefault(chat_id, 0)

    # Resolve chat title once for history display
    if str(chat_id) not in CHAT_TITLES:
        try:
            entity = await client.get_entity(chat_id)
            title = get_display_name(entity)
            if title:
                CHAT_TITLES[str(chat_id)] = title
        except Exception:
            logger.warning("Failed to resolve chat title for %s", chat_id, exc_info=True)
            CHAT_TITLES[str(chat_id)] = ""

    CURRENT_BATCH_IDS[chat_id] = []

    # Merge chat-specific config with global fallback
    media_types: list[str] = chat_conf.get(
        "media_types", global_config.get("media_types", [])
    )
    file_formats: dict = chat_conf.get(
        "file_formats", global_config.get("file_formats", {})
    )
    last_read_message_id = chat_conf.get(
        "last_read_message_id", global_config.get("last_read_message_id", 0)
    )
    _max_concurrent_raw = chat_conf.get(
        "max_concurrent_downloads", global_config.get("max_concurrent_downloads", 4)
    )
    max_concurrent_downloads = safe_int(_max_concurrent_raw, default=1, min_value=1)
    if max_concurrent_downloads == 1 and _max_concurrent_raw not in (1, None):
        logger.warning(
            "Invalid max_concurrent_downloads value %r; defaulting to 4.",
            _max_concurrent_raw,
        )
    download_delay = chat_conf.get(
        "download_delay", global_config.get("download_delay", 20)
    )

    start_date, end_date, max_messages = _resolve_date_filters(chat_conf, global_config)

    download_directory = _resolve_download_directory(chat_conf, global_config)

    messages_iter = client.iter_messages(
        chat_id, min_id=last_read_message_id, reverse=True
    )
    messages_list: list = []
    pagination_count: int = 0
    ids_to_retry = chat_conf.get("ids_to_retry", global_config.get("ids_to_retry", []))

    if ids_to_retry:
        logger.info("Downloading files failed during last run for chat %s...", chat_id)
        skipped_messages: list = await client.get_messages(  # type: ignore
            chat_id, ids=ids_to_retry
        )
        for message in skipped_messages:
            if message is None:
                continue
            pagination_count += 1
            messages_list.append(message)
        # Remove deleted messages from ids_to_retry
        chat_conf["ids_to_retry"] = [m.id for m in skipped_messages if m is not None]

    async for message in messages_iter:  # type: ignore
        BACKLOG_ITERATED[chat_id] = BACKLOG_ITERATED.get(chat_id, 0) + 1
        if end_date and message.date > end_date:
            continue
        if start_date and message.date < start_date:
            break
        if pagination_count != pagination_limit:
            pagination_count += 1
            messages_list.append(message)
        else:
            CURRENT_BATCH_IDS[chat_id] = [m.id for m in messages_list]
            last_read_message_id = await process_messages(
                client,
                messages_list,
                media_types,
                file_formats,
                chat_id,
                download_directory,
                max_concurrent_downloads,
                download_delay,
                global_semaphore=global_semaphore,
            )
            # Memory cleanup for next batch
            CURRENT_BATCH_IDS[chat_id] = []
            PROCESSED_IDS[chat_id] = []

            if max_messages and len(DOWNLOADED_IDS[chat_id]) >= max_messages:
                break
            pagination_count = 0
            messages_list = [message]
            chat_conf["last_read_message_id"] = last_read_message_id

            # Checkpoint: persist progress to disk after every batch so that
            # crashes or network failures don't lose progress.
            async with config_write_lock:
                update_config(global_config)

    if messages_list:
        CURRENT_BATCH_IDS[chat_id] = [m.id for m in messages_list]
        last_read_message_id = await process_messages(
            client,
            messages_list,
            media_types,
            file_formats,
            chat_id,
            download_directory,
            max_concurrent_downloads,
            download_delay,
            global_semaphore=global_semaphore,
        )
        CURRENT_BATCH_IDS[chat_id] = []
        PROCESSED_IDS[chat_id] = []

    chat_conf["last_read_message_id"] = last_read_message_id
    # Final checkpoint for this chat
    async with config_write_lock:
        update_config(global_config)


async def register_monitor_handler(  # NOSONAR
    client, global_config: dict, chat_conf: dict
) -> None:
    """Register a NewMessage handler for one chat in monitor mode.

    Parameters
    ----------
    client: TelegramClient
        Connected Telethon client.
    global_config: dict
        Global configuration for fallback resolution.
    chat_conf: dict
        Per-chat configuration dict (mutated with last_read_message_id).
    """
    chat_id = chat_conf["chat_id"]
    settings = _resolve_monitor_settings(global_config, chat_conf)

    # Resolve chat title once for history display
    if str(chat_id) not in CHAT_TITLES:
        try:
            entity = await client.get_entity(chat_id)
            title = get_display_name(entity)
            if title:
                CHAT_TITLES[str(chat_id)] = title
        except Exception:
            logger.warning("Failed to resolve chat title for %s", chat_id, exc_info=True)
            CHAT_TITLES[str(chat_id)] = ""

    semaphore = asyncio.Semaphore(max(1, settings["max_concurrent_downloads"]))
    download_delay = chat_conf.get(
        "download_delay", global_config.get("download_delay", 20)
    )
    PENDING_IDS.setdefault(chat_id, 0)
    BACKLOG_ITERATED.setdefault(chat_id, 0)
    BACKLOG_DONE.setdefault(chat_id, 0)
    FAILED_IDS.setdefault(chat_id, [])
    DOWNLOADED_IDS.setdefault(chat_id, [])

    @client.on(events.NewMessage(chats=chat_id))
    async def _handler(event):
        message = event.message
        _type = get_media_type(message)
        if not _type or _type not in settings["media_types"]:
            return

        PENDING_IDS[chat_id] = PENDING_IDS.get(chat_id, 0) + 1
        BACKLOG_ITERATED[chat_id] = BACKLOG_ITERATED.get(chat_id, 0) + 1
        try:
            async with semaphore:
                delay = _resolve_download_delay(download_delay)
                if delay is not None:
                    if delay > 0:
                        logger.info(
                            "Waiting %.1fs before next download...",
                            delay,
                        )
                    await asyncio.sleep(delay)
                await download_media(
                    client,
                    message,
                    settings["media_types"],
                    settings["file_formats"],
                    chat_id,
                    settings["download_directory"],
                )
        finally:
            PENDING_IDS[chat_id] = max(0, PENDING_IDS.get(chat_id, 1) - 1)
            BACKLOG_DONE[chat_id] = BACKLOG_DONE.get(chat_id, 0) + 1

        chat_conf["last_read_message_id"] = message.id
        update_config(global_config)

    logger.info("Monitor mode listening for chat_id: %s", chat_id)


async def begin_monitor(config: dict) -> TelegramClient:
    """Create the client, register listeners for each chat, and return
    the connected client (does not block).

    Parameters
    ----------
    config: dict
        Configuration dictionary.

    Returns
    -------
    TelegramClient
        Connected Telethon client with listeners registered.
    """
    client = build_telegram_client(
        api_id=config["api_id"],
        api_hash=config["api_hash"],
    )
    await client.start()

    try:
        chats_config = _get_chats_to_process(config)
    except KeyError:
        await client.disconnect()
        raise

    for chat_conf in chats_config:
        await register_monitor_handler(client, config, chat_conf)

    logger.info(
        "Monitor mode active for %d chat(s). Waiting for new media...",
        len(chats_config),
    )
    return client


async def check_account_premium(config: dict):
    """Connect to Telegram and return account info.

    Parameters
    ----------
    config: dict
        Configuration dictionary with API credentials.

    Returns
    -------
    dict or None
        Dict with keys ``premium`` (bool), ``first_name`` (str),
        ``last_name`` (str), ``username`` (str), ``photo`` (file ref or None).
        Returns ``None`` if unable to connect.
    """
    try:
        api_id = config.get("api_id", "")
        if isinstance(api_id, str) and not api_id.strip().isdigit():
            return None
    except (TypeError, ValueError):
        return None

    async with _VERIFY_LOCK:
        try:
            client = build_telegram_client(
                api_id=config["api_id"],
                api_hash=config["api_hash"],
            )
            await client.start()
            me = await client.get_me()
            await client.disconnect()
            if me is None:
                return None
            return {
                "premium": getattr(me, "premium", False),
                "first_name": getattr(me, "first_name", "") or "",
                "last_name": getattr(me, "last_name", "") or "",
                "username": getattr(me, "username", "") or "",
            }
        except Exception:
            logger.debug("check_account_premium failed (expected when no session)")
            return None


async def resolve_chat_entity(
    api_id: int, api_hash: str, chat_id: int | str
) -> str | None:
    """Resolve a chat ID or username to its display name.

    Parameters
    ----------
    api_id: int
        Telegram API ID.
    api_hash: str
        Telegram API hash.
    chat_id: Union[int, str]
        Numeric chat ID or @username.

    Returns
    -------
    str or None
        The chat title/name if resolved, or None on failure.
    """
    async with _VERIFY_LOCK:
        client = None
        try:
            client = build_telegram_client(api_id=api_id, api_hash=api_hash)
            await client.connect()
            if not await client.is_user_authorized():
                return None
            entity = await client.get_entity(chat_id)
            name = get_display_name(entity) or None
            return name
        except Exception:
            logger.exception("Failed to resolve chat entity %s", chat_id)
            return None
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass


async def get_user_dialogs(
    api_id: int,
    api_hash: str,
    client: TelegramClient | None = None,
) -> list[dict]:
    """Retrieve the user's dialogs (chats, channels, groups, bots).

    Parameters
    ----------
    api_id: int
        Telegram API ID.
    api_hash: str
        Telegram API hash.
    client: TelegramClient or None
        Existing connected client to reuse (e.g. from wizard step 2).
        If not connected, a new temporary client is created.

    Returns
    -------
    list[dict]
        Each dict has keys ``"id"`` (int or str), ``"name"`` (str),
        ``"type"`` (str: ``"channel"``, ``"group"``, ``"bot"``, ``"user"``).
        Returns empty list on failure.
    """
    dialogs = []
    own_client = None
    _client = client
    async with _VERIFY_LOCK:
        try:
            if _client is None or not await _client.is_user_authorized():
                own_client = build_telegram_client(
                    api_id=api_id, api_hash=api_hash
                )
                await own_client.connect()
                if not await own_client.is_user_authorized():
                    return []
                _client = own_client
            async for dialog in _client.iter_dialogs(limit=200):
                entity = dialog.entity
                dial_id = dialog.id
                name = dialog.name or ""
                etype = "user"
                if getattr(entity, "broadcast", False):
                    etype = "channel"
                elif getattr(entity, "megagroup", False):
                    etype = "group"
                elif getattr(entity, "bot", False):
                    etype = "bot"
                elif getattr(entity, "title", None):
                    etype = "group"
                dialogs.append({"id": dial_id, "name": name, "type": etype})
            dialogs.sort(key=lambda d: (d["type"] != "channel", d["name"].lower()))
        except Exception:
            logger.exception("Failed to fetch user dialogs")
            dialogs = []
        finally:
            if own_client is not None:
                try:
                    await own_client.disconnect()
                except Exception:
                    pass
    return dialogs


async def send_auth_code(api_id: int, api_hash: str, phone: str) -> dict:
    """Create a client, connect, and request an SMS verification code.

    Parameters
    ----------
    api_id: int
        Telegram API ID.
    api_hash: str
        Telegram API hash.
    phone: str
        Phone number in international format (e.g. ``+521234567890``).

    Returns
    -------
    dict
        ``{"phone_code_hash": str, "client": TelegramClient}`` on success,
        or ``{"error": str}`` on failure.
    """
    try:
        client = build_telegram_client(api_id=api_id, api_hash=api_hash)
        await client.connect()
        result = await client.send_code_request(phone)
        return {"phone_code_hash": result.phone_code_hash, "client": client}
    except Exception as e:
        logger.exception("send_auth_code failed for phone %s", phone)
        return {"error": str(e)}


async def verify_auth_code(client, phone: str, code: str, phone_code_hash: str) -> bool:
    """Verify SMS code and sign in, creating the ``.session`` file.

    Parameters
    ----------
    client: TelegramClient
        Connected client from ``send_auth_code``.
    phone: str
        Phone number in international format.
    code: str
        Verification code received via SMS/Telegram.
    phone_code_hash: str
        Hash returned by ``send_auth_code``.

    Returns
    -------
    bool
        ``True`` on success, ``False`` on failure.
    """
    try:
        await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        return True
    except Exception:
        logger.warning("verify_auth_code sign-in failed for %s", phone, exc_info=True)
        try:
            await client.disconnect()
        except Exception:
            pass
        return False


async def begin_import(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    config: dict, pagination_limit: int, client_ref: dict | None = None
) -> dict:
    """
    Create telethon client and initiate download.

    Parameters
    ----------
    config: dict
        Dict containing the config to create telethon client.
    pagination_limit: int
        Number of message to download asynchronously as a batch.
    client_ref: dict, optional
        If provided, ``client_ref["client"]`` is set to the connected
        client so an external caller can disconnect it to stop early.

    Returns
    -------
    dict
        Updated configuration to be written into config file.
    """
    client = build_telegram_client(
        api_id=config["api_id"],
        api_hash=config["api_hash"],
    )
    await client.start()
    if client_ref is not None:
        client_ref["client"] = client

    # Extract chats format configuration
    chats_to_process = _get_chats_to_process(config)

    parallel_chats = config.get("parallel_chats", False)
    config_write_lock = asyncio.Lock()

    if parallel_chats:
        logger.info("Processing chats in parallel...")
        global_semaphore = asyncio.Semaphore(max(1, config.get("max_concurrent_downloads", 1)))
        tasks = [
            process_chat(client, config, chat_conf, pagination_limit,
                         config_write_lock, global_semaphore)
            for chat_conf in chats_to_process
        ]
        await asyncio.gather(*tasks)
    else:
        logger.info("Processing chats sequentially...")
        for chat_conf in chats_to_process:
            await process_chat(
                client, config, chat_conf, pagination_limit, config_write_lock
            )

    await client.disconnect()
    return config


def main():  # pylint: disable=too-many-statements  # NOSONAR
    """Main function of the downloader.

    Always runs the full flow: download backlog first, then auto-switch
    to real-time monitoring for new messages.
    """
    config = config_manager.load_config()
    updated_config = config
    try:
        updated_config = asyncio.get_event_loop().run_until_complete(
            begin_import(config, pagination_limit=100)
        )
    except KeyboardInterrupt:
        logger.warning(
            "KeyboardInterrupt received. Gentle exit triggered! "
            "Saving the last read message IDs and exiting..."
        )

        # Accurately calculate the safe resumption point for each chat
        chats_to_process = _get_chats_to_process(updated_config, raise_on_missing=False)

        for chat_conf in chats_to_process:
            chat_id = chat_conf.get("chat_id")
            if chat_id and chat_id in CURRENT_BATCH_IDS:
                batch_ids = CURRENT_BATCH_IDS[chat_id]
                processed = PROCESSED_IDS.get(chat_id, [])
                unprocessed = [m_id for m_id in batch_ids if m_id not in processed]
                if unprocessed:
                    safe_id = min(unprocessed) - 1
                    chat_conf["last_read_message_id"] = max(0, safe_id)
                elif batch_ids:
                    chat_conf["last_read_message_id"] = max(batch_ids)

        update_config(updated_config)
        return

    total_failures = sum(len(set(fail_list)) for fail_list in FAILED_IDS.values())
    if total_failures > 0:
        logger.info(
            "Downloading of %d files failed. "
            "Failed message ids are added to config file.\n"
            "These files will be downloaded on the next run.",
            total_failures,
        )
    update_config(updated_config)
    logger.info("Backlog complete. Switching to Monitor mode...")
    client = asyncio.get_event_loop().run_until_complete(
        begin_monitor(updated_config)
    )
    try:
        client.run_until_disconnected()
    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt received. Stopping monitor mode...")
    update_config(updated_config)


if __name__ == "__main__":
    print_meta(logger)
    main()
