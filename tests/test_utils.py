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

        # Test jikan site type
        success = ensure_working_jikan_mirror(mock_client, verbose=True)
        self.assertFalse(success)
        mock_save.assert_not_called()

    def test_parse_year_tag(self):
        from modules.utils import parse_year_tag
        self.assertEqual(parse_year_tag(2020), "(2020)")
        self.assertEqual(parse_year_tag(2020, 2024), "(2020-2024)")
        self.assertEqual(parse_year_tag(2020, 2020), "(2020)")
        self.assertEqual(parse_year_tag(2020, is_ongoing=True), "(2020-)")
        self.assertEqual(parse_year_tag(2020, status="Currently Airing"), "(2020-)")
        self.assertEqual(parse_year_tag(2020, status="Finished Airing"), "(2020)")
        self.assertEqual(parse_year_tag(None), "")

    def test_is_season_folder_name(self):
        from modules.utils import is_season_folder_name
        self.assertTrue(is_season_folder_name("Season 1"))
        self.assertTrue(is_season_folder_name("Season 01"))
        self.assertTrue(is_season_folder_name("Season 1 (2020)"))
        self.assertTrue(is_season_folder_name("S2"))
        self.assertTrue(is_season_folder_name("Part 1"))
        self.assertTrue(is_season_folder_name("1st Season"))
        self.assertTrue(is_season_folder_name("OVAs"))
        self.assertTrue(is_season_folder_name("Movie"))
        self.assertFalse(is_season_folder_name("Some Anime"))
        self.assertFalse(is_season_folder_name("Some Anime (2020)"))
        self.assertFalse(is_season_folder_name("Frieren"))

    def test_format_anime_folder_name(self):
        from modules.utils import format_anime_folder_name
        # Anime folders -> add year tag if missing
        self.assertEqual(format_anime_folder_name("Some Anime", "(2020)"), "Some Anime (2020)")
        self.assertEqual(format_anime_folder_name("Some Anime", "(2020-2024)"), "Some Anime (2020-2024)")
        self.assertEqual(format_anime_folder_name("Some Anime", "(2020-)"), "Some Anime (2020-)")
        # Already has year tag -> keep intact
        self.assertEqual(format_anime_folder_name("Some Anime (2020)", "(2020)"), "Some Anime (2020)")
        self.assertEqual(format_anime_folder_name("Some Anime (2020-2024)", "(2020-2024)"), "Some Anime (2020-2024)")
        self.assertEqual(format_anime_folder_name("Some Anime (2020-)", "(2020-)"), "Some Anime (2020-)")
        # Season folders -> MUST NOT add year tag, and strip year tag if present
        self.assertEqual(format_anime_folder_name("Season 1", "(2020)"), "Season 1")
        self.assertEqual(format_anime_folder_name("Season 1 (2020)", "(2020)"), "Season 1")
        self.assertEqual(format_anime_folder_name("S2 (2020)", "(2020)"), "S2")

    @patch("os.path.exists")
    @patch("os.rename")
    @patch("modules.db.rename_tracked_folder")
    def test_ensure_folder_year_anime_folder(self, mock_db_rename, mock_rename, mock_exists):
        from modules.utils import ensure_folder_year
        mock_exists.side_effect = lambda path: path != r"D:\Downloads\ANIME\Some Anime (2020)"
        res = ensure_folder_year(r"D:\Downloads\ANIME\Some Anime", anime_title="Some Anime", meta={"year": 2020, "status": "Finished Airing"})
        self.assertEqual(res, r"D:\Downloads\ANIME\Some Anime (2020)")
        mock_rename.assert_called_once_with(r"D:\Downloads\ANIME\Some Anime", r"D:\Downloads\ANIME\Some Anime (2020)")
        mock_db_rename.assert_called_once_with(r"D:\Downloads\ANIME\Some Anime", r"D:\Downloads\ANIME\Some Anime (2020)")

    @patch("os.path.exists", return_value=True)
    @patch("os.rename")
    @patch("modules.db.rename_tracked_folder")
    def test_ensure_folder_year_disabled_when_flag_false(self, mock_db_rename, mock_rename, mock_exists):
        from modules.utils import ensure_folder_year
        config.ENABLE_YEAR_TAGS = False
        res = ensure_folder_year(r"D:\Downloads\ANIME\Some Anime", anime_title="Some Anime", meta={"year": 2020})
        self.assertEqual(res, r"D:\Downloads\ANIME\Some Anime")
        mock_rename.assert_not_called()
        config.ENABLE_YEAR_TAGS = True



    def test_get_anime_parent_folder(self):
        from modules.utils import get_anime_parent_folder
        config.BASE_DOWNLOAD_DIR = r"D:\Downloads\ANIME"
        self.assertEqual(get_anime_parent_folder(r"D:\Downloads\ANIME\One Piece"), r"D:\Downloads\ANIME\One Piece")
        self.assertEqual(get_anime_parent_folder(r"D:\Downloads\ANIME\Mushoku Tensei\Season 3"), r"D:\Downloads\ANIME\Mushoku Tensei")
        self.assertEqual(get_anime_parent_folder(r"D:\Downloads\ANIME\Mushoku Tensei\Season 3\Extra"), r"D:\Downloads\ANIME\Mushoku Tensei")

    @patch("os.path.exists", return_value=True)
    @patch("os.rename")
    @patch("modules.db.rename_tracked_folder")
    def test_ensure_folder_year_season_folder_remains_clean(self, mock_db_rename, mock_rename, mock_exists):
        from modules.utils import ensure_folder_year
        # Season folder whose parent already has year tag should not be renamed
        res = ensure_folder_year(r"D:\Downloads\ANIME\Some Anime (2020)\Season 1", anime_title="Some Anime", meta={"year": 2020})
        self.assertEqual(res, r"D:\Downloads\ANIME\Some Anime (2020)\Season 1")
        mock_rename.assert_not_called()

    @patch("os.path.exists")
    @patch("os.rename")
    @patch("modules.db.rename_tracked_folder")
    def test_ensure_folder_year_nested_season_folder(self, mock_db_rename, mock_rename, mock_exists):
        from modules.utils import ensure_folder_year
        config.BASE_DOWNLOAD_DIR = r"D:\Downloads\ANIME"
        # When called on a season subfolder, ensure the PARENT anime folder gets the year tag!
        mock_exists.side_effect = lambda path: path != r"D:\Downloads\ANIME\Mushoku Tensei (2021-)"
        res = ensure_folder_year(r"D:\Downloads\ANIME\Mushoku Tensei\Season 3", anime_title="Mushoku Tensei", meta={"year": 2021, "status": "Currently Airing"})
        self.assertEqual(res, r"D:\Downloads\ANIME\Mushoku Tensei (2021-)\Season 3")
        mock_rename.assert_called_once_with(r"D:\Downloads\ANIME\Mushoku Tensei", r"D:\Downloads\ANIME\Mushoku Tensei (2021-)")
        mock_db_rename.assert_called_once_with(r"D:\Downloads\ANIME\Mushoku Tensei", r"D:\Downloads\ANIME\Mushoku Tensei (2021-)")

    @patch("os.path.exists")
    @patch("os.rename")
    @patch("modules.db.rename_tracked_folder")
    def test_ensure_folder_year_uses_min_start_year(self, mock_db_rename, mock_rename, mock_exists):
        from modules.utils import ensure_folder_year
        config.BASE_DOWNLOAD_DIR = r"D:\Downloads\ANIME"
        # Mock search_anime returning multiple seasons: S1 (2022), S2 (2023), S3 (2026, Currently Airing)
        mock_client = MagicMock()
        mock_results = [
            ("aid1", "Bleach S1", "Bleach Thousand-Year Blood War", {"year": 2022, "status": "Finished Airing"}),
            ("aid2", "Bleach S2", "Bleach Thousand-Year Blood War - The Separation", {"year": 2023, "status": "Finished Airing"}),
            ("aid3", "Bleach S3", "Bleach Thousand-Year Blood War - The Calamity", {"year": 2026, "status": "Currently Airing"}),
        ]
        with patch("modules.scraper.search_anime", return_value=(mock_results, True)):
            mock_exists.side_effect = lambda path: path != r"D:\Downloads\ANIME\Bleach Thousand-Year Blood War (2022-)"
            res = ensure_folder_year(
                r"D:\Downloads\ANIME\Bleach Thousand-Year Blood War\Season 3 - The Calamity",
                anime_title="Bleach Thousand-Year Blood War - The Calamity",
                client=mock_client
            )
            self.assertEqual(res, r"D:\Downloads\ANIME\Bleach Thousand-Year Blood War (2022-)\Season 3 - The Calamity")
            mock_rename.assert_called_once_with(r"D:\Downloads\ANIME\Bleach Thousand-Year Blood War", r"D:\Downloads\ANIME\Bleach Thousand-Year Blood War (2022-)")
            mock_db_rename.assert_called_once_with(r"D:\Downloads\ANIME\Bleach Thousand-Year Blood War", r"D:\Downloads\ANIME\Bleach Thousand-Year Blood War (2022-)")

if __name__ == "__main__":
    unittest.main()



