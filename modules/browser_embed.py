"""
Browser Embedding Module for AnimePahe Downloader

Provides Win32 HWND reparenting functionality to seamlessly dock undetected-chromedriver
(uc.Chrome) windows inside a Tkinter GUI Frame while keeping the Chrome browser engine 100% intact.
"""

import os
import sys
import time
import ctypes
import threading
from modules.utils import log_debug

EMBEDDED_CONTAINER_GETTER = None
CURRENT_CHROME_HWND = None
EMBEDDED_TAB_SELECT_CALLBACK = None
EMBEDDED_TAB_HIDE_CALLBACK = None

if sys.platform == 'win32':
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ('dwSize', ctypes.c_ulong),
            ('cntUsage', ctypes.c_ulong),
            ('th32ProcessID', ctypes.c_ulong),
            ('th32DefaultHeapID', ctypes.c_void_p),
            ('th32ModuleID', ctypes.c_ulong),
            ('cntThreads', ctypes.c_ulong),
            ('th32ParentProcessID', ctypes.c_ulong),
            ('pcPriClassBase', ctypes.c_long),
            ('dwFlags', ctypes.c_ulong),
            ('szExeFile', ctypes.c_wchar * 260)
        ]

    def get_child_pids(parent_pid):
        """Recursively retrieves all child process PIDs spawned by parent_pid."""
        child_pids = set()
        try:
            hSnapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
            if hSnapshot == -1:
                return child_pids
            pe32 = PROCESSENTRY32W()
            pe32.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if kernel32.Process32FirstW(hSnapshot, ctypes.byref(pe32)):
                while True:
                    if pe32.th32ParentProcessID == parent_pid:
                        child_pids.add(pe32.th32ProcessID)
                        child_pids.update(get_child_pids(pe32.th32ProcessID))
                    if not kernel32.Process32NextW(hSnapshot, ctypes.byref(pe32)):
                        break
            kernel32.CloseHandle(hSnapshot)
        except Exception as pe_err:
            log_debug(f"Error enumerating child PIDs: {pe_err}")
        return child_pids
else:
    user32 = None
    kernel32 = None
    def get_child_pids(parent_pid):
        return set()


def register_container_hwnd(container_getter, select_callback=None, hide_callback=None):
    """Registers the Tkinter container getter and tab-switch/hide callbacks."""
    global EMBEDDED_CONTAINER_GETTER, EMBEDDED_TAB_SELECT_CALLBACK, EMBEDDED_TAB_HIDE_CALLBACK
    EMBEDDED_CONTAINER_GETTER = container_getter
    EMBEDDED_TAB_SELECT_CALLBACK = select_callback
    EMBEDDED_TAB_HIDE_CALLBACK = hide_callback
    log_debug("Registered GUI browser container HWND provider.")


def get_container_hwnd():
    if EMBEDDED_CONTAINER_GETTER:
        try:
            hwnd, _, _ = EMBEDDED_CONTAINER_GETTER()
            return hwnd
        except Exception:
            pass
    return None


def get_existing_chrome_hwnds():
    """Returns set of all pre-existing Chrome_WidgetWin_1 HWNDs before driver launch."""
    if not user32:
        return set()
    hwnds = set()

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def enum_windows_callback(hwnd, lparam):
        cbuf = ctypes.create_unicode_buffer(512)
        user32.GetClassNameW(hwnd, cbuf, 512)
        if cbuf.value == 'Chrome_WidgetWin_1':
            hwnds.add(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)
    return hwnds


