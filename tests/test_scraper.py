import unittest
from unittest.mock import patch, MagicMock, mock_open, ANY
import os
import sys
import re

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from modules.scraper import KwikDecoder, get_browser_cookies, resolve_kwik_direct, get_direct_link, search_anime

class MockChromeClass:
    driver_instance = None
    constructor_mock = None

    def __new__(cls, *args, **kwargs):
        if cls.constructor_mock:
            cls.constructor_mock(*args, **kwargs)
        return cls.driver_instance

    def __del__(self):
        pass
    def quit(self, *args, **kwargs):
        pass

class TestScraper(unittest.TestCase):

    def setUp(self):
        # Configure dynamic configuration properties that are usually populated at startup
        config.ANIMEPAHE_URL = "https://animepahe.com"
        config.JIKAN_API_URL = "https://api.jikan.moe/v4"
        config.KWIK_URL = "https://kwik.cx"
        
        # Reset MockChromeClass states
        MockChromeClass.driver_instance = None
        MockChromeClass.constructor_mock = None

    def tearDown(self):
        # Clean up dynamically added configuration variables to prevent pollution of other tests
        for attr in ['ANIMEPAHE_URL', 'JIKAN_API_URL', 'KWIK_URL']:
            if hasattr(config, attr):
                delattr(config, attr)

    def test_kwik_decoder_base_to_int(self):
        val = KwikDecoder._base_to_int("1", 10, 10)
        self.assertEqual(val, 1)
        
        val_invalid = KwikDecoder._base_to_int("?", 10, 10)
        self.assertEqual(val_invalid, 0)

    def test_kwik_decoder_decode(self):
        # wg has length 7, index 6 is valid
        decoded = KwikDecoder.decode("b", "abcdefg", 1, 6)
        self.assertEqual(decoded, "\x00")

    @patch.dict(sys.modules, {'undetected_chromedriver': None})
    def test_get_browser_cookies_import_error(self):
        cookies, ua, resolved = get_browser_cookies("http://example.com")
        self.assertIsNone(cookies)
        self.assertIsNone(ua)
        self.assertIsNone(resolved)

    @patch("sys.platform", "win32")
    @patch("time.time")
    @patch("time.sleep")
    @patch("tempfile.mkdtemp", return_value="mock_temp_dir")
    @patch("shutil.rmtree")
    def test_get_browser_cookies_success(self, mock_rmtree, mock_mktemp, mock_sleep, mock_time):
        # Set up mock time sequence to exit CF challenge quickly
        mock_time.side_effect = [100.0, 102.0, 104.0, 106.0]

        # Setup mock undetected_chromedriver and selenium modules
        mock_uc = MagicMock()
        mock_uc.Chrome = MockChromeClass
        mock_uc.ChromeOptions = MagicMock
        
        mock_driver = MagicMock()
        # Mock methods called on driver
        mock_driver.title = "Kwik Download"
        mock_driver.current_url = "https://kwik.cx/abc"
        mock_driver.get_cookies.return_value = [{"name": "cf_clearance", "value": "123", "domain": ".kwik.cx"}]
        mock_driver.execute_script.side_effect = lambda script, *args: "MockUA" if "userAgent" in script else None
        
        # Performance logs capture a URL matching owocdn
        mock_driver.get_log.return_value = [
            {"message": r'{"url": "https://owocdn.top/download/file.mp4?token=abc"}'}
        ]
        
        mock_constructor = MagicMock()
        MockChromeClass.constructor_mock = mock_constructor
        MockChromeClass.driver_instance = mock_driver

        mock_by = MagicMock()
        mock_wait = MagicMock()
        mock_ec = MagicMock()
        
        mock_button = MagicMock()
        mock_wait_instance = MagicMock()
        mock_wait_instance.until.return_value = mock_button
        mock_wait.WebDriverWait.return_value = mock_wait_instance

        # Registry mock for Windows Chrome version detection
        mock_winreg = MagicMock()
        mock_winreg.HKEY_CURRENT_USER = "HKCU"
        mock_winreg.OpenKey.return_value.__enter__.return_value = "key"
        mock_winreg.QueryValueEx.return_value = ("124.0.5.2", None)

        modules_dict = {
            'undetected_chromedriver': mock_uc,
            'selenium': MagicMock(),
            'selenium.webdriver': MagicMock(),
            'selenium.webdriver.common': MagicMock(),
            'selenium.webdriver.common.by': mock_by,
            'selenium.webdriver.support': MagicMock(),
            'selenium.webdriver.support.ui': mock_wait,
            'selenium.webdriver.support.expected_conditions': mock_ec,
            'winreg': mock_winreg
        }

        with patch.dict(sys.modules, modules_dict):
            # Test successful path
            config.BROWSER_VERSION_MAIN = None
            cookies, ua, resolved = get_browser_cookies("https://kwik.cx/f/abc")
            self.assertEqual(ua, "MockUA")
            self.assertEqual(resolved, "https://owocdn.top/download/file.mp4?token=abc")
            self.assertEqual(cookies[0]["name"], "cf_clearance")

    @patch("sys.platform", "win32")
    @patch("time.time")
    @patch("time.sleep")
    @patch("tempfile.mkdtemp", return_value="mock_temp_dir")
    @patch("shutil.rmtree")
    def test_get_browser_cookies_chrome_init_fail_retry(self, mock_rmtree, mock_mktemp, mock_sleep, mock_time):
        mock_time.side_effect = [100.0, 102.0, 104.0, 106.0]

        mock_uc = MagicMock()
        mock_driver = MagicMock()
        mock_driver.title = "Kwik Download"
        mock_driver.current_url = "https://kwik.cx/abc"
        mock_driver.get_cookies.return_value = []
        mock_driver.execute_script.return_value = "MockUA"
        mock_driver.get_log.return_value = []

        # First call raises version mismatch exception
        # Second call succeeds
        mock_constructor = MagicMock(side_effect=[
            Exception("Current browser version is 120"),
            mock_driver
        ])
        MockChromeClass.constructor_mock = mock_constructor
        MockChromeClass.driver_instance = mock_driver
        mock_uc.Chrome = MockChromeClass
        mock_uc.ChromeOptions = MagicMock

        mock_by = MagicMock()
        mock_wait = MagicMock()
        mock_ec = MagicMock()
        mock_wait_instance = MagicMock()
        mock_wait_instance.until.return_value = None
        mock_wait.WebDriverWait.return_value = mock_wait_instance

        mock_winreg = MagicMock()
        mock_winreg.HKEY_CURRENT_USER = "HKCU"
        mock_winreg.OpenKey.return_value.__enter__.return_value = "key"
        mock_winreg.QueryValueEx.return_value = ("124.0.5.2", None)

        modules_dict = {
            'undetected_chromedriver': mock_uc,
            'selenium': MagicMock(),
            'selenium.webdriver': MagicMock(),
            'selenium.webdriver.common': MagicMock(),
            'selenium.webdriver.common.by': mock_by,
            'selenium.webdriver.support': MagicMock(),
            'selenium.webdriver.support.ui': mock_wait,
            'selenium.webdriver.support.expected_conditions': mock_ec,
            'winreg': mock_winreg
        }

        with patch.dict(sys.modules, modules_dict):
            config.BROWSER_VERSION_MAIN = 124
            cookies, ua, resolved = get_browser_cookies("https://kwik.cx/f/abc")
            self.assertEqual(ua, "MockUA")
            self.assertEqual(mock_constructor.call_count, 2)
            mock_constructor.assert_any_call(options=ANY, version_main=120)

    @patch("sys.platform", "win32")
    @patch("time.time")
    @patch("time.sleep")
    @patch("tempfile.mkdtemp", return_value="mock_temp_dir")
    @patch("shutil.rmtree")
    def test_get_browser_cookies_chrome_init_fail_generic_retry(self, mock_rmtree, mock_mktemp, mock_sleep, mock_time):
        mock_time.side_effect = [100.0, 102.0, 104.0, 106.0]

        mock_uc = MagicMock()
        mock_driver = MagicMock()
        mock_driver.title = "Kwik Download"
        mock_driver.current_url = "https://kwik.cx/abc"
        mock_driver.get_cookies.return_value = []
        mock_driver.execute_script.return_value = "MockUA"
        mock_driver.get_log.return_value = []

        # First call raises generic exception
        # Second call succeeds
        mock_constructor = MagicMock(side_effect=[
            Exception("Generic Chrome init error"),
            mock_driver
        ])
        MockChromeClass.constructor_mock = mock_constructor
        MockChromeClass.driver_instance = mock_driver
        mock_uc.Chrome = MockChromeClass
        mock_uc.ChromeOptions = MagicMock

        mock_by = MagicMock()
        mock_wait = MagicMock()
        mock_ec = MagicMock()
        mock_wait_instance = MagicMock()
        mock_wait_instance.until.return_value = None
        mock_wait.WebDriverWait.return_value = mock_wait_instance

        mock_winreg = MagicMock()
        mock_winreg.HKEY_CURRENT_USER = "HKCU"
        mock_winreg.OpenKey.side_effect = Exception("Registry key not found")

        modules_dict = {
            'undetected_chromedriver': mock_uc,
            'selenium': MagicMock(),
            'selenium.webdriver': MagicMock(),
            'selenium.webdriver.common': MagicMock(),
            'selenium.webdriver.common.by': mock_by,
            'selenium.webdriver.support': MagicMock(),
            'selenium.webdriver.support.ui': mock_wait,
            'selenium.webdriver.support.expected_conditions': mock_ec,
            'winreg': mock_winreg
        }

        with patch.dict(sys.modules, modules_dict):
            # If BROWSER_VERSION_MAIN is set, it will retry with None
            config.BROWSER_VERSION_MAIN = 124
            cookies, ua, resolved = get_browser_cookies("https://kwik.cx/f/abc")
            self.assertEqual(ua, "MockUA")
            self.assertEqual(mock_constructor.call_count, 2)
            mock_constructor.assert_any_call(options=ANY, version_main=None)

    @patch.dict(sys.modules, {'curl_cffi': None, 'curl_cffi.requests': None})
    def test_resolve_kwik_direct_curl_cffi_missing(self):
        result = resolve_kwik_direct("https://kwik.cx/f/abc", "referer")
        self.assertIsNone(result)

    @patch("modules.db.get_kwik_session", return_value=({"cf_clearance": "abc"}, "MockUA"))
    @patch("modules.db.save_kwik_session")
    @patch("modules.scraper.KwikDecoder.decode")
    @patch("time.sleep")
    def test_resolve_kwik_direct_success_dict_cookies(self, mock_sleep, mock_decode, mock_save, mock_get):
        # Mock curl_cffi requests
        mock_curl = MagicMock()
        mock_session = MagicMock()
        mock_curl.Session.return_value = mock_session
        
        mock_curl_cffi = MagicMock()
        mock_curl_cffi.requests = mock_curl

        # Initial get response containing script
        mock_res_get = MagicMock()
        mock_res_get.status_code = 200
        mock_res_get.text = 'eval(function(p,a,c,k,e,d){...}("hb_val", 10, "wg_val", 5, 6, 7))'
        mock_session.get.return_value = mock_res_get

        # Decode mocked to return _token and action
        mock_decode.return_value = 'name="_token" value="test_token" action="https://kwik.cx/f/abc"'

        # POST redirect response
        mock_res_post = MagicMock()
        mock_res_post.status_code = 302
        mock_res_post.headers = {"Location": "https://owocdn.top/file.mp4"}
        mock_session.post.return_value = mock_res_post

        with patch.dict(sys.modules, {'curl_cffi': mock_curl_cffi, 'curl_cffi.requests': mock_curl}):
            result = resolve_kwik_direct("https://kwik.cx/f/abc", "https://animepahe.com")
            self.assertEqual(result, "https://owocdn.top/file.mp4")
            mock_session.get.assert_called_once()
            # Verify cookies were set
            mock_session.cookies.set.assert_called_with("cf_clearance", "abc", domain="kwik.cx")

    @patch("modules.db.get_kwik_session", return_value=([{"name": "cf_clearance", "value": "xyz", "domain": ".kwik.cx"}], "MockUA"))
    @patch("modules.db.save_kwik_session")
    @patch("modules.scraper.get_browser_cookies")
    @patch("modules.scraper.KwikDecoder.decode")
    @patch("time.sleep")
    def test_resolve_kwik_direct_cloudflare_bypass_and_retry(self, mock_sleep, mock_decode, mock_browser, mock_save, mock_get):
        config.ENABLE_BROWSER_BYPASS = True
        
        mock_curl = MagicMock()
        mock_session = MagicMock()
        mock_curl.Session.return_value = mock_session
        
        mock_curl_cffi = MagicMock()
        mock_curl_cffi.requests = mock_curl

        # First GET fails (status 403 or non-eval response)
        mock_res_get_fail = MagicMock()
        mock_res_get_fail.status_code = 403
        mock_res_get_fail.text = "Cloudflare Challenge"
        
        # Second GET after browser bypass succeeds
        mock_res_get_success = MagicMock()
        mock_res_get_success.status_code = 200
        mock_res_get_success.text = 'eval(function(p,a,c,k,e,d){...}("hb_val", 10, "wg_val", 5, 6, 7))'
        
        mock_session.get.side_effect = [mock_res_get_fail, mock_res_get_success]

        # Browser bypass mock returns new session info
        mock_browser.return_value = ([{"name": "cf_clearance", "value": "new_val", "domain": ".kwik.cx"}], "NewUA", None)

        mock_decode.return_value = 'name="_token" value="test_token" action="https://kwik.cx/f/abc"'

        mock_res_post = MagicMock()
        mock_res_post.status_code = 302
        mock_res_post.headers = {"Location": "https://owocdn.top/file.mp4"}
        mock_session.post.return_value = mock_res_post

        with patch.dict(sys.modules, {'curl_cffi': mock_curl_cffi, 'curl_cffi.requests': mock_curl}):
            result = resolve_kwik_direct("https://kwik.cx/f/abc", "https://animepahe.com", retry_with_browser=True)
            self.assertEqual(result, "https://owocdn.top/file.mp4")
            self.assertEqual(mock_session.get.call_count, 2)
            mock_browser.assert_called_once_with("https://kwik.cx/f/abc", extra_url=None)
            mock_save.assert_called_once()

    @patch("modules.db.get_kwik_session", return_value=(None, None))
    @patch("modules.scraper.get_browser_cookies")
    def test_resolve_kwik_direct_browser_resolves_directly(self, mock_browser, mock_get):
        mock_curl = MagicMock()
        mock_session = MagicMock()
        mock_curl.Session.return_value = mock_session
        
        mock_curl_cffi = MagicMock()
        mock_curl_cffi.requests = mock_curl

        mock_res_get_fail = MagicMock()
        mock_res_get_fail.status_code = 403
        mock_res_get_fail.text = "Blocked"
        mock_session.get.return_value = mock_res_get_fail

        # Browser mock returns the resolved URL directly (extracted from logs)
        mock_browser.return_value = ([], "MockUA", "https://owocdn.top/resolved_directly.mp4")

        config.ENABLE_BROWSER_BYPASS = True

        with patch.dict(sys.modules, {'curl_cffi': mock_curl_cffi, 'curl_cffi.requests': mock_curl}):
            result = resolve_kwik_direct("https://kwik.cx/f/abc", "https://animepahe.com", retry_with_browser=True)
            self.assertEqual(result, "https://owocdn.top/resolved_directly.mp4")

    @patch("modules.scraper.resolve_kwik_direct")
    @patch("modules.scraper.ensure_working_mirror")
    @patch("cloudscraper.create_scraper")
    def test_get_direct_link_success_jap(self, mock_create_scraper, mock_ensure_mirror, mock_resolve_kwik):
        mock_client = MagicMock()
        
        # Phase 1: Play page containing mirrors
        mock_res_play = MagicMock()
        mock_res_play.status_code = 200
        mock_res_play.text = """
        <a href="https://kwik.cx/123">720p</a>
        <a href="https://kwik.cx/456">720p <span class="badge">eng</span></a>
        """
        mock_client.get.return_value = mock_res_play

        # Phase 2: Redirector page leading to kwik CX
        mock_scraper = MagicMock()
        mock_res_redirect = MagicMock()
        mock_res_redirect.url = "https://kwik.cx/f/abc"
        mock_res_redirect.text = "https://kwik.cx/f/abc"
        mock_scraper.get.return_value = mock_res_redirect
        mock_create_scraper.return_value = mock_scraper

        mock_resolve_kwik.return_value = "https://owocdn.top/file.mp4"

        # Call under test with Japanese lang target
        url, lang, avails = get_direct_link(mock_client, "anime_123", "session_abc", target_quality="720p", target_lang="jap")
        
        self.assertEqual(url, "https://owocdn.top/file.mp4")
        self.assertEqual(lang, "jap")
        self.assertEqual(avails, {"jap", "en"})

    @patch("modules.scraper.resolve_kwik_direct")
    @patch("modules.scraper.ensure_working_mirror")
    @patch("cloudscraper.create_scraper")
    def test_get_direct_link_mirror_retry_and_normalize_resolutions(self, mock_create_scraper, mock_ensure_mirror, mock_resolve_kwik):
        mock_client = MagicMock()
        
        # First request fails, second succeeds
        mock_res_fail = MagicMock()
        mock_res_fail.status_code = 502
        
        mock_res_success = MagicMock()
        mock_res_success.status_code = 200
        mock_res_success.text = """
        <a href="https://kwik.cx/360">360p</a>
        <a href="https://kwik.cx/1080">1080p</a>
        """
        mock_client.get.side_effect = [mock_res_fail, mock_res_success]
        mock_ensure_mirror.return_value = True

        mock_scraper = MagicMock()
        mock_res_redirect = MagicMock()
        mock_res_redirect.url = "https://kwik.cx/f/abc"
        mock_res_redirect.text = "https://kwik.cx/f/abc"
        mock_scraper.get.return_value = mock_res_redirect
        mock_create_scraper.return_value = mock_scraper
        mock_resolve_kwik.return_value = "https://owocdn.top/1080p.mp4"

        # Ask for 1080p
        url, lang, avails = get_direct_link(mock_client, "anime_123", "session_abc", target_quality="1080p", target_lang="jap")
        self.assertEqual(url, "https://owocdn.top/1080p.mp4")
        self.assertEqual(lang, "jap")
        self.assertEqual(avails, {"jap"})

    @patch("modules.scraper.ensure_working_jikan_mirror", return_value=True)
    def test_search_anime_success(self, mock_jikan_mirror):
        mock_client = MagicMock()
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "data": [
                {"session": "sess_1", "title": "Frieren: Beyond Journey's End", "type": "TV"},
                {"session": "sess_2", "title": "Frieren: Beyond Journey's End Season 2", "type": "TV"}
            ]
        }
        mock_client.get.return_value = mock_res

        # Test query Frieren
        anime_id, title, api_ok, dist = search_anime(mock_client, "Frieren")
        self.assertEqual(anime_id, "sess_1")
        self.assertEqual(title, "Frieren Beyond Journey's End")
        self.assertTrue(api_ok)

    @patch("modules.scraper.ensure_working_jikan_mirror", return_value=True)
    @patch("modules.scraper.ensure_working_mirror", return_value=True)
    def test_search_anime_mirror_retry_and_jikan_fallback(self, mock_mirror, mock_jikan):
        mock_client = MagicMock()
        
        # First search request fails, second returns only movies
        mock_res_fail = MagicMock()
        mock_res_fail.status_code = 502
        
        mock_res_movies_only = MagicMock()
        mock_res_movies_only.status_code = 200
        mock_res_movies_only.json.return_value = {
            "data": [
                {"session": "movie_sess", "title": "Witch Hat Atelier Movie", "type": "Movie"}
            ]
        }

        # Jikan response with TV series information
        mock_res_jikan = MagicMock()
        mock_res_jikan.status_code = 200
        mock_res_jikan.json.return_value = {
            "data": [
                {
                    "title": "Witch Hat Atelier",
                    "title_english": "Tongari Boushi no Atelier",
                    "title_synonyms": ["Witch Hat"]
                }
            ]
        }

        # AnimePahe API search for Jikan alt title Tongari Boushi no Atelier
        mock_res_alt = MagicMock()
        mock_res_alt.status_code = 200
        mock_res_alt.json.return_value = {
            "data": [
                {"session": "tv_sess", "title": "Tongari Boushi no Atelier", "type": "TV"}
            ]
        }

        mock_client.get.side_effect = [
            mock_res_fail,       # Fail 1
            mock_res_movies_only, # Succeed retry
            mock_res_jikan,       # Jikan fetch
            mock_res_alt,         # Alt search 1 (Tongari Boushi no Atelier)
            mock_res_alt          # Alt search 2 (Witch Hat)
        ]

        # Query a series search "Witch Hat Atelier" (not a movie)
        # return_all = False
        anime_id, title, api_ok, dist = search_anime(mock_client, "Witch Hat Atelier", return_all=False)
        self.assertEqual(anime_id, "tv_sess")
        self.assertEqual(title, "Tongari Boushi no Atelier")
        self.assertTrue(api_ok)

    @patch("modules.scraper.ensure_working_jikan_mirror", return_value=True)
    def test_search_anime_return_all(self, mock_jikan):
        mock_client = MagicMock()
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "data": [
                {"session": "sess_1", "title": "Witch Hat Atelier", "type": "TV"}
            ]
        }
        mock_client.get.return_value = mock_res

        results, api_ok = search_anime(mock_client, "Witch Hat", return_all=True)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "sess_1")
        self.assertEqual(results[0][1], "Witch Hat Atelier")
        self.assertTrue(api_ok)

if __name__ == "__main__":
    unittest.main()
