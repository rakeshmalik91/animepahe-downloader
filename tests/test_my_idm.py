import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import config
from modules import my_idm


class TestMyIDMIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.orig_my_idm_dir = getattr(config, "MY_IDM_DIR", r"D:\Projects\my-idm")
        self.orig_backlog_file = getattr(config, "MY_IDM_BACKLOG_FILE", "")
        self.orig_use_my_idm = getattr(config, "USE_MY_IDM", False)
        self.orig_auto_start = getattr(config, "AUTO_START_MY_IDM", True)
        self.orig_queue_name = getattr(config, "MY_IDM_QUEUE_NAME", "AnimePahe")

        config.MY_IDM_DIR = self.temp_dir.name
        config.MY_IDM_BACKLOG_FILE = ""
        config.MY_IDM_QUEUE_NAME = self.orig_queue_name
        my_idm.reset_pending_download_count()

        # Fail-safe: redirect the app-level backlog path to the temp dir so that
        # no test can ever write to the real user's ~/.my-idm/backlog.txt
        self._app_backlog_patch = patch("modules.my_idm.get_default_app_backlog_path")
        self._mock_app_backlog = self._app_backlog_patch.start()
        self._mock_app_backlog.return_value = os.path.join(self.temp_dir.name, "app_backlog.txt")

    def tearDown(self):
        self._app_backlog_patch.stop()
        config.MY_IDM_DIR = self.orig_my_idm_dir
        config.MY_IDM_BACKLOG_FILE = self.orig_backlog_file
        config.USE_MY_IDM = self.orig_use_my_idm
        config.AUTO_START_MY_IDM = self.orig_auto_start
        config.MY_IDM_QUEUE_NAME = self.orig_queue_name
        my_idm.reset_pending_download_count()
        self.temp_dir.cleanup()

    def test_get_my_idm_dir(self):
        expected = os.path.abspath(self.temp_dir.name)
        self.assertEqual(my_idm.get_my_idm_dir(), expected)

    def test_get_my_idm_backlog_path_default(self):
        expected = os.path.join(os.path.abspath(self.temp_dir.name), "backlog.txt")
        self.assertEqual(my_idm.get_my_idm_backlog_path(), expected)

    def test_get_my_idm_backlog_path_custom(self):
        custom_backlog = os.path.join(self.temp_dir.name, "custom_backlog.txt")
        config.MY_IDM_BACKLOG_FILE = custom_backlog
        self.assertEqual(my_idm.get_my_idm_backlog_path(), os.path.abspath(custom_backlog))

    def test_add_to_my_idm_backlog_and_deduplication(self):
        url = "https://vault-123.owocdn.top/stream/anime_ep1.mp4"
        title = "Frieren: Beyond Journey's End"
        ep_num = 1
        filename = "Frieren_01_1080p.mp4"

        # First add: should return True
        added = my_idm.add_to_my_idm_backlog(url, filename=filename, title=title, ep_num=ep_num)
        self.assertTrue(added)

        backlog_path = my_idm.get_my_idm_backlog_path()
        self.assertTrue(os.path.exists(backlog_path))
        with open(backlog_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn(url, content)
        self.assertIn("Frieren: Beyond Journey's End - Episode 1 (Frieren_01_1080p.mp4)", content)

        # Second add of the same URL: should return False (deduplication)
        added_again = my_idm.add_to_my_idm_backlog(url, filename=filename, title=title, ep_num=ep_num)
        self.assertFalse(added_again)

    def test_add_to_my_idm_backlog_with_save_path(self):
        url = "https://vault-456.owocdn.top/stream/anime_ep2.mp4"
        title = "One Piece"
        ep_num = 1122
        filename = "One_Piece_1122.mp4"
        save_path = r"D:\Downloads\ANIME\One Piece [2024]"

        added = my_idm.add_to_my_idm_backlog(url, filename=filename, title=title, ep_num=ep_num, save_path=save_path)
        self.assertTrue(added)

        backlog_path = my_idm.get_my_idm_backlog_path()
        with open(backlog_path, "r", encoding="utf-8") as f:
            content = f.read()

        expected_line = f"{url} | {save_path}"
        self.assertIn(expected_line, content)

        # Deduplication check when save_path is present
        added_again = my_idm.add_to_my_idm_backlog(url, filename=filename, title=title, ep_num=ep_num, save_path=save_path)
        self.assertFalse(added_again)

    def test_add_empty_url(self):
        self.assertFalse(my_idm.add_to_my_idm_backlog(""))
        self.assertFalse(my_idm.add_to_my_idm_backlog("   "))

    def _backlog_download_lines(self):
        path = my_idm.get_my_idm_backlog_path()
        with open(path, "r", encoding="utf-8") as handle:
            return [
                line.strip() for line in handle
                if line.strip() and not line.strip().startswith("#")
            ]

    def test_entries_are_tagged_with_the_configured_queue(self):
        """The queue column is what routes the entry in My-IDM.

        My-IDM already infers an AnimePahe queue from the "# AnimePahe Download" comment, so
        this is belt-and-braces - but it makes the generated backlog self-describing, which
        matters because the comment is optional and only present on some lines.
        """
        my_idm.add_to_my_idm_backlog(
            "https://vault-900.owocdn.top/a.mkv", filename="a.mkv"
        )
        lines = self._backlog_download_lines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith("| queue=AnimePahe"), lines[0])

    def test_the_queue_column_comes_last_in_every_line_shape(self):
        """It must never be mistaken for a path or a filename.

        My-IDM also accepts positional columns, so `queue=` has to be the trailing one or a
        queue name containing a space would be read as a save path.
        """
        save_path = r"D:\Downloads\ANIME\One Piece"
        shapes = [
            my_idm.add_to_my_idm_backlog("https://vault-901.owocdn.top/a.mkv"),
            my_idm.add_to_my_idm_backlog(
                "https://vault-902.owocdn.top/b.mkv", filename="b.mkv"
            ),
            my_idm.add_to_my_idm_backlog(
                "https://vault-903.owocdn.top/c.mkv", save_path=save_path
            ),
            my_idm.add_to_my_idm_backlog(
                "https://vault-904.owocdn.top/d.mkv", save_path=save_path, filename="d.mkv"
            ),
        ]
        self.assertTrue(all(shapes))
        for line in self._backlog_download_lines():
            self.assertTrue(line.endswith("| queue=AnimePahe"), line)
            # The URL stays the first column, which is what the dedup scan compares on.
            self.assertTrue(line.startswith("https://"), line)

    def test_the_queue_column_does_not_break_deduplication(self):
        url = "https://vault-905.owocdn.top/e.mkv"
        self.assertTrue(my_idm.add_to_my_idm_backlog(url))
        self.assertFalse(my_idm.add_to_my_idm_backlog(url))
        self.assertEqual(len(self._backlog_download_lines()), 1)

    def test_an_explicit_queue_overrides_the_configured_one(self):
        my_idm.add_to_my_idm_backlog(
            "https://vault-906.owocdn.top/f.mkv", queue="Hand Picked"
        )
        self.assertTrue(
            self._backlog_download_lines()[0].endswith("| queue=Hand Picked")
        )

    def test_an_empty_queue_name_omits_the_column(self):
        my_idm.add_to_my_idm_backlog(
            "https://vault-907.owocdn.top/g.mkv", queue=""
        )
        line = self._backlog_download_lines()[0]
        self.assertNotIn("queue=", line)
        self.assertEqual(line, "https://vault-907.owocdn.top/g.mkv")

    def test_a_blank_configured_queue_name_omits_the_column(self):
        config.MY_IDM_QUEUE_NAME = ""
        my_idm.add_to_my_idm_backlog("https://vault-908.owocdn.top/h.mkv")
        self.assertNotIn("queue=", self._backlog_download_lines()[0])

    def test_a_queue_name_with_spaces_survives_intact(self):
        my_idm.add_to_my_idm_backlog(
            "https://vault-909.owocdn.top/i.mkv", queue="Long Running Anime"
        )
        self.assertTrue(
            self._backlog_download_lines()[0].endswith("| queue=Long Running Anime")
        )

    @patch("psutil.process_iter")
    def test_is_my_idm_running_true(self, mock_iter):
        proc = MagicMock()
        proc.info = {"pid": 1234, "name": "python.exe", "cmdline": ["python", "-m", "my_idm.main"]}
        mock_iter.return_value = [proc]

        self.assertTrue(my_idm.is_my_idm_running())

    @patch("psutil.process_iter")
    def test_is_my_idm_running_false(self, mock_iter):
        proc = MagicMock()
        proc.info = {"pid": 5678, "name": "notepad.exe", "cmdline": ["notepad.exe"]}
        mock_iter.return_value = [proc]

        self.assertFalse(my_idm.is_my_idm_running())

    @patch("psutil.process_iter")
    def test_is_my_idm_running_ignores_workspace_id_in_other_processes(self, mock_iter):
        proc = MagicMock()
        proc.info = {
            "pid": 6300,
            "name": "language_server_windows_x64.exe",
            "cmdline": ["language_server.exe", "--workspace_id", "file_d_3A_Projects_my_idm"]
        }
        mock_iter.return_value = [proc]

        self.assertFalse(my_idm.is_my_idm_running())

    def test_launch_my_idm_invalid_directory(self):
        invalid_dir = os.path.join(self.temp_dir.name, "non_existent_dir_123")
        success, msg = my_idm.launch_my_idm(my_idm_dir=invalid_dir)
        self.assertFalse(success)
        self.assertIn("does not exist", msg)

    @patch("modules.my_idm.is_my_idm_running", return_value=True)
    def test_launch_my_idm_already_running(self, mock_running):
        success, msg = my_idm.launch_my_idm(my_idm_dir=self.temp_dir.name)
        self.assertTrue(success)
        self.assertIn("already running", msg)

    @patch("modules.my_idm.is_my_idm_running", return_value=True)
    @patch("subprocess.Popen")
    def test_launch_my_idm_force_bypasses_running_check(self, mock_popen, mock_running):
        success, msg = my_idm.launch_my_idm(my_idm_dir=self.temp_dir.name, force=True)
        self.assertTrue(success)
        self.assertTrue(mock_popen.called)

    @patch("modules.my_idm.is_my_idm_running", return_value=False)
    @patch("subprocess.Popen")
    def test_launch_my_idm_with_backlog_file(self, mock_popen, mock_running):
        backlog_file = os.path.join(self.temp_dir.name, "backlog.txt")
        with open(backlog_file, "w") as f:
            f.write("https://example.com/stream.mp4\n")

        success, msg = my_idm.launch_my_idm(my_idm_dir=self.temp_dir.name, backlog_file=backlog_file)
        self.assertTrue(success)
        self.assertTrue(mock_popen.called)
        call_args = mock_popen.call_args[0][0]
        # Should include --backlog
        self.assertTrue(any("--backlog" in str(arg) for arg in call_args))

    def test_pending_count_increments_on_add(self):
        self.assertEqual(my_idm.get_pending_download_count(), 0)

        my_idm.add_to_my_idm_backlog(
            "https://vault-1.owocdn.top/stream/ep1.mp4",
            filename="Ep01.mp4", title="Test", ep_num=1,
        )
        self.assertEqual(my_idm.get_pending_download_count(), 1)

    def test_pending_count_does_not_increment_on_duplicate(self):
        url = "https://vault-2.owocdn.top/stream/ep2.mp4"
        my_idm.add_to_my_idm_backlog(url, filename="Ep02.mp4", title="Test", ep_num=2)
        self.assertEqual(my_idm.get_pending_download_count(), 1)

        # Duplicate URL should not increment
        again = my_idm.add_to_my_idm_backlog(url, filename="Ep02.mp4", title="Test", ep_num=2)
        self.assertFalse(again)
        self.assertEqual(my_idm.get_pending_download_count(), 1)

    def test_pending_count_resets_to_zero(self):
        my_idm.reset_pending_download_count()
        self.assertEqual(my_idm.get_pending_download_count(), 0)


if __name__ == "__main__":
    unittest.main()