def find_chrome_hwnd(existing_hwnds=None, timeout=10):
    """Finds newly created top-level Chrome_WidgetWin_1 browser window HWND belonging to this process tree."""
    if not user32:
        return None

    if existing_hwnds is None:
        existing_hwnds = set()

    my_pids = get_child_pids(os.getpid())
    my_pids.add(os.getpid())

    start = time.time()
    found_hwnd = None

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def enum_windows_callback(hwnd, lparam):
        nonlocal found_hwnd
        if hwnd not in existing_hwnds:
            cbuf = ctypes.create_unicode_buffer(512)
            user32.GetClassNameW(hwnd, cbuf, 512)
            if cbuf.value == 'Chrome_WidgetWin_1':
                win_pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(win_pid))
                if not my_pids or win_pid.value in my_pids:
                    rect = (ctypes.c_long * 4)()
                    user32.GetWindowRect(hwnd, rect)
                    w = rect[2] - rect[0]
                    h = rect[3] - rect[1]
                    if w > 200 and h > 150:
                        found_hwnd = hwnd
                        return False
        return True

    cb = WNDENUMPROC(enum_windows_callback)

    while time.time() - start < timeout:
        found_hwnd = None
        my_pids = get_child_pids(os.getpid())
        my_pids.add(os.getpid())

        user32.EnumWindows(cb, 0)
        if found_hwnd:
            return found_hwnd
        time.sleep(0.3)

    return None


def embed_chrome_driver(driver, existing_hwnds=None, width=800, height=500):
    """Reparents uc.Chrome driver window into the registered GUI container HWND."""
    global CURRENT_CHROME_HWND
    if not user32 or not EMBEDDED_CONTAINER_GETTER:
        log_debug("Cannot embed Chrome driver: user32 or container getter missing.")
        return False

    try:
        # Unhide and switch GUI tab to Browser solver tab
        if EMBEDDED_TAB_SELECT_CALLBACK:
            try:
                EMBEDDED_TAB_SELECT_CALLBACK()
            except Exception as cb_err:
                log_debug(f"Failed to execute tab select callback: {cb_err}")

        time.sleep(0.5)

        container_info = EMBEDDED_CONTAINER_GETTER()
        if not container_info or not container_info[0]:
            log_debug("Container HWND unavailable.")
            return False

        container_hwnd, c_width, c_height = container_info
        if c_width > 100: width = c_width
        if c_height > 100: height = c_height

        chrome_hwnd = find_chrome_hwnd(existing_hwnds=existing_hwnds, timeout=8)
        if not chrome_hwnd:
            log_debug("Could not find new Chrome_WidgetWin_1 window HWND.")
            return False

        log_debug(f"Found new Chrome HWND: {chrome_hwnd}. Reparenting into container HWND {container_hwnd}...")

        GWL_STYLE = -16
        style = user32.GetWindowLongW(chrome_hwnd, GWL_STYLE)
        style &= ~(0x00C00000 | 0x00040000 | 0x80000000)
        style |= 0x40000000
        user32.SetWindowLongW(chrome_hwnd, GWL_STYLE, style)

        user32.SetParent(chrome_hwnd, container_hwnd)
        user32.MoveWindow(chrome_hwnd, 0, 0, width, height, True)
        user32.ShowWindow(chrome_hwnd, 5)

        CURRENT_CHROME_HWND = chrome_hwnd
        log_debug(f"Successfully embedded Chrome HWND {chrome_hwnd} into container HWND {container_hwnd}")
        return True
    except Exception as e:
        log_debug(f"Error embedding Chrome window: {e}")
        return False


def resize_current_embedded(width, height):
    """Resizes the embedded Chrome window to match GUI container frame bounds."""
    global CURRENT_CHROME_HWND
    if user32 and CURRENT_CHROME_HWND and user32.IsWindow(CURRENT_CHROME_HWND):
        try:
            user32.MoveWindow(CURRENT_CHROME_HWND, 0, 0, width, height, True)
        except Exception as e:
            log_debug(f"Error resizing embedded Chrome window: {e}")


def detach_current_embedded():
    """Clears current embedded Chrome HWND tracking on session completion and hides browser tab."""
    global CURRENT_CHROME_HWND
    CURRENT_CHROME_HWND = None
    if EMBEDDED_TAB_HIDE_CALLBACK:
        try:
            EMBEDDED_TAB_HIDE_CALLBACK()
        except Exception as e:
            log_debug(f"Failed to execute tab hide callback: {e}")
