import unittest
from unittest.mock import patch, MagicMock, mock_open
import os
import sys
import threading
import time

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from modules.downloader import download_file, _segmented_download

class TestDownloader(unittest.TestCase):
    
    def setUp(self):
        self.scratch_dir = os.path.dirname(os.path.abspath(__file__))
        self.test_dir = os.path.join(self.scratch_dir, "test_downloader_files")
        if not os.path.exists(self.test_dir):
            os.makedirs(self.test_dir)
        
        # Configure defaults
        config.ENABLE_SEGMENTED_DOWNLOAD = True
        config.DOWNLOAD_SEGMENTS = 2
        config.SLOW_DOWNLOAD_THRESHOLD_KBPS = 500

    def tearDown(self):
        import shutil
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    @patch("os.path.exists", return_value=True)
    @patch("os.path.getsize", return_value=1000)
    @patch("modules.downloader.requests.Session")
    def test_download_file_already_exists(self, mock_session, mock_getsize, mock_exists):
        # Mocks check that if file exists and has size == total_size (returned from header)
        mock_client = mock_session.return_value
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.headers = {'content-length': '1000', 'accept-ranges': 'bytes'}
        mock_client.get.return_value = mock_res
        
        filename = os.path.join(self.test_dir, "exists.mp4")
        res = download_file("http://mockurl.mp4", filename, "referer")
        self.assertTrue(res)

    @patch("modules.db.get_kwik_session")
    @patch("modules.downloader.requests.Session")
    def test_segmented_download_success(self, mock_session_class, mock_get_kwik):
        mock_get_kwik.return_value = ([{"name": "c", "value": "v", "domain": "domain"}], "ua")
        
        mock_sess = MagicMock()
        mock_session_class.return_value = mock_sess
        
        # Mock responses for segments
        mock_res = MagicMock()
        mock_res.status_code = 206
        mock_res.iter_content.side_effect = lambda *args, **kwargs: [b"chunk1", b"chunk2"]
        mock_sess.get.return_value = mock_res
        
        # Test direct segmented download
        filename = os.path.join(self.test_dir, "segmented_success.mp4")
        
        # Mock initial head/get check to retrieve size
        mock_head_res = MagicMock()
        mock_head_res.status_code = 200
        mock_head_res.headers = {'content-length': '2000000', 'accept-ranges': 'bytes'}
        
        # We need mock_sess.get to return mock_head_res on first call, then mock_res on subsequent calls
        mock_sess.get.side_effect = [mock_head_res, mock_res, mock_res]
        
        success = download_file("https://kwik.cx/file", filename, "referer")
        self.assertTrue(success)
        self.assertTrue(os.path.exists(filename))
        self.assertEqual(os.path.getsize(filename), 24)  # chunk1 (6) + chunk2 (6) * 2 segments = 24 bytes

    @patch("modules.db.get_kwik_session")
    @patch("modules.downloader.requests.Session")
    def test_segmented_download_slow_abort(self, mock_session_class, mock_get_kwik):
        # We want to trigger speed check and abort.
        # Speed state checks speed after 10 seconds.
        # We mock time.time() to advance by 15 seconds, and speed_state['downloaded'] is small.
        mock_get_kwik.return_value = ({'cookie_name': 'cookie_val'}, "ua") # Test dict style cookies
        
        mock_sess = MagicMock()
        mock_session_class.return_value = mock_sess
        
        # Mock responses
        mock_head_res = MagicMock()
        mock_head_res.status_code = 200
        mock_head_res.headers = {'content-length': '2000000', 'accept-ranges': 'bytes'}
        
        mock_res = MagicMock()
        mock_res.status_code = 206
        
        # A slow generator for iter_content that blocks until event is set
        event = threading.Event()
        def slow_chunks(chunk_size):
            yield b"a"
            event.wait(timeout=0.1) # block briefly or until aborted
            yield b"b"
            
        mock_res.iter_content.side_effect = slow_chunks
        
        mock_sess.get.side_effect = [mock_head_res, mock_res, mock_res, Exception("Fallback failed")]
        
        # Mock time.time to simulate passage of 15 seconds inside _segmented_download
        start_time = time.time()
        time_side_effects = [start_time]
        for i in range(100):
            time_side_effects.append(start_time + 15 + i)
            
        filename = os.path.join(self.test_dir, "segmented_slow.mp4")
        
        # Run in a thread or patch time.time
        with patch("time.time", side_effect=time_side_effects):
            # We mock time.sleep so we don't actually sleep
            with patch("time.sleep") as mock_sleep:
                mock_sleep.side_effect = lambda x: event.set()
                success = download_file("https://kwik.cx/file", filename, "referer")
                
        # Since segmented failed due to slow, it fell back to normal download which failed too (since no more mock side effects)
        self.assertFalse(success)

    @patch("modules.downloader.requests.Session")
    def test_segmented_download_thread_exception(self, mock_session_class):
        mock_sess = MagicMock()
        mock_session_class.return_value = mock_sess
        
        mock_head_res = MagicMock()
        mock_head_res.status_code = 200
        mock_head_res.headers = {'content-length': '2000000', 'accept-ranges': 'bytes'}
        
        # Trigger exception inside get for segments
        mock_sess.get.side_effect = [mock_head_res, Exception("Connection aborted")]
        
        filename = os.path.join(self.test_dir, "segmented_fail.mp4")
        success = download_file("https://kwik.cx/file", filename, "referer")
        self.assertFalse(success)

    @patch("modules.db.get_kwik_session")
    @patch("modules.downloader.requests.Session")
    def test_segmented_download_resume(self, mock_session_class, mock_get_kwik):
        # Test resuming segmented download when one part is already fully downloaded
        mock_get_kwik.return_value = (None, None)
        mock_sess = MagicMock()
        mock_session_class.return_value = mock_sess
        
        # We have 2 segments, part size = 1,000,000 bytes.
        # Create part0 already fully downloaded (1,000,000 bytes)
        filename = os.path.join(self.test_dir, "segmented_resume.mp4")
        part0 = f"{filename}.part0"
        with open(part0, 'wb') as f:
            f.write(b"0" * 1000000)
            
        mock_head_res = MagicMock()
        mock_head_res.status_code = 200
        mock_head_res.headers = {'content-length': '2000000', 'accept-ranges': 'bytes'}
        
        # Segment 1 gets downloaded
        mock_res = MagicMock()
        mock_res.status_code = 206
        mock_res.iter_content.side_effect = lambda *args, **kwargs: [b"resumedpart"]
        
        mock_sess.get.side_effect = [mock_head_res, mock_res]
        
        success = download_file("https://kwik.cx/file", filename, "referer")
        self.assertTrue(success)
        self.assertTrue(os.path.exists(filename))
        # Total size: part0 (1000000) + part1 (11) = 1000011
        self.assertEqual(os.path.getsize(filename), 1000011)

    @patch("concurrent.futures.ThreadPoolExecutor", side_effect=Exception("Pool construction error"))
    @patch("modules.downloader.requests.Session")
    def test_segmented_download_coordinator_exception(self, mock_session_class, mock_pool):
        mock_sess = MagicMock()
        mock_session_class.return_value = mock_sess
        mock_head_res = MagicMock()
        mock_head_res.status_code = 200
        mock_head_res.headers = {'content-length': '2000000', 'accept-ranges': 'bytes'}
        mock_sess.get.side_effect = [mock_head_res, Exception("Fallback failed")]
        
        filename = os.path.join(self.test_dir, "segmented_coord_fail.mp4")
        success = download_file("https://kwik.cx/file", filename, "referer")
        self.assertFalse(success)

    @patch("concurrent.futures.ThreadPoolExecutor", side_effect=Exception("Pool construction error"))
    @patch("os.remove", side_effect=OSError("Permission denied"))
    @patch("modules.downloader.requests.Session")
    def test_segmented_download_cleanup_remove_exception(self, mock_session_class, mock_remove, mock_pool):
        # Trigger cleanup where os.remove throws exception to cover line 143
        mock_sess = MagicMock()
        mock_session_class.return_value = mock_sess
        mock_head_res = MagicMock()
        mock_head_res.status_code = 200
        mock_head_res.headers = {'content-length': '2000000', 'accept-ranges': 'bytes'}
        mock_sess.get.side_effect = [mock_head_res, Exception("Fallback failed")]
        
        filename = os.path.join(self.test_dir, "segmented_cleanup_fail.mp4")
        # Pre-create part0 file so os.remove is called on it
        part0 = f"{filename}.part0"
        with open(part0, 'w') as f:
            f.write("dummy")
            
        success = download_file("https://kwik.cx/file", filename, "referer")
        self.assertFalse(success)

    @patch("modules.downloader.requests.Session")
    def test_normal_download_success(self, mock_session_class):
        config.ENABLE_SEGMENTED_DOWNLOAD = False
        
        mock_sess = MagicMock()
        mock_session_class.return_value = mock_sess
        
        mock_head_res = MagicMock()
        mock_head_res.status_code = 200
        mock_head_res.headers = {'content-length': '100', 'accept-ranges': 'bytes'}
        
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.iter_content.return_value = [b"normal1", b"normal2"]
        
        mock_sess.get.side_effect = [mock_head_res, mock_res]
        
        filename = os.path.join(self.test_dir, "normal_success.mp4")
        success = download_file("https://kwik.cx/file", filename, "referer")
        self.assertTrue(success)
        self.assertTrue(os.path.exists(filename))
        self.assertEqual(os.path.getsize(filename), 14)

    @patch("modules.downloader.requests.Session")
    @patch("os.path.exists", return_value=True)
    @patch("os.path.getsize", return_value=20)
    def test_normal_download_resume(self, mock_getsize, mock_exists, mock_session_class):
        # Test resuming a download using Range bytes
        config.ENABLE_SEGMENTED_DOWNLOAD = False
        
        mock_sess = MagicMock()
        mock_session_class.return_value = mock_sess
        
        mock_head_res = MagicMock()
        mock_head_res.status_code = 200
        mock_head_res.headers = {'content-length': '100', 'accept-ranges': 'bytes'}
        
        mock_res = MagicMock()
        mock_res.status_code = 206 # Partial Content
        mock_res.iter_content.return_value = [b"resumed"]
        
        mock_sess.get.side_effect = [mock_head_res, mock_res]
        
        filename = os.path.join(self.test_dir, "normal_resume.mp4")
        
        # Mock open to verify we append to the file
        m_open = mock_open()
        m_open.return_value.write.side_effect = lambda chunk: len(chunk)
        with patch("builtins.open", m_open):
            success = download_file("https://kwik.cx/file", filename, "referer")
            self.assertTrue(success)
            m_open.assert_called_with(filename, "ab")

    @patch("modules.downloader.requests.Session")
    def test_download_file_head_error(self, mock_session_class):
        mock_sess = MagicMock()
        mock_session_class.return_value = mock_sess
        
        mock_head_res = MagicMock()
        mock_head_res.status_code = 404
        mock_head_res.text = "<html><title>Not Found</title></html>"
        mock_sess.get.return_value = mock_head_res
        
        filename = os.path.join(self.test_dir, "notfound.mp4")
        success = download_file("https://kwik.cx/file", filename, "referer")
        self.assertFalse(success)

if __name__ == "__main__":
    unittest.main()
