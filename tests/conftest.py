import pytest
import tempfile
import os
import sys
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

@pytest.fixture(autouse=True, scope="session")
def isolate_test_logs():
    temp_dir = tempfile.mkdtemp(prefix="animepahe_test_logs_")
    orig_log = config.LOG_PATH
    config.LOG_PATH = os.path.join(temp_dir, "test_debug_log.txt")
    yield
    config.LOG_PATH = orig_log
    shutil.rmtree(temp_dir, ignore_errors=True)
