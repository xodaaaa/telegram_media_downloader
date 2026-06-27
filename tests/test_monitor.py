"""Unit tests for monitor mode in media_downloader."""

import asyncio
import os
import unittest
from unittest import mock

from media_downloader import (
    VALID_MODES,
    _resolve_monitor_settings,
    begin_monitor,
    main,
    register_monitor_handler,
)


class MockClient:
    def __init__(self, *args, **kwargs):
        self._handlers = []

    async def start(self):
        """Mock client.start(); no-op for tests."""

    async def disconnect(self):
        """Mock client.disconnect(); no-op for tests."""

    def on(self, event):
        def wrapper(handler):
            self._handlers.append((event, handler))
            return handler

        return wrapper

    def run_until_disconnected(self):
        """Mock blocking wait; no-op for tests."""


class MockEvent:
    def __init__(self, message):
        self.message = message


def _make_client_mock():
    """Return a mock Telethon client that can be used as async context."""
    m = mock.AsyncMock()
    m.start = mock.AsyncMock()
    m.disconnect = mock.AsyncMock()
    return m


class TestResolveMonitorSettings(unittest.TestCase):
    """Tests for _resolve_monitor_settings."""

    def test_fallback_chat_to_global(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            abs_dir = os.path.join(tmp, "global", "dl")
            global_config = {
                "media_types": ["photo"],
                "file_formats": {"photo": ["jpg"]},
                "max_concurrent_downloads": 8,
                "download_directory": abs_dir,
            }
            chat_conf = {}
            result = _resolve_monitor_settings(global_config, chat_conf)
            self.assertEqual(result["media_types"], ["photo"])
            self.assertEqual(result["file_formats"], {"photo": ["jpg"]})
            self.assertEqual(result["max_concurrent_downloads"], 8)
            self.assertEqual(result["download_directory"], abs_dir)

    def test_chat_overrides_global(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            abs_global = os.path.join(tmp, "global", "dl")
            abs_chat = os.path.join(tmp, "chat", "dl")
            global_config = {
                "media_types": ["photo"],
                "file_formats": {"photo": ["jpg"]},
                "max_concurrent_downloads": 8,
                "download_directory": abs_global,
            }
            chat_conf = {
                "media_types": ["video"],
                "file_formats": {"video": ["mp4"]},
                "max_concurrent_downloads": 2,
                "download_directory": abs_chat,
            }
            result = _resolve_monitor_settings(global_config, chat_conf)
            self.assertEqual(result["media_types"], ["video"])
            self.assertEqual(result["file_formats"], {"video": ["mp4"]})
            self.assertEqual(result["max_concurrent_downloads"], 2)
            self.assertEqual(result["download_directory"], abs_chat)

    def test_empty_configs(self):
        result = _resolve_monitor_settings({}, {})
        self.assertEqual(result["media_types"], [])
        self.assertEqual(result["file_formats"], {})
        self.assertEqual(result["max_concurrent_downloads"], 1)
        self.assertIsNone(result["download_directory"])

    def test_invalid_max_concurrent_downloads(self):
        for bad_val in [None, "auto", 0, -1]:
            with self.subTest(bad_val=bad_val):
                chat_conf = {"max_concurrent_downloads": bad_val}
                result = _resolve_monitor_settings({}, chat_conf)
                self.assertEqual(result["max_concurrent_downloads"], 1)

    def test_download_directory_relative(self):
        result = _resolve_monitor_settings({}, {"download_directory": "rel_dl"})
        self.assertEqual(result["download_directory"], os.path.abspath("rel_dl"))

    def test_download_directory_empty_string(self):
        result = _resolve_monitor_settings({}, {"download_directory": ""})
        self.assertIsNone(result["download_directory"])

    def test_download_directory_whitespace_only(self):
        result = _resolve_monitor_settings({}, {"download_directory": "   "})
        self.assertIsNone(result["download_directory"])


class TestRegisterMonitorHandler(unittest.TestCase):
    """Tests for register_monitor_handler."""

    def setUp(self):
        import media_downloader

        media_downloader.PENDING_IDS.clear()
        media_downloader.FAILED_IDS.clear()
        media_downloader.DOWNLOADED_IDS.clear()
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    def tearDown(self):
        self._loop.close()

    def test_handler_calls_download_media(self):
        loop = self._loop
        client = MockClient()
        chat_conf = {"chat_id": 999, "media_types": ["photo"]}

        with mock.patch(
            "media_downloader.download_media", new_callable=mock.AsyncMock
        ) as mock_dl, mock.patch(
            "media_downloader.get_media_type", return_value="photo"
        ):
            loop.run_until_complete(register_monitor_handler(client, {}, chat_conf))
            self.assertEqual(len(client._handlers), 1)
            handler = client._handlers[0][1]
            msg = mock.Mock(id=100)
            msg.media = mock.Mock()
            loop.run_until_complete(handler(MockEvent(msg)))
            mock_dl.assert_called_once()

    def test_handler_skips_wrong_type(self):
        loop = self._loop
        client = MockClient()
        chat_conf = {"chat_id": 999, "media_types": ["video"]}

        with mock.patch(
            "media_downloader.download_media", new_callable=mock.AsyncMock
        ) as mock_dl, mock.patch(
            "media_downloader.get_media_type", return_value="photo"
        ):
            loop.run_until_complete(register_monitor_handler(client, {}, chat_conf))
            handler = client._handlers[0][1]
            msg = mock.Mock(id=101)
            msg.media = mock.Mock()
            loop.run_until_complete(handler(MockEvent(msg)))
            mock_dl.assert_not_called()

    def test_updates_last_read_message_id(self):
        loop = self._loop
        client = MockClient()
        chat_conf = {"chat_id": 777, "media_types": ["photo"]}

        with mock.patch(
            "media_downloader.download_media", new_callable=mock.AsyncMock
        ), mock.patch("media_downloader.get_media_type", return_value="photo"):
            loop.run_until_complete(register_monitor_handler(client, {}, chat_conf))
            handler = client._handlers[0][1]
            msg = mock.Mock(id=505)
            msg.media = mock.Mock()
            loop.run_until_complete(handler(MockEvent(msg)))
            self.assertEqual(chat_conf["last_read_message_id"], 505)


class TestBeginMonitor(unittest.TestCase):
    """Tests for begin_monitor."""

    def setUp(self):
        import media_downloader

        media_downloader.PENDING_IDS.clear()
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    def tearDown(self):
        self._loop.close()

    def test_missing_chat_id_raises(self):
        loop = self._loop
        conf = {"api_id": 1, "api_hash": "h"}
        with mock.patch("media_downloader.TelegramClient") as mock_tc:
            mock_tc.return_value = _make_client_mock()
            with self.assertRaises(KeyError):
                loop.run_until_complete(begin_monitor(conf))

    def test_registers_handler_per_chat(self):
        loop = self._loop
        conf = {
            "api_id": 1,
            "api_hash": "h",
            "chats": [{"chat_id": 1}, {"chat_id": 2}],
        }
        with mock.patch(
            "media_downloader.register_monitor_handler",
            new_callable=mock.AsyncMock,
        ) as mock_reg:
            with mock.patch("media_downloader.TelegramClient") as mock_tc:
                mock_tc.return_value = _make_client_mock()
                loop.run_until_complete(begin_monitor(conf))
                self.assertEqual(mock_reg.call_count, 2)

    def test_legacy_single_chat(self):
        loop = self._loop
        conf = {"api_id": 1, "api_hash": "h", "chat_id": 123}
        with mock.patch(
            "media_downloader.register_monitor_handler",
            new_callable=mock.AsyncMock,
        ) as mock_reg:
            with mock.patch("media_downloader.TelegramClient") as mock_tc:
                mock_tc.return_value = _make_client_mock()
                loop.run_until_complete(begin_monitor(conf))
                self.assertEqual(mock_reg.call_count, 1)

    def test_proxy_config(self):
        loop = self._loop
        conf = {
            "api_id": 1,
            "api_hash": "h",
            "chat_id": 123,
            "proxy": {
                "scheme": "socks5",
                "hostname": "127.0.0.1",
                "port": 1080,
            },
        }
        with mock.patch("media_downloader.TelegramClient") as mock_tc:
            mock_tc.return_value = _make_client_mock()
            with mock.patch(
                "media_downloader.register_monitor_handler",
                new_callable=mock.AsyncMock,
            ):
                loop.run_until_complete(begin_monitor(conf))
                call_kwargs = mock_tc.call_args[1]
                self.assertIsNotNone(call_kwargs.get("proxy"))


class TestMainMonitorMode(unittest.TestCase):
    """Tests for main() with monitor-related modes."""

    def setUp(self):
        import media_downloader

        media_downloader.PENDING_IDS.clear()
        media_downloader.FAILED_IDS.clear()
        media_downloader.DOWNLOADED_IDS.clear()
        media_downloader.PROCESSED_IDS.clear()
        media_downloader.CURRENT_BATCH_IDS.clear()
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    def tearDown(self):
        self._loop.close()

    def _setup_mocks(self, config_override=None):
        patches = []

        p_load = mock.patch("media_downloader.config_manager.load_config")
        mock_load = p_load.start()
        patches.append(p_load)
        mock_load.return_value = config_override or {
            "api_id": 1,
            "api_hash": "h",
            "chat_id": 123,
            "mode": "history",
        }

        p_bi = mock.patch("media_downloader.begin_import")
        mock_bi = p_bi.start()
        patches.append(p_bi)

        p_bm = mock.patch("media_downloader.begin_monitor")
        mock_bm = p_bm.start()
        patches.append(p_bm)

        mock_client = mock.Mock()
        mock_client.run_until_disconnected = mock.Mock()
        mock_bm.return_value = mock_client

        p_upd = mock.patch("media_downloader.update_config")
        mock_upd = p_upd.start()
        patches.append(p_upd)

        p_cfu = mock.patch("media_downloader.check_for_updates")
        p_cfu.start()
        patches.append(p_cfu)

        return mock_bi, mock_bm, mock_upd, mock_client, patches

    @staticmethod
    def _teardown_mocks(patches):
        for p in patches:
            p.stop()

    def test_mode_monitor(self):
        mock_bi, mock_bm, mock_upd, _, patches = self._setup_mocks(
            {
                "api_id": 1,
                "api_hash": "h",
                "chat_id": 123,
                "mode": "monitor",
            }
        )
        try:
            main()
            mock_bm.assert_called_once()
            mock_bi.assert_not_called()
            mock_upd.assert_called()
        finally:
            self._teardown_mocks(patches)

    def test_mode_history_monitor(self):
        mock_bi, mock_bm, mock_upd, _, patches = self._setup_mocks(
            {
                "api_id": 1,
                "api_hash": "h",
                "chat_id": 123,
                "mode": "history_monitor",
            }
        )
        try:
            main()
            mock_bi.assert_called_once()
            mock_bm.assert_called_once()
            self.assertGreaterEqual(mock_upd.call_count, 2)
        finally:
            self._teardown_mocks(patches)

    def test_mode_history_monitor_keyboard_interrupt_backlog(self):
        mock_bi, mock_bm, mock_upd, _, patches = self._setup_mocks(
            {
                "api_id": 1,
                "api_hash": "h",
                "chat_id": 123,
                "mode": "history_monitor",
            }
        )
        mock_bi.side_effect = KeyboardInterrupt()
        try:
            main()
            mock_bi.assert_called_once()
            mock_bm.assert_not_called()
            mock_upd.assert_called_once()
        finally:
            self._teardown_mocks(patches)

    def test_mode_monitor_keyboard_interrupt(self):
        _, mock_bm, mock_upd, mock_client, patches = self._setup_mocks(
            {
                "api_id": 1,
                "api_hash": "h",
                "chat_id": 123,
                "mode": "monitor",
            }
        )
        mock_client.run_until_disconnected.side_effect = KeyboardInterrupt()
        try:
            main()
            mock_bm.assert_called_once()
            mock_upd.assert_called()
        finally:
            self._teardown_mocks(patches)

    def test_mode_history_default_no_mode_in_config(self):
        mock_bi, mock_bm, _, _, patches = self._setup_mocks(
            {"api_id": 1, "api_hash": "h", "chat_id": 123}
        )
        try:
            main()
            mock_bi.assert_called_once()
            mock_bm.assert_not_called()
        finally:
            self._teardown_mocks(patches)

    def test_mode_unknown_falls_back(self):
        mock_bi, mock_bm, _, _, patches = self._setup_mocks(
            {
                "api_id": 1,
                "api_hash": "h",
                "chat_id": 123,
                "mode": "invalid_mode",
            }
        )
        try:
            main()
            mock_bi.assert_called_once()
            mock_bm.assert_not_called()
        finally:
            self._teardown_mocks(patches)


class TestValidModes(unittest.TestCase):
    """Trivial test to ensure VALID_MODES has expected values."""

    def test_valid_modes(self):
        self.assertIn("history", VALID_MODES)
        self.assertIn("monitor", VALID_MODES)
        self.assertIn("history_monitor", VALID_MODES)
        self.assertEqual(len(VALID_MODES), 3)


if __name__ == "__main__":
    unittest.main()
