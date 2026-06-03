import unittest
from unittest.mock import patch, MagicMock, mock_open
import os
import sys
import httpx

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from modules.utils import (
    log_debug,
    normalize_path,
    get_latest_episode_local,
    is_episode_already_present,
    detect_lang_from_files,
    send_windows_notification,
    ensure_working_mirror,
    ensure_working_kwik_mirror,
    ensure_working_jikan_mirror
)

class TestUtils(unittest.TestCase):
    
    @patch("builtins.open", new_callable=mock_open, read_data="previous log")
    @patch("os.path.exists", return_value=True)
    def test_log_debug_existing_file(self, mock_exists, mock_file):
        config.LOG_PATH = "dummy_log.txt"
        log_debug("test message")
        mock_file.assert_any_call("dummy_log.txt", "r", encoding="utf-8")
        mock_file.assert_any_call("dummy_log.txt", "w", encoding="utf-8")

    @patch("builtins.open", new_callable=mock_open)
    @patch("os.path.exists", return_value=False)
    def test_log_debug_new_file(self, mock_exists, mock_file):
        config.LOG_PATH = "dummy_log.txt"
        log_debug("test message")
        mock_file.assert_called_once_with("dummy_log.txt", "w", encoding="utf-8")

    @patch("builtins.open", side_effect=Exception("I/O error"))
    def test_log_debug_exception(self, mock_file):
        config.LOG_PATH = "dummy_log.txt"
        # Should not raise an exception
        log_debug("test message")

    def test_normalize_path(self):
        self.assertEqual(normalize_path("A：B:C"), os.path.normpath("a b c"))
        self.assertEqual(normalize_path(""), "")
        self.assertEqual(normalize_path(None), "")

    @patch("os.path.exists", return_value=False)
    def test_get_latest_episode_local_not_exists(self, mock_exists):
        self.assertEqual(get_latest_episode_local("dummy_folder"), -1)

    @patch("os.path.exists", return_value=True)
    @patch("os.walk")
    def test_get_latest_episode_local_exists(self, mock_walk, mock_exists):
        mock_walk.return_value = [
            ("root", [], ["AnimePahe_Title_-_05_720p.mp4", "Episode 12.mkv", "random.txt"])
        ]
        self.assertEqual(get_latest_episode_local("dummy_folder"), 12)

    @patch("os.path.exists", return_value=True)
    @patch("os.walk")
    @patch("builtins.int", side_effect=ValueError("invalid"))
    def test_get_latest_episode_local_value_error(self, mock_int, mock_walk, mock_exists):
        mock_walk.return_value = [
            ("root", [], ["Episode 12.mp4"])
        ]
        self.assertEqual(get_latest_episode_local("dummy_folder"), -1)

    @patch("os.walk")
    def test_is_episode_already_present(self, mock_walk):
        mock_walk.return_value = [
            ("root", [], ["Anime_-_01_720p.mp4", "Anime_-_02_720p.mp4", "somefile.txt"])
        ]
        self.assertTrue(is_episode_already_present("dummy", 1, "Title"))
        self.assertTrue(is_episode_already_present("dummy", 2, "Title"))
        self.assertFalse(is_episode_already_present("dummy", 3, "Title"))

    @patch("os.walk")
    def test_detect_lang_from_files(self, mock_walk):
        # 1. English indicators
        mock_walk.return_value = [("root", [], ["file_eng_dub.mp4"])]
        self.assertEqual(detect_lang_from_files("dummy"), "en")

        # 2. Japanese indicators
        mock_walk.return_value = [("root", [], ["file_subsplease.mp4"])]
        self.assertEqual(detect_lang_from_files("dummy"), "jap")

        # 3. None / other extension
        mock_walk.return_value = [("root", [], ["file_eng_dub.txt"])]
        self.assertIsNone(detect_lang_from_files("dummy"))

    @patch("os.name", "posix")
    def test_send_windows_notification_non_windows(self):
        # Should return immediately on non-windows
        self.assertIsNone(send_windows_notification("title", "msg"))

    @patch("os.name", "nt")
    @patch("subprocess.Popen")
    def test_send_windows_notification_windows(self, mock_popen):
        config.ENABLE_NOTIFICATIONS = True
        send_windows_notification("title", "msg", "folder")
        mock_popen.assert_called_once()

    @patch("os.name", "nt")
    @patch("subprocess.Popen", side_effect=Exception("Failed to run"))
    def test_send_windows_notification_error(self, mock_popen):
        config.ENABLE_NOTIFICATIONS = True
        # Should not raise exception
        send_windows_notification("title", "msg")

    @patch("os.name", "nt")
    def test_send_windows_notification_disabled(self):
        config.ENABLE_NOTIFICATIONS = False
        # Should return immediately without subprocess call
        with patch("subprocess.Popen") as mock_popen:
            send_windows_notification("title", "msg")
            mock_popen.assert_not_called()

    @patch("modules.utils._ensure_working_site_mirror")
    def test_ensure_working_mirror_alias(self, mock_ensure):
        ensure_working_mirror(None, True)
        mock_ensure.assert_called_once_with(None, "animepahe", True)

    @patch("modules.utils._ensure_working_site_mirror")
    def test_ensure_working_kwik_mirror_alias(self, mock_ensure):
        ensure_working_kwik_mirror(None, True)
        mock_ensure.assert_called_once_with(None, "kwik", True)

    @patch("modules.utils._ensure_working_site_mirror")
    def test_ensure_working_jikan_mirror_alias(self, mock_ensure):
        ensure_working_jikan_mirror(None, True)
        mock_ensure.assert_called_once_with(None, "jikan", True)

    @patch("modules.db.get_last_working_mirror", return_value="https://animepahe.com")
    @patch("modules.db.save_working_mirror")
    def test_ensure_working_site_mirror_success_httpx(self, mock_save, mock_get_last):
        mock_client = MagicMock()
        mock_res = MagicMock()
        mock_res.status_code = 200
        # Mock httpx style URL object with a different final_url (redirect)
        mock_url = MagicMock()
        mock_url.scheme = "https"
        mock_url.host = "animepahe.org"
        mock_url.path = "/"
        mock_res.url = mock_url
        mock_client.get.return_value = mock_res

        config.ANIMEPAHE_URL = "https://animepahe.si"
        # Test animepahe site type (also hits redirects log)
        success = ensure_working_mirror(mock_client, verbose=True)
        self.assertTrue(success)
        mock_save.assert_called_with("animepahe", "https://animepahe.si")

    @patch("modules.db.get_last_working_mirror", return_value=None)
    @patch("modules.db.save_working_mirror")
    def test_ensure_working_site_mirror_success_requests(self, mock_save, mock_get_last):
        mock_client = MagicMock()
        mock_res = MagicMock()
        mock_res.status_code = 200
        # Mock requests style string URL
        del mock_res.url.scheme
        mock_res.url = "https://kwik.cx/abc"
        mock_client.get.return_value = mock_res

        # Test kwik site type
        success = ensure_working_kwik_mirror(mock_client, verbose=False)
        self.assertTrue(success)
        mock_save.assert_called_with("kwik", "https://kwik.si")

    @patch("modules.db.get_last_working_mirror", return_value=None)
    @patch("modules.db.save_working_mirror")
    def test_ensure_working_site_mirror_jikan_success(self, mock_save, mock_get_last):
        mock_client = MagicMock()
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_url = MagicMock()
        mock_url.scheme = "https"
        mock_url.host = "api.jikan.moe"
        mock_url.path = "/v4"
        mock_res.url = mock_url
        mock_client.get.return_value = mock_res

        success = ensure_working_jikan_mirror(mock_client, verbose=True)
        self.assertTrue(success)

    @patch("modules.db.get_last_working_mirror", return_value=None)
    @patch("modules.db.save_working_mirror")
    def test_ensure_working_site_mirror_server_error_500(self, mock_save, mock_get_last):
        mock_client = MagicMock()
        mock_res = MagicMock()
        mock_res.status_code = 500
        mock_client.get.return_value = mock_res

        # Test failure due to status code 500
        success = ensure_working_mirror(mock_client, verbose=True)
        self.assertFalse(success)

    @patch("modules.db.get_last_working_mirror", return_value=None)
    @patch("modules.db.save_working_mirror")
    def test_ensure_working_site_mirror_failure(self, mock_save, mock_get_last):
        mock_client = MagicMock()
        # Raise exception on get
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")

        # Test jikan site type
        success = ensure_working_jikan_mirror(mock_client, verbose=True)
        self.assertFalse(success)
        mock_save.assert_not_called()

if __name__ == "__main__":
    unittest.main()
