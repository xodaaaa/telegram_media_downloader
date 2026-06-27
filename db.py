"""Database helper for tracking download history."""

import logging
import os
import sqlite3
from typing import Optional

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(THIS_DIR, "downloads.sqlite3")
_db_initialized = False


def get_connection():
    """Get a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Initialize the SQLite database and create the history table if it doesn't exist."""
    global _db_initialized
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS download_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    file_name TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    download_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    file_path TEXT
                )
            """
            )
            # Migration check: add file_path if it doesn't exist
            cursor.execute("PRAGMA table_info(download_history)")
            columns = [c[1] for c in cursor.fetchall()]
            if "file_path" not in columns:
                cursor.execute("ALTER TABLE download_history ADD COLUMN file_path TEXT")
            if "media_type" not in columns:
                cursor.execute(
                    "ALTER TABLE download_history ADD COLUMN media_type TEXT"
                )
            if "chat_title" not in columns:
                cursor.execute(
                    "ALTER TABLE download_history ADD COLUMN chat_title TEXT"
                )

            # Create an index for faster queries on recent downloads
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_timestamp ON download_history(download_timestamp DESC)"
            )
            conn.commit()
        _db_initialized = True
    except Exception:
        logger = logging.getLogger("media_downloader")
        logger.exception("Failed to initialize database")


def _ensure_db():
    """Lazily initialize the database on first use."""
    if not _db_initialized:
        init_db()


def record_download(
    chat_id: str,
    message_id: int,
    file_name: str,
    file_size: int,
    file_path: Optional[str] = None,
    media_type: Optional[str] = None,
    chat_title: Optional[str] = None,
):
    """Record a successful download in the history table."""
    _ensure_db()
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO download_history (chat_id, message_id, file_name, file_size, file_path, media_type, chat_title)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    str(chat_id),
                    message_id,
                    file_name,
                    file_size,
                    file_path,
                    media_type,
                    chat_title,
                ),
            )
            conn.commit()
    except Exception:
        logger = logging.getLogger("media_downloader")
        logger.exception("Failed to record download history for %s", file_name)


def format_bytes(n: int) -> str:
    """Format a byte count into a human-readable string.

    Parameters
    ----------
    n: int
        Number of bytes.

    Returns
    -------
    str
        Human-readable size string (e.g. "142.7 GB", "12.4 MB").
    """
    if n <= 0:
        return "0 B"
    if n >= 1024**4:
        return f"{n / (1024**4):.1f} TB"
    if n >= 1024**3:
        return f"{n / (1024**3):.1f} GB"
    if n >= 1024**2:
        return f"{n / 1024**2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def get_total_downloaded_bytes() -> int:
    """Sum file_size of all download history entries.

    Returns
    -------
    int
        Total bytes downloaded across all history.
    """
    _ensure_db()
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COALESCE(SUM(file_size), 0) FROM download_history")
            row = cursor.fetchone()
            return int(row[0]) if row else 0
    except Exception:
        logger = logging.getLogger("media_downloader")
        logger.exception("Failed to compute total downloaded size")
        return 0


def get_download_counts() -> dict:
    """Count downloaded files by media type.

    Returns
    -------
    dict
        Keys ``"video"`` and ``"photo"`` with integer counts.
    """
    _ensure_db()
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT media_type, COUNT(*) FROM download_history "
                "WHERE media_type IS NOT NULL GROUP BY media_type"
            )
            result = {"video": 0, "photo": 0}
            for row in cursor.fetchall():
                if row[0] in result:
                    result[row[0]] = row[1]
            return result
    except Exception:
        logger = logging.getLogger("media_downloader")
        logger.exception("Failed to count downloads by media type")
        return {"video": 0, "photo": 0}


def reset_history():
    """Clear all download history."""
    _ensure_db()
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM download_history")
            conn.commit()
    except Exception:
        logger = logging.getLogger("media_downloader")
        logger.exception("Failed to reset download history")


def get_recent_downloads(
    limit: int = 100,
    offset: int = 0,
    search_item: str = "",
    media_type: str = "All",
    sort_by: str = "download_timestamp",
    sort_desc: bool = True,
):
    """Retrieve the most recent downloads with optional search and sorting."""
    _ensure_db()
    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Map valid sort columns to prevent SQL injection
            valid_sort_cols = {
                "timestamp": "download_timestamp",
                "chat": "COALESCE(chat_title, chat_id)",
                "filename": "file_name",
                "size": "file_size",
                "media_type": "media_type",
                # Also fallbacks for safety:
                "download_timestamp": "download_timestamp",
                "chat_id": "chat_id",
                "file_name": "file_name",
                "size_mb": "file_size",
                "chat_display": "COALESCE(chat_title, chat_id)",
            }
            order_col = valid_sort_cols.get(sort_by, "download_timestamp")
            order_dir = "DESC" if sort_desc else "ASC"

            query = """
                SELECT id, chat_id, message_id, file_name, file_size, download_timestamp, file_path, media_type, chat_title
                FROM download_history
                WHERE 1=1
            """
            count_query = "SELECT COUNT(*) FROM download_history WHERE 1=1"

            params = []

            if search_item:
                search_clause = " AND (file_name LIKE ? OR chat_id LIKE ?)"
                query += search_clause
                count_query += search_clause
                params.extend([f"%{search_item}%", f"%{search_item}%"])

            if media_type and media_type != "All":
                type_clause = " AND media_type = ?"
                query += type_clause
                count_query += type_clause
                params.append(media_type)

            query += f" ORDER BY {order_col} {order_dir} LIMIT ? OFFSET ?"

            # Execute main query
            cursor.execute(query, (*params, limit, offset))
            records = [dict(row) for row in cursor.fetchall()]

            # Execute count query
            cursor.execute(count_query, params)
            total_count = cursor.fetchone()[0]

            return records, total_count
    except Exception:
        logger = logging.getLogger("media_downloader")
        logger.exception("Failed to fetch download history")
        return [], 0
