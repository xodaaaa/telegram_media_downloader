"""Pytest configuration — prevents tests from touching the real database."""
import os
import tempfile

import db

# Redirect the database to a temporary file so tests never touch
# the real downloads.sqlite3 in the project root.
try:
    _tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
except TypeError:
    _tmp_dir = tempfile.TemporaryDirectory()
_tmp_db = tempfile.NamedTemporaryFile(
    suffix=".sqlite3", delete=False, dir=_tmp_dir.name
)
_tmp_db.close()
db.DB_PATH = _tmp_db.name


def pytest_sessionfinish(session, exitstatus):
    """Clean up the temporary database after the test session."""
    try:
        os.unlink(_tmp_db.name)
    except OSError:
        pass
