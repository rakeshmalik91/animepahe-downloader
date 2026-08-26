import pytest
import os
import sys
import tkinter as tk
from tkinter import ttk
from unittest.mock import MagicMock

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui


@pytest.fixture
def tk_root():
    root = None
    try:
        root = tk.Tk()
        root.withdraw()
    except Exception:
        root = None
    yield root
    if root:
        try:
            root.update_idletasks()
            root.destroy()
        except Exception:
            pass


def _create_mock_gui(root):
    app = MagicMock(spec=gui.AnimePaheGUI)
    if root:
        app.root = root
        app.progress_frame = ttk.Frame(root)
        app.text_frame = ttk.Frame(root)
        app.text_frame.pack(fill=tk.BOTH, expand=True)
    else:
        app.root = MagicMock()
        app.progress_frame = MagicMock()
        app.text_frame = MagicMock()
    app.active_progress_bars = {}
    # Bind actual methods to test
    app.update_progress = gui.AnimePaheGUI.update_progress.__get__(app, gui.AnimePaheGUI)
    app.remove_progress_bar = gui.AnimePaheGUI.remove_progress_bar.__get__(app, gui.AnimePaheGUI)
    app.clear_all_progress_bars = gui.AnimePaheGUI.clear_all_progress_bars.__get__(app, gui.AnimePaheGUI)
    app.append_log = gui.AnimePaheGUI.append_log.__get__(app, gui.AnimePaheGUI)
    return app


def test_switch_from_segmented_to_non_segmented(tk_root):
    app = _create_mock_gui(tk_root)
    
    # Segmented download progress arrives
    app.append_log("    [Seg] Bleach_TYBW_01.mp4:  20%|██        | 20.0M/100M [00:02<00:08, 10.0MiB/s]\r")
    assert len(app.active_progress_bars) == 1
    assert "Bleach_TYBW_01.mp4" in app.active_progress_bars
    item = app.active_progress_bars["Bleach_TYBW_01.mp4"]
    if tk_root:
        assert float(item["bar"]["value"]) == 20.0
        assert "[Seg] Bleach_TYBW_01.mp4 (20%)" in item["lbl_title"].cget("text")

    # Download switches to non-segmented fallback
    app.append_log("    Bleach_TYBW_01.mp4:  35%|███▌      | 35.0M/100M [00:03<00:06, 10.0MiB/s]\r")
    # Ensure still only 1 progress bar exists and it updated in-place
    assert len(app.active_progress_bars) == 1
    assert "Bleach_TYBW_01.mp4" in app.active_progress_bars
    if tk_root:
        assert float(item["bar"]["value"]) == 35.0
        assert item["lbl_title"].cget("text") == "Bleach_TYBW_01.mp4 (35%)"


def test_switch_from_non_segmented_to_segmented(tk_root):
    app = _create_mock_gui(tk_root)
    
    # Non-segmented download progress arrives
    app.append_log("    Naruto_01.mp4:  15%|█▌        | 15.0M/100M [00:01<00:05, 15.0MiB/s]\r")
    assert len(app.active_progress_bars) == 1
    assert "Naruto_01.mp4" in app.active_progress_bars
    item = app.active_progress_bars["Naruto_01.mp4"]
    if tk_root:
        assert float(item["bar"]["value"]) == 15.0

    # Switch to segmented
    app.append_log("    [Seg] Naruto_01.mp4:  50%|█████     | 50.0M/100M [00:03<00:03, 15.0MiB/s]\r")
    assert len(app.active_progress_bars) == 1
    if tk_root:
        assert float(item["bar"]["value"]) == 50.0
        assert "[Seg] Naruto_01.mp4 (50%)" in item["lbl_title"].cget("text")


def test_concurrent_downloads_tracked_separately(tk_root):
    app = _create_mock_gui(tk_root)
    
    app.append_log("    [Seg] Ep1.mp4:  20%|██        | 20M/100M [00:01<00:04, 20MiB/s]\r")
    app.append_log("    Ep2.mp4:  40%|████      | 40M/100M [00:02<00:03, 20MiB/s]\r")
    
    assert len(app.active_progress_bars) == 2
    assert "Ep1.mp4" in app.active_progress_bars
    assert "Ep2.mp4" in app.active_progress_bars
    if tk_root:
        assert float(app.active_progress_bars["Ep1.mp4"]["bar"]["value"]) == 20.0
        assert float(app.active_progress_bars["Ep2.mp4"]["bar"]["value"]) == 40.0
