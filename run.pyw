"""
AnimePahe Auto-Downloader GUI Launcher (Console-less)

This script launches the AnimePahe Downloader GUI directly without spawning
a console / command prompt window on Windows.
"""

import os
import sys
import io
from pathlib import Path

# When running under pythonw.exe on Windows, sys.stdout and sys.stderr are None.
# Provide dummy streams so libraries (e.g. tqdm, colorama, urllib3) don't crash.
class SafeStream(io.TextIOBase):
    def write(self, s):
        return len(s) if s else 0
    def flush(self):
        pass
    def isatty(self):
        return False

if sys.stdout is None:
    sys.stdout = SafeStream()
if sys.stderr is None:
    sys.stderr = SafeStream()
if sys.stdin is None:
    sys.stdin = io.StringIO()

# Ensure working directory and sys.path point to the application root directory
APP_DIR = Path(__file__).resolve().parent
os.chdir(APP_DIR)
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

if __name__ == "__main__":
    try:
        import gui
        gui.main()
    except Exception as e:
        import traceback
        import tkinter as tk
        from tkinter import messagebox

        error_msg = traceback.format_exc()

        # Log error details to debug_log.txt for troubleshooting
        try:
            log_path = APP_DIR / "debug_log.txt"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[CRASH] GUI Launcher error:\n{error_msg}\n")
        except Exception:
            pass

        # Display an error dialog to the user
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "AnimePahe Downloader - Startup Error",
                f"Failed to start AnimePahe GUI:\n\n{error_msg}",
            )
            root.destroy()
        except Exception:
            pass

        sys.exit(1)
