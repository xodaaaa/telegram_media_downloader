"""Unit tests for the SQLite database layer."""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import db


class TestFormatBytes(unittest.TestCase):
    """Tests for format_bytes helper."""

    def test_zero_bytes(self):
        self.assertEqual(db.format_bytes(0), "0 B")

    def test_negative_bytes(self):
        self.assertEqual(db.format_bytes(-1), "0 B")

    def test_bytes(self):
        self.assertEqual(db.format_bytes(500), "500 B")

    def test_kb(self):
        self.assertEqual(db.format_bytes(2048), "2.0 KB")

    def test_mb(self):
        self.assertEqual(db.format_bytes(5 * 1024**2), "5.0 MB")

    def test_gb(self):
        self.assertEqual(db.format_bytes(3 * 1024**3), "3.0 GB")
        self.assertEqual(db.format_bytes(int(1.5 * 1024**3)), "1.5 GB")

    def test_tb(self):
        self.assertEqual(db.format_bytes(2 * 1024**4), "2.0 TB")


class TestGetTotalDownloadedBytes(unittest.TestCase):
    """Tests for get_total_downloaded_bytes."""

    def setUp(self):
        db._db_initialized = False

        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db_file = tempfile.NamedTemporaryFile(
            delete=False, suffix=".sqlite3", dir=self._tmpdir.name
        )
        self._db_file.close()
        self._db_path = self._db_file.name
        self._patcher = __import__("unittest").mock.patch("db.DB_PATH", self._db_path)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        if os.path.exists(self._db_path):
            try:
                os.remove(self._db_path)
            except OSError:
                pass

    def test_empty_db_returns_zero(self):
        self.assertEqual(db.get_total_downloaded_bytes(), 0)

    def test_sums_multiple_records(self):
        db.record_download("c1", 1, "a.jpg", 1024)
        db.record_download("c1", 2, "b.jpg", 2048)
        db.record_download("c2", 3, "c.jpg", 512)
        self.assertEqual(db.get_total_downloaded_bytes(), 3584)


class TestDB(unittest.TestCase):
    """Tests for db.py logic."""

    def setUp(self):
        # Reset initialized flag for fresh testing
        db._db_initialized = False
        # Create a unique temporary file for this test
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.test_db_file = tempfile.NamedTemporaryFile(
            delete=False, suffix=".sqlite3", dir=self._tmpdir.name
        )
        self.test_db_file.close()  # Close it so SQLite can open it
        self.test_db_path = self.test_db_file.name

        # Point db.DB_PATH to our unique file
        self.patcher = mock.patch("db.DB_PATH", self.test_db_path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        # Clean up the unique DB file
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except OSError:
                # On Windows, it might still be locked briefly
                pass

    def test_init_db(self):
        """Verify that init_db creates the table and index."""
        db.init_db()
        self.assertTrue(db._db_initialized)
        self.assertTrue(os.path.exists(db.DB_PATH))

        with db.get_connection() as conn:
            cursor = conn.cursor()
            # Check table
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='download_history'"
            )
            self.assertIsNotNone(cursor.fetchone())
            # Check for media_type column (part of migration logic)
            cursor.execute("PRAGMA table_info(download_history)")
            columns = [c[1] for c in cursor.fetchall()]
            self.assertIn("media_type", columns)
            self.assertIn("file_path", columns)

    def test_record_download(self):
        """Verify that record_download correctly inserts data."""
        db.record_download(
            chat_id="chat123",
            message_id=45,
            file_name="test.jpg",
            file_size=1024,
            file_path="/downloads/test.jpg",
            media_type="photo",
        )

        records, count = db.get_recent_downloads()
        self.assertEqual(count, 1)
        res = records[0]
        self.assertEqual(res["chat_id"], "chat123")
        self.assertEqual(res["message_id"], 45)
        self.assertEqual(res["file_name"], "test.jpg")
        self.assertEqual(res["file_size"], 1024)
        self.assertEqual(res["media_type"], "photo")

    def test_reset_history(self):
        """Verify that reset_history clears the table."""
        db.record_download("c1", 1, "f1.jpg", 100)
        db.record_download("c2", 2, "f2.jpg", 200)

        _, count = db.get_recent_downloads()
        self.assertEqual(count, 2)

        db.reset_history()
        _, count = db.get_recent_downloads()
        self.assertEqual(count, 0)

    def test_get_recent_downloads_filtering(self):
        """Verify search and media type filtering."""
        db.record_download("group_a", 10, "report.pdf", 500, media_type="document")
        db.record_download("group_b", 20, "image.png", 300, media_type="photo")

        # Search by filename
        records, count = db.get_recent_downloads(search_item="report")
        self.assertEqual(count, 1)
        self.assertEqual(records[0]["file_name"], "report.pdf")

        # Search by chat_id
        records, count = db.get_recent_downloads(search_item="group_b")
        self.assertEqual(count, 1)
        self.assertEqual(records[0]["file_name"], "image.png")

        # Filter by media_type
        records, count = db.get_recent_downloads(media_type="photo")
        self.assertEqual(count, 1)
        self.assertEqual(records[0]["media_type"], "photo")

    def test_get_recent_downloads_sorting(self):
        """Verify sorting by different columns."""
        db.record_download("C", 1, "zebra.jpg", 5000)
        db.record_download("A", 2, "apple.jpg", 1000)
        db.record_download("B", 3, "mango.jpg", 3000)

        # Sort by filename ASC
        records, _ = db.get_recent_downloads(sort_by="filename", sort_desc=False)
        self.assertEqual(records[0]["file_name"], "apple.jpg")
        self.assertEqual(records[2]["file_name"], "zebra.jpg")

        # Sort by chat_id DESC
        records, _ = db.get_recent_downloads(sort_by="chat", sort_desc=True)
        self.assertEqual(records[0]["chat_id"], "C")

        # Sort by size ASC
        records, _ = db.get_recent_downloads(sort_by="size", sort_desc=False)
        self.assertEqual(records[0]["file_size"], 1000)

    def test_get_recent_downloads_pagination(self):
        """Verify limit and offset pagination."""
        for i in range(10):
            db.record_download("chat", i, f"file_{i}.jpg", 100)

        records, count = db.get_recent_downloads(limit=3, offset=0)
        self.assertEqual(len(records), 3)
        self.assertEqual(count, 10)

        # Offset 9 should give the last record
        records, _ = db.get_recent_downloads(limit=3, offset=9)
        self.assertEqual(len(records), 1)


if __name__ == "__main__":
    unittest.main()
