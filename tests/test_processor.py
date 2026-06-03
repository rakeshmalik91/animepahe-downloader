import unittest
from unittest.mock import patch, MagicMock, call, ANY
import os
import sys
import threading
import time

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from modules.processor import process_one_folder

class TestProcessor(unittest.TestCase):

    def setUp(self):
        config.ANIMEPAHE_URL = "https://animepahe.com"
        config.KWIK_URL = "https://kwik.cx"
        config.JIKAN_API_URL = "https://api.jikan.moe/v4"
        config.BASE_DOWNLOAD_DIR = r"D:\Downloads\ANIME"
        config.DEFAULT_LANGUAGE = "en"
        config.AUTO_REJECT_LANGUAGE_FALLBACK = False
        config.OPEN_BROWSER_ON_FAIL = False
        config.MAX_DOWNLOAD_RETRIES = 2
        config.DOWNLOAD_RETRY_BASE_DELAY = 0.01
        config.DOWNLOAD_RETRY_MULTIPLIER = 1
        config.AUTO_SKIP_DAYS = 30
        config.MAX_DISTANCE_THRESHOLD = 20

        # Start patchers to globally mock all dependencies
        self.patchers = []
        
        def start_patch(target, **kwargs):
            p = patch(target, **kwargs)
            self.patchers.append(p)
            return p.start()

        # Patch top-level imports in modules.processor namespace
        self.mock_update_checked = start_patch("modules.processor.update_last_checked")
        self.mock_save_tracked = start_patch("modules.processor.save_tracked")
        self.mock_get_tracked = start_patch("modules.processor.get_tracked", return_value=None)
        self.mock_latest_ep = start_patch("modules.processor.get_latest_episode_local", return_value=0)
        self.mock_detect_lang = start_patch("modules.processor.detect_lang_from_files", return_value=None)
        self.mock_ep_present = start_patch("modules.processor.is_episode_already_present", return_value=False)
        self.mock_search = start_patch("modules.processor.search_anime")
        self.mock_direct_link = start_patch("modules.processor.get_direct_link")
        self.mock_download = start_patch("modules.processor.download_file", return_value=True)
        self.mock_ensure_mirror = start_patch("modules.processor.ensure_working_mirror", return_value=True)

        # Patch original database functions to override local imports inside nested functions
        self.mock_db_get_tracked = start_patch("modules.db.get_tracked", return_value=None)
        self.mock_db_save_tracked = start_patch("modules.db.save_tracked")
        self.mock_db_update_checked = start_patch("modules.db.update_last_checked")

        # Patch system libraries
        self.mock_input = start_patch("builtins.input")
        self.mock_mtime = start_patch("os.path.getmtime", return_value=1700000000.0)
        self.mock_walk = start_patch("os.walk", return_value=[])
        self.mock_exists = start_patch("os.path.exists", return_value=False)
        self.mock_rename = start_patch("os.rename")

        # Set default mock behaviors
        self.mock_search.return_value = ("anime_123", "Frieren", True, 5)

    def tearDown(self):
        # Stop patchers
        for p in self.patchers:
            p.stop()
            
        # Clean up dynamic configs
        for attr in ['ANIMEPAHE_URL', 'JIKAN_API_URL', 'KWIK_URL']:
            if hasattr(config, attr):
                delattr(config, attr)

    def test_process_one_folder_basic_success(self):
        self.mock_latest_ep.return_value = 1
        self.mock_direct_link.return_value = ("https://owocdn.top/file.mp4", "jap", {"jap"})

        mock_client = MagicMock()
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "data": [
                {"episode": 1, "session": "sess_1"},
                {"episode": 2, "session": "sess_2"}
            ]
        }
        mock_client.get.return_value = mock_res

        success, anime_id, anime_title = process_one_folder(
            mock_client,
            r"D:\Downloads\ANIME\Frieren",
            anime_id="anime_123",
            anime_title="Frieren"
        )

        self.assertTrue(success)
        self.assertEqual(anime_id, "anime_123")
        self.assertEqual(anime_title, "Frieren")
        self.mock_download.assert_called_once_with("https://owocdn.top/file.mp4", ANY, ANY, position=None)
        self.mock_update_checked.assert_called_once_with(r"D:\Downloads\ANIME\Frieren")

    def test_process_one_folder_search_path(self):
        self.mock_direct_link.return_value = ("https://owocdn.top/file.mp4", "en", {"en"})

        mock_client = MagicMock()
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "data": [{"episode": 1, "session": "sess_1"}]
        }
        mock_client.get.return_value = mock_res

        success, anime_id, anime_title = process_one_folder(
            mock_client,
            r"D:\Downloads\ANIME\Frieren",
            anime_id=None,
            anime_title=None
        )

        self.assertTrue(success)
        self.assertEqual(anime_id, "anime_123")
        self.assertEqual(anime_title, "Frieren")
        self.mock_search.assert_called_once_with(mock_client, "Frieren")

    def test_process_one_folder_high_distance_prompt_custom_url(self):
        self.mock_search.return_value = ("anime_123", "Wrong Title", True, 25)
        self.mock_input.side_effect = ["u", "https://animepahe.com/anime/1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"]

        mock_client = MagicMock()
        mock_res_html = MagicMock()
        mock_res_html.status_code = 200
        mock_res_html.text = "<h1>Custom Anime Title<span>"
        
        mock_res_api = MagicMock()
        mock_res_api.status_code = 500
        
        mock_client.get.side_effect = [mock_res_html, mock_res_api]

        success, anime_id, anime_title = process_one_folder(
            mock_client,
            r"D:\Downloads\ANIME\MyAnime",
            anime_id=None,
            anime_title=None
        )

        self.assertFalse(success)  # Failed due to 500 API code
        self.assertEqual(anime_id, "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d")
        self.assertEqual(anime_title, "Custom Anime Title")

    def test_process_one_folder_high_distance_prompt_no(self):
        self.mock_search.return_value = ("anime_123", "Wrong Title", True, 25)
        self.mock_input.return_value = "n"

        mock_client = MagicMock()
        success, anime_id, anime_title = process_one_folder(
            mock_client,
            r"D:\Downloads\ANIME\MyAnime",
            anime_id=None,
            anime_title=None
        )
        self.assertEqual((success, anime_id, anime_title), (False, None, None))

    def test_process_one_folder_silent_season_correction(self):
        mock_client = MagicMock()
        self.mock_search.side_effect = [
            ("new_id", "Re Zero Season 2", True, 5) 
        ]

        mock_res_api = MagicMock()
        mock_res_api.status_code = 500
        mock_client.get.return_value = mock_res_api

        success, anime_id, anime_title = process_one_folder(
            mock_client,
            r"D:\Downloads\ANIME\Re Zero Season 2",
            anime_id="old_id",
            anime_title="Re Zero Season 1"
        )

        self.assertFalse(success)
        self.assertEqual(anime_id, "new_id")
        self.assertEqual(anime_title, "Re Zero Season 2")
        self.mock_save_tracked.assert_called_with(r"D:\Downloads\ANIME\Re Zero Season 2", "new_id", "Re Zero Season 2", True)

    def test_process_one_folder_silent_season_rejected_distance(self):
        mock_client = MagicMock()
        config.MAX_DISTANCE_THRESHOLD = 2

        self.mock_search.side_effect = [
            ("other_id", "Other Anime Season 2", True, 5)
        ]

        mock_res_api = MagicMock()
        mock_res_api.status_code = 500
        mock_client.get.return_value = mock_res_api

        success, anime_id, anime_title = process_one_folder(
            mock_client,
            r"D:\Downloads\ANIME\Re Zero Season 2",
            anime_id="old_id",
            anime_title="Re Zero Season 1"
        )

        self.assertFalse(success)
        self.assertEqual(anime_id, "old_id")
        self.assertEqual(anime_title, "Re Zero Season 1")
        self.mock_save_tracked.assert_not_called()

    def test_process_one_folder_api_mirror_retry_fail(self):
        # Temporarily unmock ensure_working_mirror for this specific test
        self.mock_ensure_mirror.return_value = False
        mock_client = MagicMock()
        mock_res = MagicMock()
        mock_res.status_code = 502
        mock_client.get.return_value = mock_res

        success, anime_id, anime_title = process_one_folder(
            mock_client,
            r"D:\Downloads\ANIME\Frieren",
            anime_id="anime_123",
            anime_title="Frieren"
        )
        self.assertFalse(success)

    def test_process_one_folder_api_404_rematch(self):
        mock_client = MagicMock()
        
        mock_res_404 = MagicMock()
        mock_res_404.status_code = 404
        
        mock_res_success = MagicMock()
        mock_res_success.status_code = 200
        mock_res_success.json.return_value = {"data": []}
        
        # Side effect has 3 elements: first request fails 404, retry fails 404, rematch succeeds 200
        mock_client.get.side_effect = [mock_res_404, mock_res_404, mock_res_success]
        self.mock_search.return_value = ("new_id", "Frieren Rematch", True, 25)
        self.mock_input.return_value = "y"

        success, anime_id, anime_title = process_one_folder(
            mock_client,
            r"D:\Downloads\ANIME\Frieren",
            anime_id="old_id",
            anime_title="Frieren"
        )

        self.assertTrue(success)
        self.assertEqual(anime_id, "new_id")
        self.assertEqual(anime_title, "Frieren Rematch")
        self.mock_save_tracked.assert_called_with(r"D:\Downloads\ANIME\Frieren", "new_id", "Frieren Rematch", True)

    def test_process_one_folder_already_up_to_date_auto_skip(self):
        mock_client = MagicMock()
        mock_res = MagicMock()
        mock_res.status_code = 200
        # Return empty data so no new episodes are found
        mock_res.json.return_value = {
            "data": []
        }
        mock_client.get.return_value = mock_res

        self.mock_walk.return_value = [
            ("root", [], ["Anime_-_01_720p.mp4"])
        ]
        self.mock_exists.return_value = True

        from datetime import datetime, timedelta
        long_ago = (datetime.now() - timedelta(days=40)).isoformat()
        self.mock_get_tracked.return_value = ("anime_123", "Frieren", 1, long_ago)
        self.mock_db_get_tracked.return_value = ("anime_123", "Frieren", 1, long_ago)

        success, anime_id, anime_title = process_one_folder(
            mock_client,
            r"D:\Downloads\ANIME\Frieren",
            anime_id="anime_123",
            anime_title="Frieren"
        )

        self.assertTrue(success)
        self.mock_save_tracked.assert_called_with(r"D:\Downloads\ANIME\Frieren", "anime_123", "Frieren", False, update_time=False)

    def test_process_one_folder_rename_existing_files(self):
        mock_client = MagicMock()
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "data": [
                {"episode": 1, "session": "sess_1"},
                {"episode": 100, "session": "sess_100"}
            ]
        }
        mock_client.get.return_value = mock_res

        self.mock_walk.return_value = [
            (r"D:\Downloads\ANIME\Frieren", [], ["Anime_-_01_720p.mp4"])
        ]

        self.mock_direct_link.return_value = (None, None, set())

        success, anime_id, anime_title = process_one_folder(
            mock_client,
            r"D:\Downloads\ANIME\Frieren",
            anime_id="anime_123",
            anime_title="Frieren"
        )

        self.assertTrue(success)
        self.mock_rename.assert_called_once_with(
            os.path.join(r"D:\Downloads\ANIME\Frieren", "Anime_-_01_720p.mp4"),
            os.path.join(r"D:\Downloads\ANIME\Frieren", "Anime_-_001_720p.mp4")
        )

    def test_process_one_folder_language_fallback_prompt(self):
        mock_client = MagicMock()
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "data": [{"episode": 1, "session": "sess_1"}]
        }
        mock_client.get.return_value = mock_res

        self.mock_direct_link.side_effect = [
            (None, None, {"jap"}),
            ("https://owocdn.top/jap_version.mp4", "jap", {"jap"})
        ]

        self.mock_input.return_value = "y"

        success, anime_id, anime_title = process_one_folder(
            mock_client,
            r"D:\Downloads\ANIME\Frieren",
            anime_id="anime_123",
            anime_title="Frieren",
            lang=None
        )

        self.assertTrue(success)
        self.assertEqual(self.mock_direct_link.call_count, 2)
        self.mock_download.assert_called_once_with("https://owocdn.top/jap_version.mp4", ANY, ANY, position=None)

    @patch("time.sleep")
    def test_process_one_folder_download_fail_retries_and_force_bypass(self, mock_sleep):
        mock_client = MagicMock()
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "data": [{"episode": 1, "session": "sess_1"}]
        }
        mock_client.get.return_value = mock_res

        self.mock_direct_link.side_effect = [
            ("https://owocdn.top/try1.mp4", "en", {"en"}),
            ("https://owocdn.top/try2.mp4", "en", {"en"})
        ]

        self.mock_download.side_effect = [False, True]

        success, anime_id, anime_title = process_one_folder(
            mock_client,
            r"D:\Downloads\ANIME\Frieren",
            anime_id="anime_123",
            anime_title="Frieren"
        )

        self.assertTrue(success)
        self.assertEqual(self.mock_download.call_count, 2)
        self.assertEqual(self.mock_direct_link.call_count, 2)
        self.mock_direct_link.assert_has_calls([
            call(mock_client, "anime_123", "sess_1", "720p", "en"),
            call(mock_client, "anime_123", "sess_1", "720p", "en", retry_with_browser="force")
        ])

    @patch("time.sleep")
    def test_process_one_folder_download_exhausted_prompts(self, mock_sleep):
        mock_client = MagicMock()
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "data": [{"episode": 1, "session": "sess_1"}]
        }
        mock_client.get.return_value = mock_res
        self.mock_direct_link.return_value = ("https://owocdn.top/file.mp4", "en", {"en"})

        self.mock_download.return_value = False
        self.mock_input.return_value = "s"

        success, anime_id, anime_title = process_one_folder(
            mock_client,
            r"D:\Downloads\ANIME\Frieren",
            anime_id="anime_123",
            anime_title="Frieren",
            parallel=1
        )

        self.assertTrue(success)
        self.assertEqual(self.mock_download.call_count, 3) 
        self.mock_input.assert_called_once()

    def test_process_one_folder_parallel_downloads(self):
        mock_client = MagicMock()
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "data": [
                {"episode": 1, "session": "sess_1"},
                {"episode": 2, "session": "sess_2"}
            ]
        }
        mock_client.get.return_value = mock_res
        self.mock_direct_link.return_value = ("https://owocdn.top/file.mp4", "en", {"en"})

        success, anime_id, anime_title = process_one_folder(
            mock_client,
            r"D:\Downloads\ANIME\Frieren",
            anime_id="anime_123",
            anime_title="Frieren",
            parallel=2
        )

        self.assertTrue(success)
        self.assertEqual(self.mock_download.call_count, 2)

if __name__ == "__main__":
    unittest.main()
