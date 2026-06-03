import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import sqlite3
import json

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from modules.db import (
    init_db,
    get_tracked,
    save_tracked,
    update_last_checked,
    cleanup_db,
    get_folder_by_id,
    get_kwik_session,
    save_kwik_session,
    get_last_working_mirror,
    save_working_mirror
)

class TestDB(unittest.TestCase):
    
    def setUp(self):
        # Set database path to a test database in scratch
        self.scratch_dir = os.path.dirname(os.path.abspath(__file__))
        self.test_db = os.path.join(self.scratch_dir, "test_tracking.db")
        config.DB_PATH = self.test_db
        config.BASE_DOWNLOAD_DIR = os.path.join(self.scratch_dir, "test_downloads")
        if os.path.exists(self.test_db):
            os.remove(self.test_db)
        init_db()

    def tearDown(self):
        # Clean up database file
        if os.path.exists(self.test_db):
            try:
                os.remove(self.test_db)
            except:
                pass

    def test_init_db(self):
        # Already ran in setUp, verify tables exist
        conn = sqlite3.connect(self.test_db)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in c.fetchall()]
        conn.close()
        self.assertIn("tracking", tables)
        self.assertIn("sessions", tables)
        self.assertIn("mirrors", tables)

    def test_get_tracked_direct_match(self):
        folder = r"D:\downloads\anime\series1"
        save_tracked(folder, "id123", "Title 1", True)
        res = get_tracked(folder)
        self.assertIsNotNone(res)
        self.assertEqual(res[0], "id123")
        self.assertEqual(res[1], "Title 1")
        self.assertEqual(res[2], 1)

    def test_get_tracked_fuzzy_match(self):
        # Test folder with different slashes / casing / colons
        folder_db = r"D:\downloads\anime\Series：Name"
        folder_query = r"d:/downloads/anime/series:name"
        save_tracked(folder_db, "id456", "Title 2", False)
        
        res = get_tracked(folder_query)
        self.assertIsNotNone(res)
        self.assertEqual(res[0], "id456")
        self.assertEqual(res[2], 0)

    def test_get_tracked_ancestor_match(self):
        # Parent folder: test_downloads\ParentDir
        # Child folder: test_downloads\ParentDir\SubDir
        parent = os.path.join(config.BASE_DOWNLOAD_DIR, "ParentDir")
        child = os.path.join(parent, "SubDir")
        
        save_tracked(parent, "id789", "Parent Title", True)
        res = get_tracked(child)
        self.assertIsNotNone(res)
        self.assertEqual(res[0], "id789")

    def test_get_tracked_ancestor_root_reached(self):
        config.BASE_DOWNLOAD_DIR = "C:\\"
        res = get_tracked("D:\\SubFolder\\AnotherSub")
        self.assertIsNone(res)

    def test_get_tracked_no_match(self):
        res = get_tracked(r"D:\nonexistent")
        self.assertIsNone(res)

    @patch("sqlite3.connect", side_effect=sqlite3.Error("Connection failed"))
    def test_get_tracked_exception(self, mock_connect):
        res = get_tracked("dummy")
        self.assertIsNone(res)

    def test_update_last_checked(self):
        folder = r"D:\downloads\anime\series3"
        save_tracked(folder, "id999", "Title 3", True, update_time=False)
        
        # Verify last_updated is None
        res = get_tracked(folder)
        self.assertIsNone(res[3])
        
        # Update last checked
        update_last_checked(folder)
        res = get_tracked(folder)
        self.assertIsNotNone(res[3])

    @patch("os.path.exists")
    def test_cleanup_db(self, mock_exists):
        folder1 = os.path.join(config.BASE_DOWNLOAD_DIR, "exist")
        folder2 = os.path.join(config.BASE_DOWNLOAD_DIR, "nonexist")
        
        save_tracked(folder1, "id1", "Exist", True)
        save_tracked(folder2, "id2", "NonExist", True)
        
        # mock_exists returns True for folder1 and False for folder2
        def side_effect(path):
            return path == folder1
        mock_exists.side_effect = side_effect
        
        cleanup_db()
        
        # Check folder1 is still tracked, folder2 is deleted
        self.assertIsNotNone(get_tracked(folder1))
        self.assertIsNone(get_tracked(folder2))

    @patch("sqlite3.connect", side_effect=Exception("Database error"))
    def test_cleanup_db_exception(self, mock_connect):
        # Should not raise exception
        cleanup_db()

    @patch("os.path.exists")
    def test_get_folder_by_id(self, mock_exists):
        folder = r"D:\downloads\anime\series4"
        save_tracked(folder, "id_folder", "Title 4", True)
        
        # When folder exists
        mock_exists.return_value = True
        self.assertEqual(get_folder_by_id("id_folder"), folder)
        
        # When folder does not exist
        mock_exists.return_value = False
        self.assertIsNone(get_folder_by_id("id_folder"))
        
        # Nonexistent ID
        self.assertIsNone(get_folder_by_id("nonexistent_id"))

    def test_get_folder_by_id_invalid(self):
        self.assertIsNone(get_folder_by_id(None))

    @patch("sqlite3.connect", side_effect=Exception("DB Error"))
    def test_get_folder_by_id_exception(self, mock_connect):
        self.assertIsNone(get_folder_by_id("id"))

    def test_get_save_kwik_session(self):
        cookies = [{"name": "cf_clearance", "value": "xyz", "domain": "kwik.cx"}]
        ua = "Chrome User Agent"
        
        self.assertTrue(save_kwik_session(cookies, ua))
        
        c_list, c_ua = get_kwik_session()
        self.assertEqual(c_list, cookies)
        self.assertEqual(c_ua, ua)

    def test_get_kwik_session_empty(self):
        c_list, c_ua = get_kwik_session()
        self.assertIsNone(c_list)
        self.assertIsNone(c_ua)

    @patch("sqlite3.connect", side_effect=sqlite3.Error("Failed"))
    def test_get_kwik_session_exception(self, mock_connect):
        c_list, c_ua = get_kwik_session()
        self.assertIsNone(c_list)
        self.assertIsNone(c_ua)

    @patch("sqlite3.connect", side_effect=sqlite3.Error("Failed"))
    def test_save_kwik_session_exception(self, mock_connect):
        self.assertFalse(save_kwik_session({}, "ua"))

    def test_get_save_working_mirror(self):
        site_type = "animepahe"
        url = "https://animepahe.si"
        
        self.assertTrue(save_working_mirror(site_type, url))
        self.assertEqual(get_last_working_mirror(site_type), url)

    def test_get_last_working_mirror_empty(self):
        self.assertIsNone(get_last_working_mirror("kwik"))

    @patch("sqlite3.connect", side_effect=sqlite3.Error("Failed"))
    def test_get_last_working_mirror_exception(self, mock_connect):
        self.assertIsNone(get_last_working_mirror("animepahe"))

    @patch("sqlite3.connect", side_effect=sqlite3.Error("Failed"))
    def test_save_working_mirror_exception(self, mock_connect):
        self.assertFalse(save_working_mirror("animepahe", "url"))

if __name__ == "__main__":
    unittest.main()
