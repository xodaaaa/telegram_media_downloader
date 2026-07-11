"""Checkpoint database for resumable downloads using SQLite + WAL.

Tracks per-file download progress so interrupted transfers can resume
from the last saved byte offset — regardless of the interruption cause
(network drop, app crash, manual stop, FloodWait, etc.).

Schema
------
download_checkpoints
├── chat_id TEXT          — chat/channel identifier
├── message_id INTEGER    — message identifier
├── file_path TEXT        — absolute path to the file being downloaded
├── file_size INTEGER     — total file size in bytes
├── downloaded_bytes INTEGER — bytes successfully written so far
├── state TEXT            — DOWNLOADING | COMPLETED | FAILED
├── created_at REAL       — creation timestamp
├── updated_at REAL       — last update timestamp
├── expires_at REAL       — auto-cleanup threshold (7 days)
└── PRIMARY KEY (chat_id, message_id)
"""

import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("media_downloader")

THIS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKPOINT_DB_PATH = os.path.join(THIS_DIR, "checkpoints.sqlite3")


@dataclass
class DownloadCheckpoint:
    """A single download progress record."""
    chat_id: str
    message_id: int
    file_path: str
    file_size: int
    downloaded_bytes: int
    state: str  # DOWNLOADING | COMPLETED | FAILED
    created_at: float = 0.0
    updated_at: float = 0.0


class CheckpointDB:
    """SQLite-backed checkpoint store with WAL mode for concurrent safety."""

    def __init__(self, db_path: str = CHECKPOINT_DB_PATH):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._initialized = False

    def init(self):
        """Create the database and table if they don't exist."""
        if self._initialized:
            return
        self.conn = sqlite3.connect(self.db_path, timeout=10)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-8000")  # ~8 MB cache
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS download_checkpoints (
                chat_id          TEXT NOT NULL,
                message_id       INTEGER NOT NULL,
                file_path        TEXT NOT NULL,
                file_size        INTEGER NOT NULL,
                downloaded_bytes INTEGER DEFAULT 0,
                state            TEXT DEFAULT 'DOWNLOADING',
                created_at       REAL,
                updated_at       REAL,
                expires_at       REAL,
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cp_expires
            ON download_checkpoints(expires_at)
        """)
        self.conn.commit()
        self._initialized = True
        logger.debug("Checkpoint DB initialised at %s", self.db_path)

    def close(self):
        """Close the database connection gracefully."""
        if self.conn is not None:
            try:
                self.conn.execute("PRAGMA optimize")
            except Exception:
                pass
            try:
                self.conn.close()
            except Exception as exc:
                logger.warning("Checkpoint DB close error: %s", exc)
            self.conn = None
            self._initialized = False

    def save(self, chat_id: str, message_id: int, file_path: str,
             file_size: int, downloaded_bytes: int,
             state: str = "DOWNLOADING") -> bool:
        """Insert or update a checkpoint record."""
        try:
            now = time.time()
            expires_at = now + 7 * 24 * 60 * 60  # 7 days
            # Fetch existing created_at so it's preserved on update
            existing = self.conn.execute(
                "SELECT created_at FROM download_checkpoints "
                "WHERE chat_id=? AND message_id=?",
                (chat_id, message_id),
            ).fetchone()
            created_at = existing[0] if existing else now
            self.conn.execute("""
                INSERT OR REPLACE INTO download_checkpoints
                    (chat_id, message_id, file_path, file_size,
                     downloaded_bytes, state, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                chat_id, message_id, file_path, file_size,
                downloaded_bytes, state, created_at, now, expires_at,
            ))
            self.conn.commit()
            return True
        except Exception as exc:
            logger.error("Failed to save checkpoint: %s", exc)
            return False

    def load(self, chat_id: str, message_id: int) -> Optional[DownloadCheckpoint]:
        """Load a checkpoint for (chat_id, message_id) if it exists and is active."""
        try:
            cursor = self.conn.execute("""
                SELECT chat_id, message_id, file_path, file_size,
                       downloaded_bytes, state, created_at, updated_at
                FROM download_checkpoints
                WHERE chat_id=? AND message_id=? AND state='DOWNLOADING'
            """, (chat_id, message_id))
            row = cursor.fetchone()
            if row is None:
                return None
            return DownloadCheckpoint(
                chat_id=str(row[0]), message_id=int(row[1]),
                file_path=str(row[2]), file_size=int(row[3]),
                downloaded_bytes=int(row[4]), state=str(row[5]),
                created_at=float(row[6] or 0), updated_at=float(row[7] or 0),
            )
        except Exception as exc:
            logger.error("Failed to load checkpoint: %s", exc)
            return None

    def delete(self, chat_id: str, message_id: int) -> bool:
        """Remove a checkpoint (called on successful completion)."""
        try:
            self.conn.execute("""
                DELETE FROM download_checkpoints
                WHERE chat_id=? AND message_id=?
            """, (chat_id, message_id))
            self.conn.commit()
            return True
        except Exception as exc:
            logger.error("Failed to delete checkpoint: %s", exc)
            return False

    def cleanup_expired(self) -> int:
        """Remove checkpoints older than 7 days. Returns count removed."""
        try:
            now = time.time()
            cursor = self.conn.execute(
                "DELETE FROM download_checkpoints WHERE expires_at <= ?",
                (now,),
            )
            self.conn.commit()
            removed = cursor.rowcount
            if removed:
                logger.info("Cleaned up %d expired checkpoint(s)", removed)
            return removed
        except Exception as exc:
            logger.error("Checkpoint cleanup error: %s", exc)
            return 0


# Module-level singleton — initialised once on first use
checkpoint_db = CheckpointDB()
