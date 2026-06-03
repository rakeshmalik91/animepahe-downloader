import unittest
from unittest.mock import patch, MagicMock, ANY
import os
import sys
import sqlite3

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import animepahe_download

class TestMain(unittest.TestCase):

    def setUp(self):
        config.BASE_DOWNLOAD_DIR = r"D:\Downloads\ANIME"
        config.DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_tracking.db")
        config.ANIMEPAHE_URL = "https://animepahe.com"
        config.KWIK_URL = "https://kwik.cx"
        config.JIKAN_API_URL = "https://api.jikan.moe/v4"
        
        # Start global patchers to prevent network and filesystem access
        self.patchers = []
        
        def start_patch(target, **kwargs):
            p = patch(target, **kwargs)
            self.patchers.append(p)
            return p.start()

        self.mock_init_db = start_patch("animepahe_download.init_db")
        self.mock_cleanup_db = start_patch("animepahe_download.cleanup_db")
        self.mock_log_debug = start_patch("animepahe_download.log_debug")
        self.mock_get_folder_by_id = start_patch("animepahe_download.get_folder_by_id", return_value=None)
        self.mock_get_tracked = start_patch("animepahe_download.get_tracked", return_value=None)
        self.mock_save_tracked = start_patch("animepahe_download.save_tracked")
        
        self.mock_ensure_mirror = start_patch("animepahe_download.ensure_working_mirror", return_value=True)
        self.mock_ensure_kwik_mirror = start_patch("animepahe_download.ensure_working_kwik_mirror", return_value=True)
        self.mock_ensure_jikan_mirror = start_patch("animepahe_download.ensure_working_jikan_mirror", return_value=True)
        
        self.mock_search = start_patch("animepahe_download.search_anime")
        self.mock_process_folder = start_patch("animepahe_download.process_one_folder", return_value=(True, "anime_123", "Frieren"))
        
        self.mock_sqlite = start_patch("sqlite3.connect")
        self.mock_exists = start_patch("os.path.exists", return_value=False)
        self.mock_isdir = start_patch("os.path.isdir", return_value=False)
        self.mock_listdir = start_patch("os.listdir", return_value=[])
        self.mock_walk = start_patch("os.walk", return_value=[])
        self.mock_makedirs = start_patch("os.makedirs")
        self.mock_input = start_patch("builtins.input")

        # Set up default sqlite connection mock to avoid returning truthy MagicMocks for cursor fetchone
        self.mock_conn = MagicMock()
        self.mock_cursor = MagicMock()
        self.mock_cursor.fetchone.return_value = None
        self.mock_conn.cursor.return_value = self.mock_cursor
        self.mock_sqlite.return_value = self.mock_conn

    def tearDown(self):
        for p in self.patchers:
            p.stop()
            
        # Clean up config properties if modified
        for attr in ['ANIMEPAHE_URL', 'JIKAN_API_URL', 'KWIK_URL']:
            if hasattr(config, attr):
                delattr(config, attr)

    def test_main_skip_folder(self):
        sys.argv = ["animepahe_download.py", "--skip-folder", "Frieren"]
        self.mock_exists.return_value = True

        animepahe_download.main()

        self.mock_save_tracked.assert_called_once_with(
            os.path.abspath(os.path.join(config.BASE_DOWNLOAD_DIR, "Frieren")),
            None, None, False
        )

    def test_main_unskip_folder(self):
        sys.argv = ["animepahe_download.py", "--unskip-folder", "Frieren"]
        self.mock_cursor.rowcount = 1

        animepahe_download.main()

        self.mock_sqlite.assert_called_once_with(config.DB_PATH)
        self.mock_cursor.execute.assert_any_call("DELETE FROM tracking WHERE folder_path = ?", ANY)
        self.mock_conn.commit.assert_called_once()

    def test_main_invalid_episodes(self):
        sys.argv = ["animepahe_download.py", "Frieren", "-ep", "invalid"]
        
        # Should exit early without checking mirrors
        animepahe_download.main()
        self.mock_ensure_mirror.assert_not_called()

    @patch("httpx.Client")
    def test_main_mirror_failure(self, mock_client):
        sys.argv = ["animepahe_download.py", "Frieren"]
        self.mock_ensure_mirror.return_value = False

        animepahe_download.main()
        self.mock_search.assert_not_called()

    @patch("httpx.Client")
    def test_main_specific_name_already_tracked(self, mock_client):
        sys.argv = ["animepahe_download.py", "Frieren"]
        
        # Mock get_tracked to return already tracked anime
        self.mock_get_tracked.return_value = ("anime_123", "Frieren", 1, None)

        animepahe_download.main()

        self.mock_process_folder.assert_called_once_with(
            ANY,
            os.path.join(config.BASE_DOWNLOAD_DIR, "Frieren"),
            "anime_123", "Frieren",
            "720p", None, episodes_filter=None, parallel=ANY
        )

    @patch("httpx.Client")
    def test_main_specific_name_untracked_search(self, mock_client):
        sys.argv = ["animepahe_download.py", "Frieren"]
        self.mock_get_tracked.return_value = None
        self.mock_process_folder.return_value = (True, "anime_123", "Frieren Title")
        
        # Search returns Frieren session info
        self.mock_search.return_value = ("anime_123", "Frieren Title", True, 5)

        animepahe_download.main()

        self.mock_search.assert_called_once_with(ANY, "Frieren")
        self.mock_process_folder.assert_called_once_with(
            ANY,
            os.path.join(config.BASE_DOWNLOAD_DIR, "Frieren"),
            "anime_123", "Frieren Title",
            "720p", None, episodes_filter=None, parallel=ANY
        )
        self.mock_save_tracked.assert_called_once_with(
            os.path.normpath(os.path.join(config.BASE_DOWNLOAD_DIR, "Frieren")),
            "anime_123", "Frieren Title", True
        )

    @patch("httpx.Client")
    def test_main_high_distance_prompt_yes(self, mock_client):
        sys.argv = ["animepahe_download.py", "Frieren"]
        self.mock_get_tracked.return_value = None
        self.mock_search.return_value = ("anime_123", "Wrong Frieren", True, 25)
        
        # User confirms 'y' (yes)
        self.mock_input.return_value = "y"

        animepahe_download.main()

        self.mock_process_folder.assert_called_once_with(
            ANY,
            os.path.join(config.BASE_DOWNLOAD_DIR, "Frieren"),
            "anime_123", "Wrong Frieren",
            ANY, ANY, episodes_filter=ANY, parallel=ANY
        )

    @patch("httpx.Client")
    def test_main_all_seasons_y(self, mock_client):
        sys.argv = ["animepahe_download.py", "Frieren", "--all-seasons", "-y"]
        
        # search_anime with return_all=True returns multiple matches
        self.mock_search.return_value = (
            [
                ("aid_1", "Frieren S1", "Frieren Season 1", {}),
                ("aid_2", "Frieren S2", "Frieren Season 2", {})
            ],
            True
        )

        animepahe_download.main()

        # Should process and save both seasons without prompting (due to -y)
        self.assertEqual(self.mock_process_folder.call_count, 2)
        self.assertEqual(self.mock_save_tracked.call_count, 2)

    @patch("httpx.Client")
    def test_main_more_seasons_library_scan(self, mock_client):
        sys.argv = ["animepahe_download.py", "--more-seasons"]
        
        # Mock listdir using side effect instead of a list sequence to avoid StopIteration
        def listdir_side_effect(path):
            if path == config.BASE_DOWNLOAD_DIR:
                return ["Frieren"]
            return []
        self.mock_listdir.side_effect = listdir_side_effect
        self.mock_isdir.return_value = True
        
        # Parent folder "Frieren" is tracked
        self.mock_get_tracked.return_value = ("aid_1", "Frieren Season 1", 1, None)

        # Search returns new sequel "Frieren Season 2"
        self.mock_search.return_value = (
            [
                ("aid_1", "Frieren S1", "Frieren Season 1", {"year": "2023"}),
                ("aid_2", "Frieren S2", "Frieren Season 2", {"year": "2024"})
            ],
            True
        )

        # Mock fetchone to return the Frieren folder only when querying "aid_1"
        def fetchone_side_effect():
            calls = self.mock_cursor.execute.call_args_list
            if calls:
                args, _ = calls[-1]
                if args[1] == ("aid_1",):
                    return ("D:\\Downloads\\ANIME\\Frieren",)
            return None
        self.mock_cursor.fetchone.side_effect = fetchone_side_effect

        # User chooses 'y' to download new season
        self.mock_input.return_value = "y"

        animepahe_download.main()

        self.mock_process_folder.assert_called_once_with(
            ANY,
            os.path.join(config.BASE_DOWNLOAD_DIR, "Frieren", "S2"),
            "aid_2", "Frieren Season 2",
            ANY, ANY, episodes_filter=ANY, parallel=ANY
        )

    @patch("httpx.Client")
    def test_main_full_library_scan_tracked(self, mock_client):
        sys.argv = ["animepahe_download.py"]
        
        # Mock os.walk library contents
        self.mock_walk.return_value = [
            (config.BASE_DOWNLOAD_DIR, ["Frieren"], []),
            (os.path.normpath(os.path.join(config.BASE_DOWNLOAD_DIR, "Frieren")), [], ["Anime_-_01_720p.mp4"])
        ]
        
        # Folder is tracked and has auto=1 (download updates)
        self.mock_get_tracked.return_value = ("anime_123", "Frieren", 1, None)

        animepahe_download.main()

        self.mock_process_folder.assert_called_once_with(
            ANY,
            os.path.normpath(os.path.join(config.BASE_DOWNLOAD_DIR, "Frieren")),
            "anime_123", "Frieren",
            ANY, ANY, episodes_filter=ANY, parallel=ANY
        )

if __name__ == "__main__":
    unittest.main()
