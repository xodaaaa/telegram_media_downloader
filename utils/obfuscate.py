"""Obfuscate sensitive configuration values for debug reports."""

import os
import re


def _mask_text(value: str, show_first: int = 0, show_last: int = 0) -> str:
    """Mask a string, keeping only the first/last N characters visible.

    Parameters
    ----------
    value : str
        String to mask.
    show_first : int
        Number of characters to keep at the start.
    show_last : int
        Number of characters to keep at the end.

    Returns
    -------
    str
        Masked string.
    """
    if not value:
        return ""
    length = len(value)
    if show_first + show_last >= length:
        return value
    prefix = value[:show_first] if show_first else ""
    suffix = value[-show_last:] if show_last else ""
    masked_count = length - show_first - show_last
    return f"{prefix}{'*' * min(masked_count, 8)}{suffix}"


def obfuscate_config(config: dict) -> dict:
    """Return a copy of the config with sensitive fields obfuscated.

    Parameters
    ----------
    config : dict
        Original configuration dict.

    Returns
    -------
    dict
        Shallow copy with sensitive values masked.
    """
    safe = dict(config)

    # API id: show last 3 digits
    raw_id = safe.get("api_id")
    if raw_id and str(raw_id).isdigit():
        sid = str(raw_id)
        safe["api_id"] = f"{'*' * max(0, len(sid) - 3)}{sid[-3:]}"
    else:
        safe["api_id"] = "[not set]"

    # API hash: fully hidden
    if safe.get("api_hash"):
        safe["api_hash"] = "[hidden]"

    # Phone: +XX*****XXXX
    phone = safe.get("phone", "")
    if phone:
        # Keep country code + first digit, mask middle
        safe["phone"] = _mask_text(phone, show_first=4, show_last=4)

    # Session path: just the filename
    safe["_session"] = (
        "media_downloader.session (present)"
        if os.path.exists(
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "media_downloader.session",
            )
        )
        else "media_downloader.session (absent)"
    )

    # Download directory: show only last 2 segments
    dl_dir = safe.get("download_directory", "")
    if dl_dir:
        parts = dl_dir.replace("\\", "/").rstrip("/").split("/")
        safe["download_directory"] = ".../" + "/".join(parts[-2:])

    # Chat IDs and titles: mask
    if "chat_id" in safe:
        cid = str(safe["chat_id"])
        safe["chat_id"] = _mask_text(cid, show_first=0, show_last=4)

    chats = safe.get("chats", [])
    if chats:
        obf_chats = []
        for c in chats:
            cc = dict(c)
            cid = str(cc.get("chat_id", ""))
            cc["chat_id"] = _mask_text(cid, show_first=0, show_last=4)
            # Remove per-chat directory paths if present
            if "download_directory" in cc:
                parts = (
                    str(cc["download_directory"])
                    .replace("\\", "/")
                    .rstrip("/")
                    .split("/")
                )
                cc["download_directory"] = ".../" + "/".join(parts[-2:])
            # Remove ids_to_retry (too verbose)
            if "ids_to_retry" in cc:
                cc["ids_to_retry"] = f"[{len(cc['ids_to_retry'])} items]"
            obf_chats.append(cc)
        safe["chats"] = obf_chats

    # Remove internal wizard flag
    safe.pop("_wizard_completed", None)

    return safe


def obfuscate_chat_name(name: str) -> str:
    """Obfuscate a chat title, keeping first 3 and last 3 characters.

    Parameters
    ----------
    name : str
        Chat title or name.

    Returns
    -------
    str
        Masked chat name.
    """
    return _mask_text(name, show_first=3, show_last=3)
