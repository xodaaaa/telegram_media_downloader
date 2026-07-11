"""Centralized TelegramClient factory."""

import os
import sqlite3
from typing import Optional

from telethon import TelegramClient
from telethon.sessions.sqlite import SQLiteSession

from utils.meta import APP_VERSION, DEVICE_MODEL, LANG_CODE, SYSTEM_VERSION


class _WALSession(SQLiteSession):
    """SQLiteSession subclass that enables WAL mode + busy_timeout.

    WAL (Write-Ahead Logging) allows concurrent reads while a write
    is in progress, and busy_timeout makes SQLite retry for up to
    30 seconds before raising \"database is locked\".

    This prevents ``sqlite3.OperationalError: database is locked``
    when multiple downloads reconnect concurrently after a connection
    drop (all trying to write to the same session file).
    """

    def _assert_connection(self):
        cursor = super()._assert_connection()
        if self._conn is not None:
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA busy_timeout=30000")
            except Exception:
                pass
        return cursor


def build_telegram_client(
    api_id: int,
    api_hash: str,
    *,
    session_name: str = "media_downloader",
) -> TelegramClient:
    """Build a TelegramClient with the standard device metadata.

    Uses a custom SQLite session that enables WAL mode and a 30-second
    busy timeout to prevent \"database is locked\" errors during
    concurrent reconnection after connection drops.

    Parameters
    ----------
    api_id: int
        Telegram API ID.
    api_hash: str
        Telegram API hash.
    session_name: str
        Session file name (default: ``"media_downloader"``).

    Returns
    -------
    TelegramClient
        A Telethon client instance (not yet connected).
    """
    session = _WALSession(session_name)
    return TelegramClient(
        session,
        api_id=api_id,
        api_hash=api_hash,
        device_model=DEVICE_MODEL,
        system_version=SYSTEM_VERSION,
        app_version=APP_VERSION,
        lang_code=LANG_CODE,
        entity_cache_limit=50000,
        connection_retries=None,  # infinite reconnection attempts
        retry_delay=2,            # wait 2s between reconnection retries
        timeout=30,               # connection timeout (seconds)
        auto_reconnect=True,      # reconnect automatically on disconnect
        flood_sleep_threshold=86400,  # auto-sleep floods up to 24h
    )
