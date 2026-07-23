"""
AnimePahe Auto-Downloader GUI Interface

A modern, responsive desktop interface for animepahe-downloader using Tkinter & TTK.
Can be launched directly using:
    python gui.py
Or via the downloader CLI:
    python animepahe_download.py --gui
"""

import os
import sys
import re
import io
import time
import queue
import sqlite3
import threading
import subprocess
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from datetime import datetime

# Local imports
import config
from modules.db import init_db, get_tracked, save_tracked, save_setting, get_setting, clear_sessions
from modules.utils import set_prompt_handler, log_debug, ensure_working_mirror, ensure_working_kwik_mirror, ensure_working_jikan_mirror, ensure_working_anilist_mirror, ensure_working_kitsu_mirror
from modules.browser_embed import register_container_hwnd, resize_current_embedded
import animepahe_download


class GUIStreamRedirector(io.TextIOBase):
    """Thread-safe stream redirector to capture stdout/stderr for the GUI log window."""
    def __init__(self, log_queue, original_stream):
        super().__init__()
        self.log_queue = log_queue
        self.original_stream = original_stream

    def write(self, s):
        if s:
            self.log_queue.put(s)
            try:
                self.original_stream.write(s)
                self.original_stream.flush()
            except Exception:
                pass
        return len(s)

    def flush(self):
        try:
            self.original_stream.flush()
        except Exception:
            pass

def create_external_link_icon(parent, bg_color, fg_color="#89b4fa", hover_color="#89dceb", command=None):
    """Draws a base folder button using the '⧉' icon with interactive hover states."""
    cv = tk.Canvas(parent, width=16, height=16, bg=bg_color, highlightthickness=0, cursor="hand2")

    def draw(color):
        cv.delete("all")
        cv.create_text(8, 8, text="⧉", fill=color, font=("Segoe UI Symbol", 10))

    draw(fg_color)

    if command:
        cv.bind("<Button-1>", lambda e: command())
        cv.bind("<Enter>", lambda e: draw(hover_color))
        cv.bind("<Leave>", lambda e: draw(fg_color))

    return cv


class QueueToolTip:
    """Hover tooltip for displaying active and queued task items."""
    def __init__(self, widget, get_content_func):
        self.widget = widget
        self.get_content_func = get_content_func
        self.tip_window = None
        self.widget.bind("<Enter>", self.show_tip)
        self.widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event=None):
        content = self.get_content_func()
        if not content:
            return
        if self.tip_window:
            self.hide_tip()

        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5

        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        try:
            tw.attributes("-topmost", True)
        except Exception:
            pass

        frame = tk.Frame(tw, background="#181825", highlightbackground="#45475a", highlightthickness=1)
        frame.pack(fill=tk.BOTH, expand=True)

        label = tk.Label(
            frame,
            text=content,
            justify=tk.LEFT,
            background="#181825",
            foreground="#cdd6f4",
            font=("Segoe UI", 9),
            padx=10,
            pady=8,
            wraplength=600
        )
        label.pack()

    def hide_tip(self, event=None):
        if self.tip_window:
            try:
                self.tip_window.destroy()
            except Exception:
                pass
            self.tip_window = None


class AnimePaheGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("AnimePahe Auto-Downloader")

        # Initialize DB
        init_db()

        self.root.minsize(920, 720)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Thread management & state
        self.running_thread = None
        self.is_cancelled = False
        self.log_queue = queue.Queue()
        self.task_queue = queue.Queue()
        self.current_task_label = None
        self.prompt_queue = queue.Queue()
        self.prompt_result_event = threading.Event()
        self.prompt_response = None

        # Redirect stdout and stderr
        self.orig_stdout = sys.stdout
        self.orig_stderr = sys.stderr
        sys.stdout = GUIStreamRedirector(self.log_queue, self.orig_stdout)
        sys.stderr = GUIStreamRedirector(self.log_queue, self.orig_stderr)

        # Register prompt handler in modules.utils
        set_prompt_handler(self.gui_prompt_handler)

        # Build Theme & UI Components
        self.setup_styles()
        self.build_ui()

        # Process pending layout events before geometry restoration
        self.root.update_idletasks()

        # Restore saved window geometry (size & screen position)
        geom = get_setting("window_geometry", "1040x860")
        try:
            self.root.geometry(geom)
        except Exception:
            self.root.geometry("1040x860")

        # Track window position & size changes
        self.root.bind("<Configure>", self.on_configure)

        # Start log queue polling
        self.root.after(100, self.poll_log_queue)

        # Initial Mirror Check in Background
        threading.Thread(target=self.check_mirrors_background, daemon=True).start()

        # Auto-run scanner on startup if enabled in config
        if getattr(config, 'AUTO_RUN_SCANNER_ON_STARTUP', False):
            self.root.after(800, self.action_start_scan)

    # -------------------------------------------------------------------
    # Styling & Modern Theme Setup
    # -------------------------------------------------------------------
    def setup_styles(self):
        self.colors = {
            "bg": "#181825",            # Catppuccin Crust / Dark Surface
            "card": "#1e1e2e",          # Base Surface
            "header": "#313244",        # Surface 1
            "text": "#cdd6f4",          # Main Text
            "subtext": "#a6adc8",       # Muted Text
            "accent": "#89b4fa",        # Soft Blue
            "accent_hover": "#b4befe",
            "success": "#a6e3a1",       # Soft Green
            "warning": "#f9e2af",       # Soft Yellow
            "error": "#f38ba8",         # Soft Red
            "entry_bg": "#313244",
            "border": "#45475a"
        }

        self.root.configure(bg=self.colors["bg"])

        self.style = ttk.Style()
        self.style.theme_use("clam")

        # Configure generic styles
        self.style.configure(".", background=self.colors["card"], foreground=self.colors["text"], font=("Segoe UI", 10))
        self.style.configure("TFrame", background=self.colors["card"])
        self.style.configure("Crust.TFrame", background=self.colors["bg"])

        # Notebook / Tabs
        self.style.configure("TNotebook", background=self.colors["bg"], borderwidth=0, tabmargins=[2, 4, 2, 0])
        self.style.configure("TNotebook.Tab", background=self.colors["header"], foreground=self.colors["subtext"],
                             padding=[16, 6], font=("Segoe UI", 10, "bold"), borderwidth=0, focuscolor="")
        self.style.map("TNotebook.Tab",
                       background=[("selected", self.colors["card"]), ("hover", self.colors["header"])],
                       foreground=[("selected", self.colors["accent"]), ("hover", self.colors["text"])],
                       padding=[("selected", [16, 10]), ("!selected", [16, 6])],
                       expand=[("selected", [2, 3, 2, 0])])

        # Header Frame
        self.style.configure("Header.TFrame", background=self.colors["header"])
        self.style.configure("HeaderTitle.TLabel", background=self.colors["header"], foreground=self.colors["accent"],
                             font=("Segoe UI", 15, "bold"))
        self.style.configure("HeaderSub.TLabel", background=self.colors["header"], foreground=self.colors["subtext"],
                             font=("Segoe UI", 9))

        # Card & LabelFrame
        self.style.configure("TLabelframe", background=self.colors["card"], foreground=self.colors["accent"],
                             bordercolor=self.colors["border"], borderwidth=1, padding=12)
        self.style.configure("TLabelframe.Label", background=self.colors["card"], foreground=self.colors["accent"],
                             font=("Segoe UI", 10, "bold"))

        # Buttons
        self.style.configure("TButton", background=self.colors["header"], foreground=self.colors["text"],
                             padding=[12, 6], borderwidth=0, font=("Segoe UI", 9, "bold"))
        self.style.map("TButton",
                       background=[("active", self.colors["accent"]), ("disabled", "#45475a")],
                       foreground=[("active", "#11111b"), ("disabled", "#6c7086")])

        self.style.configure("Accent.TButton", background=self.colors["accent"], foreground="#11111b",
                             padding=[14, 7], font=("Segoe UI", 10, "bold"))
        self.style.map("Accent.TButton",
                       background=[("active", self.colors["accent_hover"]), ("disabled", "#45475a")],
                       foreground=[("active", "#11111b"), ("disabled", "#6c7086")])

        self.style.configure("Stop.TButton", background=self.colors["error"], foreground="#11111b",
                             padding=[14, 7], font=("Segoe UI", 10, "bold"))
        self.style.map("Stop.TButton",
                       background=[("active", "#f5e0dc"), ("disabled", "#45475a")],
                       foreground=[("active", "#11111b"), ("disabled", "#6c7086")])

        # Compact Icon Button Style (Aligned with text baseline)
        self.style.configure("Icon.TButton", background=self.colors["header"], foreground=self.colors["text"],
                             padding=[3, 1], borderwidth=0, font=("Segoe UI", 9))
        self.style.map("Icon.TButton",
                       background=[("active", self.colors["accent"]), ("disabled", "#45475a")],
                       foreground=[("active", "#11111b"), ("disabled", "#6c7086")])

        # Vibrant Green Progress Bar Style
        self.style.configure("Green.Horizontal.TProgressbar",
                             troughcolor="#313244",
                             background="#a6e3a1",
                             bordercolor="#181825",
                             lightcolor="#a6e3a1",
                             darkcolor="#a6e3a1",
                             thickness=14)

        # Inputs & Combobox
        self.style.configure("TEntry", fieldbackground=self.colors["entry_bg"], foreground=self.colors["text"],
                             insertcolor=self.colors["text"], borderwidth=1, bordercolor=self.colors["border"])
        self.style.configure("TCombobox", fieldbackground=self.colors["entry_bg"], background=self.colors["header"],
                             foreground=self.colors["text"], arrowcolor=self.colors["accent"])
        self.style.map("TCombobox", fieldbackground=[("readonly", self.colors["entry_bg"])])

        # Checkbutton / Radiobutton
        self.style.configure("TCheckbutton", background=self.colors["card"], foreground=self.colors["text"])
        self.style.map("TCheckbutton", background=[("active", self.colors["card"])])

        self.style.configure("TRadiobutton", background=self.colors["card"], foreground=self.colors["text"])
        self.style.map("TRadiobutton", background=[("active", self.colors["card"])])

        # Treeview
        self.style.configure("Treeview", background=self.colors["card"], foreground=self.colors["text"],
                             fieldbackground=self.colors["card"], rowheight=26, borderwidth=0)
        self.style.configure("Treeview.Heading", background=self.colors["header"], foreground=self.colors["accent"],
                             font=("Segoe UI", 9, "bold"), borderwidth=1)
        self.style.map("Treeview", background=[("selected", self.colors["header"])],
                       foreground=[("selected", self.colors["accent"])])

    # -------------------------------------------------------------------
    # Main UI Construction
    # -------------------------------------------------------------------
    def build_ui(self):
        # Top Header Bar
        header_frame = ttk.Frame(self.root, style="Header.TFrame", padding=[15, 10])
        header_frame.pack(fill=tk.X, side=tk.TOP)

        title_box = ttk.Frame(header_frame, style="Header.TFrame")
        title_box.pack(side=tk.LEFT)
        ttk.Label(title_box, text="🎌 AnimePahe Downloader", style="HeaderTitle.TLabel").pack(anchor=tk.W)

        sub_header = ttk.Frame(title_box, style="Header.TFrame")
        sub_header.pack(anchor=tk.W, pady=(2, 0))
        self.lbl_base_dir = ttk.Label(sub_header, text=f"Base: {config.BASE_DOWNLOAD_DIR}", style="HeaderSub.TLabel")
        self.lbl_base_dir.pack(side=tk.LEFT)
        btn_open = create_external_link_icon(sub_header, bg_color=self.colors["header"],
                                            fg_color=self.colors["accent"], command=self.action_open_base_folder)
        btn_open.pack(side=tk.LEFT, padx=(3, 0), pady=(1, 0))

        # Mirror Badges Container
        self.mirror_badge_frame = ttk.Frame(header_frame, style="Header.TFrame")
        self.mirror_badge_frame.pack(side=tk.RIGHT, padx=10)

        self.lbl_ap_status = tk.Label(self.mirror_badge_frame, text="AnimePahe: ⏳ Checking...", bg="#313244", fg="#cdd6f4", font=("Segoe UI", 8, "bold"), padx=6, pady=2)
        self.lbl_ap_status.pack(side=tk.LEFT, padx=2)

        self.lbl_kw_status = tk.Label(self.mirror_badge_frame, text="Kwik: ⏳ Checking...", bg="#313244", fg="#cdd6f4", font=("Segoe UI", 8, "bold"), padx=6, pady=2)
        self.lbl_kw_status.pack(side=tk.LEFT, padx=2)

        self.lbl_jk_status = tk.Label(self.mirror_badge_frame, text="Jikan: ⏳", bg="#313244", fg="#cdd6f4", font=("Segoe UI", 8, "bold"), padx=5, pady=2, cursor="hand2")
        self.lbl_jk_status.pack(side=tk.LEFT, padx=2)
        self.lbl_jk_status.bind("<Button-1>", lambda e: self.show_mirror_details_popup())

        self.lbl_al_status = tk.Label(self.mirror_badge_frame, text="AniList: ⏳", bg="#313244", fg="#cdd6f4", font=("Segoe UI", 8, "bold"), padx=5, pady=2, cursor="hand2")
        self.lbl_al_status.pack(side=tk.LEFT, padx=2)
        self.lbl_al_status.bind("<Button-1>", lambda e: self.show_mirror_details_popup())

        self.lbl_kt_status = tk.Label(self.mirror_badge_frame, text="Kitsu: ⏳", bg="#313244", fg="#cdd6f4", font=("Segoe UI", 8, "bold"), padx=5, pady=2, cursor="hand2")
        self.lbl_kt_status.pack(side=tk.LEFT, padx=2)
        self.lbl_kt_status.bind("<Button-1>", lambda e: self.show_mirror_details_popup())

        ttk.Button(self.mirror_badge_frame, text="🔄 Check", command=lambda: threading.Thread(target=self.check_mirrors_background, daemon=True).start()).pack(side=tk.LEFT, padx=3)

        # Notebook Container
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=False, padx=10, pady=(10, 5))

        # Create Tabs with compact padding
        self.tab_scan = ttk.Frame(self.notebook, padding=8)
        self.tab_manual = ttk.Frame(self.notebook, padding=8)
        self.tab_library = ttk.Frame(self.notebook, padding=8)
        self.tab_settings = ttk.Frame(self.notebook, padding=8)
        self.tab_browser = ttk.Frame(self.notebook, padding=4)

        self.notebook.add(self.tab_scan, text=" Library Scanner ")
        self.notebook.add(self.tab_manual, text=" Manual Search & Download ")
        self.notebook.add(self.tab_library, text=" Tracked Folders DB ")
        self.notebook.add(self.tab_settings, text=" Settings & Mirrors ")
        self.notebook.add(self.tab_browser, text=" 🌐 Embedded Browser ")

        # Populate Tabs
        self.build_tab_scan()
        self.build_tab_manual()
        self.build_tab_library()
        self.build_tab_settings()
        self.build_tab_browser()

        # Console Log Drawer at Bottom
        self.build_console_section()

    # -------------------------------------------------------------------
    # Tab 1: Library Scanner
    # -------------------------------------------------------------------
    def build_tab_scan(self):
        # Mode Selection
        mode_frame = ttk.LabelFrame(self.tab_scan, text=" Scan Mode ")
        mode_frame.pack(fill=tk.X, pady=(0, 6))

        self.scan_mode_var = tk.StringVar(value="standard")
        ttk.Radiobutton(mode_frame, text="Standard Scan (Auto-update existing library)", variable=self.scan_mode_var, value="standard").pack(anchor=tk.W, pady=1)
        ttk.Radiobutton(mode_frame, text="Scan for More Seasons (--more-seasons: find completely untracked sequels)", variable=self.scan_mode_var, value="more_seasons").pack(anchor=tk.W, pady=1)
        ttk.Radiobutton(mode_frame, text="Scan for Newer Seasons (--new-seasons: filter strictly for higher seasons/years)", variable=self.scan_mode_var, value="new_seasons").pack(anchor=tk.W, pady=1)

        # Download Options Card
        opts_frame = ttk.LabelFrame(self.tab_scan, text=" Download Preferences ")
        opts_frame.pack(fill=tk.X, pady=(0, 6))

        grid_box = ttk.Frame(opts_frame)
        grid_box.pack(fill=tk.X)

        # Quality
        ttk.Label(grid_box, text="Video Quality:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=3)
        self.scan_quality_var = tk.StringVar(value="Auto")
        cb_q = ttk.Combobox(grid_box, textvariable=self.scan_quality_var, values=["Auto", "720p", "1080p", "360p"], width=10, state="readonly")
        cb_q.grid(row=0, column=1, sticky=tk.W, padx=5, pady=3)

        # Audio Language
        ttk.Label(grid_box, text="Audio Language:").grid(row=0, column=2, sticky=tk.W, padx=15, pady=3)
        self.scan_lang_var = tk.StringVar(value="Auto")
        cb_l = ttk.Combobox(grid_box, textvariable=self.scan_lang_var, values=["Auto", "en", "jap"], width=10, state="readonly")
        cb_l.grid(row=0, column=3, sticky=tk.W, padx=5, pady=3)

        # Parallel Downloads
        ttk.Label(grid_box, text="Parallel Downloads:").grid(row=0, column=4, sticky=tk.W, padx=15, pady=3)
        self.scan_parallel_var = tk.StringVar(value=str(getattr(config, 'DEFAULT_PARALLEL_DOWNLOADS', 2)))
        cb_p = ttk.Combobox(grid_box, textvariable=self.scan_parallel_var, values=["1", "2", "3", "4", "6", "8"], width=10, state="readonly")
        cb_p.grid(row=0, column=5, sticky=tk.W, padx=5, pady=3)

        # Specific Folder Target & Yes Flag
        row2_box = ttk.Frame(opts_frame)
        row2_box.pack(fill=tk.X, pady=(3, 0))

        ttk.Label(row2_box, text="Target Folder/Anime (Optional):").pack(side=tk.LEFT, padx=5)
        self.scan_filter_var = tk.StringVar()
        ttk.Entry(row2_box, textvariable=self.scan_filter_var, width=30).pack(side=tk.LEFT, padx=5)

        self.scan_yes_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2_box, text="Auto-confirm all prompts (-y)", variable=self.scan_yes_var).pack(side=tk.LEFT, padx=15)

        # Action Buttons Box
        btn_box = ttk.Frame(self.tab_scan)
        btn_box.pack(fill=tk.X, pady=(4, 0))

        self.btn_start_scan = ttk.Button(btn_box, text="🚀 Start Library Scan", style="Accent.TButton", command=self.action_start_scan)
        self.btn_start_scan.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_stop_scan = ttk.Button(btn_box, text="🛑 Stop Task", style="Stop.TButton", command=self.action_stop_task, state="disabled")
        self.btn_stop_scan.pack(side=tk.LEFT)

    # -------------------------------------------------------------------
    # Tab 2: Manual Search & Download
    # -------------------------------------------------------------------
    def build_tab_manual(self):
        search_card = ttk.LabelFrame(self.tab_manual, text=" Search & Direct Download ")
        search_card.pack(fill=tk.X, pady=(0, 6))

        # Anime Name
        f1 = ttk.Frame(search_card)
        f1.pack(fill=tk.X, pady=2)
        ttk.Label(f1, text="Anime Name(s):", width=18).pack(side=tk.LEFT)
        self.manual_name_var = tk.StringVar()
        ttk.Entry(f1, textvariable=self.manual_name_var, font=("Segoe UI", 10)).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Direct URL
        f2 = ttk.Frame(search_card)
        f2.pack(fill=tk.X, pady=2)
        ttk.Label(f2, text="AnimePahe URL (Opt):", width=18).pack(side=tk.LEFT)
        self.manual_url_var = tk.StringVar()
        ttk.Entry(f2, textvariable=self.manual_url_var, font=("Segoe UI", 10)).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Episode Filter
        f3 = ttk.Frame(search_card)
        f3.pack(fill=tk.X, pady=2)
        ttk.Label(f3, text="Episodes (e.g. 1,3,5-10):", width=18).pack(side=tk.LEFT)
        self.manual_episodes_var = tk.StringVar()
        ttk.Entry(f3, textvariable=self.manual_episodes_var, width=20, font=("Segoe UI", 10)).pack(side=tk.LEFT)
        ttk.Label(f3, text="  (Leave blank to download all missing episodes)", style="HeaderSub.TLabel").pack(side=tk.LEFT)

        # Preferences
        opts_frame = ttk.LabelFrame(self.tab_manual, text=" Options & Flags ")
        opts_frame.pack(fill=tk.X, pady=(0, 6))

        grid_box = ttk.Frame(opts_frame)
        grid_box.pack(fill=tk.X)

        ttk.Label(grid_box, text="Quality:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=3)
        self.manual_quality_var = tk.StringVar(value="Auto")
        ttk.Combobox(grid_box, textvariable=self.manual_quality_var, values=["Auto", "720p", "1080p", "360p"], width=10, state="readonly").grid(row=0, column=1, sticky=tk.W, padx=5, pady=3)

        ttk.Label(grid_box, text="Audio:").grid(row=0, column=2, sticky=tk.W, padx=15, pady=3)
        self.manual_lang_var = tk.StringVar(value="Auto")
        ttk.Combobox(grid_box, textvariable=self.manual_lang_var, values=["Auto", "en", "jap"], width=10, state="readonly").grid(row=0, column=3, sticky=tk.W, padx=5, pady=3)

        ttk.Label(grid_box, text="Parallel:").grid(row=0, column=4, sticky=tk.W, padx=15, pady=3)
        self.manual_parallel_var = tk.StringVar(value=str(getattr(config, 'DEFAULT_PARALLEL_DOWNLOADS', 2)))
        ttk.Combobox(grid_box, textvariable=self.manual_parallel_var, values=["1", "2", "3", "4", "6", "8"], width=10, state="readonly").grid(row=0, column=5, sticky=tk.W, padx=5, pady=3)

        flags_box = ttk.Frame(opts_frame)
        flags_box.pack(fill=tk.X, pady=(3, 0))

        self.manual_all_seasons_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(flags_box, text="Search All Seasons / Movies (--all-seasons)", variable=self.manual_all_seasons_var).pack(side=tk.LEFT, padx=5)

        self.manual_yes_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(flags_box, text="Skip Prompts & Auto-Download (-y)", variable=self.manual_yes_var).pack(side=tk.LEFT, padx=15)

        # Action Buttons
        btn_box = ttk.Frame(self.tab_manual)
        btn_box.pack(fill=tk.X, pady=(4, 0))

        self.btn_start_manual = ttk.Button(btn_box, text="🔍 Search & Download", style="Accent.TButton", command=self.action_start_manual)
        self.btn_start_manual.pack(side=tk.LEFT, padx=(0, 10))

    # -------------------------------------------------------------------
    # Tab 3: Tracked Folders Database Manager
    # -------------------------------------------------------------------
    def build_tab_library(self):
        top_bar = ttk.Frame(self.tab_library)
        top_bar.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(top_bar, text="Filter Table:").pack(side=tk.LEFT, padx=(0, 5))
        self.db_filter_var = tk.StringVar()
        self.db_filter_var.trace("w", lambda *args: self.refresh_treeview())
        ttk.Entry(top_bar, textvariable=self.db_filter_var, width=30).pack(side=tk.LEFT)

        ttk.Button(top_bar, text="🔄 Refresh Table", command=self.refresh_treeview).pack(side=tk.RIGHT)

        # Treeview Table
        table_frame = ttk.Frame(self.tab_library)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("folder", "aid", "title", "auto", "updated")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse", height=5)

        self.tree.heading("folder", text="Folder Path / Name")
        self.tree.heading("aid", text="Anime ID")
        self.tree.heading("title", text="Anime Title")
        self.tree.heading("auto", text="Status")
        self.tree.heading("updated", text="Last Checked")

        self.tree.column("folder", width=260)
        self.tree.column("aid", width=110)
        self.tree.column("title", width=220)
        self.tree.column("auto", width=110, anchor=tk.CENTER)
        self.tree.column("updated", width=140, anchor=tk.CENTER)

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Bottom Actions Bar
        actions_bar = ttk.Frame(self.tab_library)
        actions_bar.pack(fill=tk.X, pady=(8, 0))

        ttk.Button(actions_bar, text="✅ Enable Auto-Download", command=self.action_enable_auto).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(actions_bar, text="❌ Set as Skipped", command=self.action_set_skipped).pack(side=tk.LEFT, padx=5)
        ttk.Button(actions_bar, text="🗑️ Remove Entry", command=self.action_delete_tracking).pack(side=tk.LEFT, padx=5)
        ttk.Button(actions_bar, text="📂 Open Folder", command=self.action_open_folder).pack(side=tk.LEFT, padx=5)
        ttk.Button(actions_bar, text="➕ Add Skip Folder", command=self.action_add_skip_folder).pack(side=tk.RIGHT)

        # Initial populate
        self.refresh_treeview()

    # -------------------------------------------------------------------
    # Tab 4: Settings & Configuration
    # -------------------------------------------------------------------
    def build_tab_settings(self):
        settings_card = ttk.LabelFrame(self.tab_settings, text=" Configuration & Preferences ")
        settings_card.pack(fill=tk.BOTH, expand=True)

        # Base Directory
        f_dir = ttk.Frame(settings_card)
        f_dir.pack(fill=tk.X, pady=4)
        ttk.Label(f_dir, text="Base Download Dir:", width=20).pack(side=tk.LEFT)
        self.cfg_dir_var = tk.StringVar(value=config.BASE_DOWNLOAD_DIR)
        ttk.Entry(f_dir, textvariable=self.cfg_dir_var, font=("Segoe UI", 9)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        ttk.Button(f_dir, text="Browse...", command=self.action_browse_dir).pack(side=tk.RIGHT, padx=2)
        btn_open_setting = create_external_link_icon(f_dir, bg_color=self.colors["card"],
                                                    fg_color=self.colors["accent"], command=self.action_open_base_folder)
        btn_open_setting.pack(side=tk.RIGHT, padx=3)

        # Numeric & Dropdown Preferences
        f_def = ttk.Frame(settings_card)
        f_def.pack(fill=tk.X, pady=4)

        ttk.Label(f_def, text="Quality:").pack(side=tk.LEFT)
        self.cfg_quality_var = tk.StringVar(value=config.DEFAULT_QUALITY)
        ttk.Combobox(f_def, textvariable=self.cfg_quality_var, values=["720p", "1080p", "360p"], width=8, state="readonly").pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(f_def, text="Audio:").pack(side=tk.LEFT)
        self.cfg_lang_var = tk.StringVar(value=getattr(config, 'DEFAULT_LANGUAGE', 'en'))
        ttk.Combobox(f_def, textvariable=self.cfg_lang_var, values=["en", "jap"], width=8, state="readonly").pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(f_def, text="Parallel:").pack(side=tk.LEFT)
        self.cfg_parallel_var = tk.IntVar(value=config.DEFAULT_PARALLEL_DOWNLOADS)
        ttk.Spinbox(f_def, from_=1, to=8, textvariable=self.cfg_parallel_var, width=4).pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(f_def, text="Segments:").pack(side=tk.LEFT)
        self.cfg_segments_var = tk.IntVar(value=getattr(config, 'DOWNLOAD_SEGMENTS', 4))
        ttk.Spinbox(f_def, from_=1, to=16, textvariable=self.cfg_segments_var, width=4).pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(f_def, text="Max Dist:").pack(side=tk.LEFT)
        self.cfg_distance_var = tk.IntVar(value=getattr(config, 'MAX_DISTANCE_THRESHOLD', 20))
        ttk.Spinbox(f_def, from_=5, to=50, textvariable=self.cfg_distance_var, width=4).pack(side=tk.LEFT, padx=4)

        # 2-Column Toggles
        f_toggles = ttk.Frame(settings_card)
        f_toggles.pack(fill=tk.X, pady=4)

        col1 = ttk.Frame(f_toggles)
        col1.pack(side=tk.LEFT, fill=tk.X, expand=True)
        col2 = ttk.Frame(f_toggles)
        col2.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.cfg_ipv4_var = tk.BooleanVar(value=getattr(config, 'FORCE_IPV4', False))
        ttk.Checkbutton(col1, text="Force IPv4 connection", variable=self.cfg_ipv4_var).pack(anchor=tk.W, pady=1)

        self.cfg_auto_scan_var = tk.BooleanVar(value=getattr(config, 'AUTO_RUN_SCANNER_ON_STARTUP', False))
        ttk.Checkbutton(col1, text="Run scanner on GUI startup", variable=self.cfg_auto_scan_var).pack(anchor=tk.W, pady=1)

        self.cfg_browser_fail_var = tk.BooleanVar(value=getattr(config, 'OPEN_BROWSER_ON_FAIL', False))
        ttk.Checkbutton(col2, text="Open browser on download fail", variable=self.cfg_browser_fail_var).pack(anchor=tk.W, pady=1)

        self.cfg_segmented_var = tk.BooleanVar(value=getattr(config, 'ENABLE_SEGMENTED_DOWNLOAD', True))
        ttk.Checkbutton(col2, text="Enable Segmented downloads", variable=self.cfg_segmented_var).pack(anchor=tk.W, pady=1)

        # Save & Actions Button Bar
        btn_bar = ttk.Frame(settings_card)
        btn_bar.pack(fill=tk.X, pady=(6, 2))

        btn_save = ttk.Button(btn_bar, text="💾 Save Settings", style="Accent.TButton", command=self.action_save_settings)
        btn_save.pack(side=tk.LEFT, padx=(0, 6))

        btn_clear_cf = ttk.Button(btn_bar, text="🧹 Clear CF Session Cookies", command=self.action_clear_cf_sessions)
        btn_clear_cf.pack(side=tk.LEFT)

    def action_clear_cf_sessions(self):
        """Clears all cached Cloudflare & site session cookies from DB."""
        clear_sessions()
        messagebox.showinfo("Cloudflare Sessions Cleared", "Successfully cleared all cached Cloudflare and site cookies.\n\nNext download or scan operation will perform a fresh Cloudflare browser resolution.")

    # -------------------------------------------------------------------
    # Tab 5: Embedded Browser View
    # -------------------------------------------------------------------
    def build_tab_browser(self):
        browser_card = ttk.LabelFrame(self.tab_browser, text=" Cloudflare & Browser Resolution ")
        browser_card.pack(fill=tk.BOTH, expand=True)

        info_bar = ttk.Frame(browser_card)
        info_bar.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(info_bar, text="ℹ Real-time Cloudflare bypass & link extraction view (undetected-chromedriver)", font=("Segoe UI", 9), foreground="#89b4fa").pack(side=tk.LEFT)

        # Container Frame for Chrome Reparenting
        self.browser_container = ttk.Frame(browser_card, style="TFrame")
        self.browser_container.pack(fill=tk.BOTH, expand=True)
        self.browser_container.bind("<Configure>", self.on_browser_container_resize)

        # Register HWND getter & thread-safe tab switcher with browser_embed module
        register_container_hwnd(self.get_browser_container_info, select_callback=self.select_browser_tab, hide_callback=self.hide_browser_tab)

        # Initially hide the Embedded Browser tab when not in use
        try:
            self.notebook.hide(self.tab_browser)
        except Exception:
            pass

    def get_browser_container_info(self):
        """Returns (container_hwnd, width, height) after ensuring frame update."""
        try:
            self.root.update_idletasks()
            return self.browser_container.winfo_id(), self.browser_container.winfo_width(), self.browser_container.winfo_height()
        except Exception:
            return None, 800, 500

    def select_browser_tab(self):
        """Unhides GUI tab, expands height over console log, and switches to 🌐 Embedded Browser when resolution triggers."""
        evt = threading.Event()

        def do_switch():
            try:
                curr = self.notebook.select()
                if curr and curr != str(self.tab_browser):
                    self.previous_tab = curr
                if hasattr(self, 'console_frame') and self.console_frame:
                    self.console_frame.pack_forget()
                self.notebook.pack_configure(expand=True)
                self.notebook.add(self.tab_browser, text=" 🌐 Embedded Browser ")
                self.notebook.select(self.tab_browser)
                self.root.update_idletasks()
                self.root.update()
            except Exception as e:
                log_debug(f"Error switching browser tab: {e}")
            finally:
                evt.set()

        self.root.after(0, do_switch)
        evt.wait(timeout=5)

    def hide_browser_tab(self):
        """Restores previous tab, unhides console frame, and hides the 🌐 Embedded Browser tab when browser session finishes."""
        def do_hide():
            try:
                if hasattr(self, 'previous_tab') and self.previous_tab:
                    try:
                        self.notebook.select(self.previous_tab)
                    except Exception:
                        pass
                self.notebook.hide(self.tab_browser)
                self.notebook.pack_configure(expand=False)
                if hasattr(self, 'console_frame') and self.console_frame:
                    self.console_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
                self.root.update_idletasks()
            except Exception as e:
                log_debug(f"Error hiding browser tab: {e}")

        self.root.after(0, do_hide)

    def on_browser_container_resize(self, event=None):
        if event and hasattr(self, 'browser_container'):
            width = event.width
            height = event.height
            resize_current_embedded(width, height)

    # -------------------------------------------------------------------
    # Console Log Section
    # -------------------------------------------------------------------
    def build_console_section(self):
        self.console_frame = ttk.LabelFrame(self.root, text=" Terminal & Output Log ", padding=8)
        self.console_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        # Console Header Toolbar Row (Non-overlapping Clear Log)
        console_hdr = ttk.Frame(self.console_frame)
        console_hdr.pack(fill=tk.X, side=tk.TOP, pady=(0, 4))

        ttk.Label(console_hdr, text="Activity Log", font=("Segoe UI", 9, "bold"), foreground="#89b4fa").pack(side=tk.LEFT)
        self.lbl_queue_status = ttk.Label(console_hdr, text="Queue: Idle", font=("Segoe UI", 9), foreground="#a6adc8", cursor="hand2")
        self.lbl_queue_status.pack(side=tk.LEFT, padx=15)
        self.queue_tooltip = QueueToolTip(self.lbl_queue_status, self.get_queue_tooltip_content)

        ttk.Button(console_hdr, text="Clear Log", command=self.action_clear_log).pack(side=tk.RIGHT)
        self.btn_clear_queue = ttk.Button(console_hdr, text="Clear Queue (0)", command=self.action_clear_queue, state="disabled")
        self.btn_clear_queue.pack(side=tk.RIGHT, padx=5)

        # GUI Multi-Progress Bar Container Panel (Packed dynamically only when progress bars are active)
        self.progress_frame = ttk.Frame(self.console_frame)
        self.active_progress_bars = {}

        # Console Text Box Container
        self.text_frame = ttk.Frame(self.console_frame)
        self.text_frame.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        self.log_text = tk.Text(self.text_frame, bg="#11111b", fg="#cdd6f4", font=("Consolas", 10),
                                wrap=tk.WORD, borderwidth=0, highlightthickness=0, height=14, state="disabled")

        log_scroll = ttk.Scrollbar(self.text_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscroll=log_scroll.set)

        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Text Tag Colors for Rich Log Output
        self.log_text.tag_config("info", foreground="#cdd6f4", font=("Consolas", 10))
        self.log_text.tag_config("success", foreground="#a6e3a1", font=("Consolas", 10, "bold"))
        self.log_text.tag_config("warning", foreground="#f9e2af", font=("Consolas", 10, "bold"))
        self.log_text.tag_config("error", foreground="#f38ba8", font=("Consolas", 10, "bold"))
        self.log_text.tag_config("progress", foreground="#89dceb", font=("Consolas", 10, "bold"))
        self.log_text.tag_config("header", foreground="#89b4fa", font=("Consolas", 10, "bold"))

    # -------------------------------------------------------------------
    # Queue Polling & Threaded Output Streaming
    # -------------------------------------------------------------------
    def poll_log_queue(self):
        while not self.log_queue.empty():
            try:
                s = self.log_queue.get_nowait()
                self.append_log(s)
            except queue.Empty:
                break

        # Check prompt queue
        if not self.prompt_queue.empty():
            try:
                prompt_text, default = self.prompt_queue.get_nowait()
                self.show_prompt_dialog(prompt_text, default)
            except queue.Empty:
                pass

        self.root.after(100, self.poll_log_queue)

    def update_progress(self, key, pct, title, stats):
        if not hasattr(self, 'active_progress_bars'):
            self.active_progress_bars = {}

        if key not in self.active_progress_bars:
            if len(self.active_progress_bars) == 0:
                self.progress_frame.pack(fill=tk.X, side=tk.TOP, pady=(0, 4), before=self.text_frame)

            if len(self.active_progress_bars) >= 4:
                oldest_key = list(self.active_progress_bars.keys())[0]
                self.remove_progress_bar(oldest_key)

            bar_frame = ttk.Frame(self.progress_frame)
            bar_frame.pack(fill=tk.X, pady=2)

            header_row = ttk.Frame(bar_frame)
            header_row.pack(fill=tk.X)

            lbl_t = ttk.Label(header_row, text=f"{title} ({pct}%)", font=("Segoe UI", 9, "bold"), foreground="#a6e3a1")
            lbl_t.pack(side=tk.LEFT)

            lbl_s = ttk.Label(header_row, text=stats, font=("Segoe UI", 9, "bold"), foreground="#a6e3a1")
            lbl_s.pack(side=tk.RIGHT)

            pbar = ttk.Progressbar(bar_frame, orient=tk.HORIZONTAL, mode="determinate", maximum=100, style="Green.Horizontal.TProgressbar")
            pbar.pack(fill=tk.X, pady=(2, 0))

            self.active_progress_bars[key] = {
                "frame": bar_frame,
                "lbl_title": lbl_t,
                "lbl_stats": lbl_s,
                "bar": pbar
            }

        item = self.active_progress_bars[key]
        if pct is not None:
            item["bar"]["value"] = pct
            item["lbl_title"].config(text=f"{title} ({pct}%)")
        if stats:
            item["lbl_stats"].config(text=stats)

        if pct == 100:
            target_key = key
            self.root.after(3000, lambda: self.remove_progress_bar(target_key))

    def remove_progress_bar(self, key):
        if hasattr(self, 'active_progress_bars') and key in self.active_progress_bars:
            item = self.active_progress_bars.pop(key)
            item["frame"].destroy()
            if len(self.active_progress_bars) == 0:
                self.progress_frame.pack_forget()

    def clear_all_progress_bars(self):
        if hasattr(self, 'active_progress_bars'):
            for key in list(self.active_progress_bars.keys()):
                try:
                    item = self.active_progress_bars[key]
                    item["frame"].destroy()
                except Exception:
                    pass
            self.active_progress_bars.clear()
            self.progress_frame.pack_forget()

    def append_log(self, text):
        if not text:
            return

        # Strip ANSI escape codes
        clean_text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)

        # Detect progress bar chunk (tqdm, [Seg], or \r streams)
        is_progress = ('\r' in clean_text) or ('%|' in clean_text and ('/s]' in clean_text or 'b/s]' in clean_text.lower()))

        if is_progress:
            lines = [l.strip() for l in clean_text.replace('\r', '\n').split('\n') if l.strip()]
            if lines:
                latest_line = lines[-1]

                pct_m = re.search(r'(\d+)%', latest_line)
                pct = int(pct_m.group(1)) if pct_m else None

                title_m = re.search(r'^(.*?):\s*\d+%', latest_line)
                if title_m:
                    title = title_m.group(1).strip()
                elif "[" in latest_line and "]" in latest_line:
                    title = latest_line.split("]")[0] + "]"
                else:
                    title = "Downloading"

                stats_m = re.search(r'(\d+\.?\d*[KMG]?i?B?/\d+\.?\d*[KMG]?i?B?.*)', latest_line)
                stats = stats_m.group(1).strip() if stats_m else ""

                if pct is not None:
                    self.update_progress(title, pct, title, stats)
            # DO NOT insert progress bar text into log_text console!
            return

        self.log_text.config(state="normal")

        curr = self.log_text.get("1.0", "end-1c")
        if curr and not curr.endswith("\n"):
            self.log_text.insert(tk.END, "\n")

        line_content = clean_text.rstrip("\n")
        if line_content:
            lower = line_content.lower()
            tag = "info"
            if "error" in lower or "failed" in lower or "exception" in lower:
                tag = "error"
            elif "warning" in lower or "high name distance" in lower:
                tag = "warning"
            elif "completed:" in lower or "success" in lower:
                tag = "success"
            elif "---" in line_content or "scanning" in lower or "searching" in lower:
                tag = "header"

            self.log_text.insert(tk.END, line_content, tag)

        self.log_text.config(state="disabled")
        self.log_text.see(tk.END)

    def action_clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state="disabled")
        self.clear_all_progress_bars()

    # -------------------------------------------------------------------
    # Mirror Health Checker
    # -------------------------------------------------------------------
    def show_mirror_details_popup(self):
        details = getattr(self, 'mirror_details_text', "No mirror check performed yet. Click 'Check' to refresh.")
        messagebox.showinfo("Mirror Availability Details", f"Active Server Endpoints:\n\n{details}")

    def check_mirrors_background(self):
        try:
            import httpx
            transport = httpx.HTTPTransport(local_address="0.0.0.0") if getattr(config, 'FORCE_IPV4', False) else None
            client = httpx.Client(http2=True, transport=transport, timeout=10, follow_redirects=True)

            ap_ok = ensure_working_mirror(client, verbose=False)
            kw_ok = ensure_working_kwik_mirror(client, verbose=False)
            jk_ok = ensure_working_jikan_mirror(client, verbose=False)
            al_ok = ensure_working_anilist_mirror(client, verbose=False)
            kt_ok = ensure_working_kitsu_mirror(client, verbose=False)
            client.close()

            def update_labels():
                ap_domain = getattr(config, 'ANIMEPAHE_URL', 'Down').replace('https://', '').replace('http://', '').strip('/')
                kw_domain = getattr(config, 'KWIK_URL', 'Down').replace('https://', '').replace('http://', '').strip('/')
                jk_domain = getattr(config, 'JIKAN_API_URL', 'Down').replace('https://', '').replace('http://', '').strip('/')
                al_domain = getattr(config, 'ANILIST_API_URL', 'Down').replace('https://', '').replace('http://', '').strip('/')
                kt_domain = getattr(config, 'KITSU_API_URL', 'Down').replace('https://', '').replace('http://', '').strip('/')

                # 1. AnimePahe Badge (Full domain name)
                self.lbl_ap_status.config(
                    text=f"AnimePahe: {ap_domain}" if ap_ok else "AnimePahe: ✖ Down",
                    bg="#1e3a29" if ap_ok else "#4a1d24",
                    fg="#a6e3a1" if ap_ok else "#f38ba8"
                )

                # 2. Kwik Badge (Full domain name)
                self.lbl_kw_status.config(
                    text=f"Kwik: {kw_domain}" if kw_ok else "Kwik: ✖ Down",
                    bg="#1e3a29" if kw_ok else "#4a1d24",
                    fg="#a6e3a1" if kw_ok else "#f38ba8"
                )

                # 3. Compact Search APIs (Jikan, AniList, Kitsu with explicit red/green colors)
                self.lbl_jk_status.config(
                    text="Jikan: ✔" if jk_ok else "Jikan: ✖",
                    bg="#1e3a29" if jk_ok else "#4a1d24",
                    fg="#a6e3a1" if jk_ok else "#f38ba8"
                )

                self.lbl_al_status.config(
                    text="AniList: ✔" if al_ok else "AniList: ✖",
                    bg="#1e3a29" if al_ok else "#4a1d24",
                    fg="#a6e3a1" if al_ok else "#f38ba8"
                )

                self.lbl_kt_status.config(
                    text="Kitsu: ✔" if kt_ok else "Kitsu: ✖",
                    bg="#1e3a29" if kt_ok else "#4a1d24",
                    fg="#a6e3a1" if kt_ok else "#f38ba8"
                )

                self.mirror_details_text = (
                    f"• AnimePahe: {ap_domain if ap_ok else '❌ Down'}\n"
                    f"• Kwik: {kw_domain if kw_ok else '❌ Down'}\n"
                    f"• Jikan: {jk_domain if jk_ok else '❌ Down'}\n"
                    f"• AniList: {al_domain if al_ok else '❌ Down'}\n"
                    f"• Kitsu: {kt_domain if kt_ok else '❌ Down'}"
                )
            self.root.after(0, update_labels)
        except Exception as e:
            log_debug(f"Mirror check failed: {e}")

    # -------------------------------------------------------------------
    # Interactive Prompt Callback Handler (Thread Safe)
    # -------------------------------------------------------------------
    def gui_prompt_handler(self, prompt_text, default="n"):
        """Called by background downloader thread whenever user input is required."""
        self.prompt_response = default
        self.prompt_result_event.clear()
        self.prompt_queue.put((prompt_text, default))

        # Block background thread until main thread sets event
        self.prompt_result_event.wait()
        return self.prompt_response

    def show_prompt_dialog(self, prompt_text, default):
        dialog = tk.Toplevel(self.root)
        dialog.title("Anime Scanner Prompt")
        dialog.geometry("540x240")
        dialog.configure(bg=self.colors["card"])
        dialog.transient(self.root)
        dialog.grab_set()

        # Center dialog relative to main window
        dialog.geometry(f"+{self.root.winfo_x() + 200}+{self.root.winfo_y() + 150}")

        def on_dialog_close():
            self.prompt_response = default
            dialog.destroy()
            self.prompt_result_event.set()

        dialog.protocol("WM_DELETE_WINDOW", on_dialog_close)

        lbl = tk.Label(dialog, text=prompt_text, bg=self.colors["card"], fg=self.colors["text"],
                       font=("Segoe UI", 10), wraplength=500, justify=tk.LEFT)
        lbl.pack(padx=20, pady=(20, 15), fill=tk.BOTH, expand=True)

        btn_frame = ttk.Frame(dialog, padding=10)
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)

        def set_choice(val):
            self.prompt_response = val
            dialog.destroy()
            self.prompt_result_event.set()

        # Parse options based on prompt text
        lower = prompt_text.lower()
        if "download" in lower and ("sub instead" in lower or "dub instead" in lower or "download japanese sub" in lower or "download english dub" in lower):
            dialog.title("Language Fallback Option")
            ttk.Button(btn_frame, text="✅ Download Fallback", style="Accent.TButton", command=lambda: set_choice("y")).pack(side=tk.LEFT, padx=6)
            ttk.Button(btn_frame, text="❌ Skip Episode", command=lambda: set_choice("n")).pack(side=tk.LEFT, padx=6)
        elif "[r(etry)/s(kip)/f(orever-skip)/q(uit)]" in lower or "download failed" in lower:
            dialog.title("Download Failure Action")
            ttk.Button(btn_frame, text="🔄 Retry", style="Accent.TButton", command=lambda: set_choice("r")).pack(side=tk.LEFT, padx=4)
            ttk.Button(btn_frame, text="⏭️ Skip Episode", command=lambda: set_choice("s")).pack(side=tk.LEFT, padx=4)
            ttk.Button(btn_frame, text="🚫 Skip Series", command=lambda: set_choice("f")).pack(side=tk.LEFT, padx=4)
            ttk.Button(btn_frame, text="🛑 Quit", command=lambda: set_choice("q")).pack(side=tk.LEFT, padx=4)
        elif "[y/n/i" in lower or "[y/n/s/f/u]" in lower:
            ttk.Button(btn_frame, text="✅ Yes (Track)", style="Accent.TButton", command=lambda: set_choice("y")).pack(side=tk.LEFT, padx=4)
            ttk.Button(btn_frame, text="⏭️ Skip Now", command=lambda: set_choice("n")).pack(side=tk.LEFT, padx=4)
            ttk.Button(btn_frame, text="🚫 Skip Folder Forever", command=lambda: set_choice("s" if "[y/n/s/f/u]" in lower else "i")).pack(side=tk.LEFT, padx=4)
            if "u" in lower:
                ttk.Button(btn_frame, text="🔗 Custom URL", command=lambda: self.ask_custom_url(dialog, set_choice)).pack(side=tk.LEFT, padx=4)
        elif "[y(es)/n(o)/u(rl)]" in lower:
            ttk.Button(btn_frame, text="✅ Yes", style="Accent.TButton", command=lambda: set_choice("y")).pack(side=tk.LEFT, padx=4)
            ttk.Button(btn_frame, text="❌ No", command=lambda: set_choice("n")).pack(side=tk.LEFT, padx=4)
            ttk.Button(btn_frame, text="🔗 Provide URL", command=lambda: self.ask_custom_url(dialog, set_choice)).pack(side=tk.LEFT, padx=4)
        elif "enter animepahe url" in lower:
            entry_url = ttk.Entry(dialog, width=45)
            entry_url.pack(padx=20, pady=5)
            entry_url.focus_set()
            ttk.Button(btn_frame, text="OK", style="Accent.TButton", command=lambda: set_choice(entry_url.get())).pack(side=tk.RIGHT)
        else:
            ttk.Button(btn_frame, text="Yes", style="Accent.TButton", command=lambda: set_choice("y")).pack(side=tk.LEFT, padx=5)
            ttk.Button(btn_frame, text="No", command=lambda: set_choice("n")).pack(side=tk.LEFT, padx=5)

    def ask_custom_url(self, parent_dialog, set_choice):
        url = simpledialog.askstring("AnimePahe URL", "Enter direct AnimePahe series URL:", parent=parent_dialog)
        if url:
            self.prompt_response = url
            parent_dialog.destroy()
            self.prompt_result_event.set()

    # -------------------------------------------------------------------
    # Task Execution Helpers & Queue Management
    # -------------------------------------------------------------------
    def update_queue_ui(self):
        q_size = self.task_queue.qsize()
        if self.current_task_label:
            status = f"Running: {self.current_task_label} | Queued: {q_size}"
            self.lbl_queue_status.config(text=f"🔄 {status}", foreground="#89b4fa")
        else:
            self.lbl_queue_status.config(text="Queue: Idle", foreground="#a6adc8")

        if hasattr(self, 'btn_clear_queue'):
            state = "normal" if q_size > 0 else "disabled"
            self.btn_clear_queue.config(text=f"Clear Queue ({q_size})", state=state)

    def get_queue_tooltip_content(self):
        queued_items = [item[1] for item in list(self.task_queue.queue)]
        lines = []
        if self.current_task_label:
            lines.append(f"▶️ Active Task:\n  • {self.current_task_label}")

        if queued_items:
            if lines:
                lines.append("")
            lines.append(f"📋 Queued Tasks ({len(queued_items)}):")
            for idx, task_label in enumerate(queued_items, 1):
                lines.append(f"  #{idx}. {task_label}")
        elif not lines:
            lines.append("Queue is empty")

        return "\n".join(lines)

    def action_clear_queue(self):
        cleared_count = 0
        while not self.task_queue.empty():
            try:
                self.task_queue.get_nowait()
                cleared_count += 1
            except Exception:
                break
        self.append_log(f"\n🧹 Cleared {cleared_count} task(s) from queue.\n")
        self.update_queue_ui()

    def run_cli_in_thread(self, cmd_args, task_label=None):
        if not task_label:
            task_label = " ".join(cmd_args) if cmd_args else "Library Scan"

        if self.running_thread and self.running_thread.is_alive():
            self.task_queue.put((cmd_args, task_label))
            self.update_queue_ui()
            return

        self.current_task_label = task_label
        self.btn_stop_scan.config(state="normal")
        self.update_queue_ui()
        self.append_log(f"\n🚀 Running Task: {task_label} (animepahe_download.py {' '.join(cmd_args)})\n")

        def worker():
            try:
                animepahe_download.main(cmd_args)
                time.sleep(0.2)
                self.log_queue.put(f"\n✅ Task finished: {task_label}\n")
            except Exception as e:
                self.log_queue.put(f"\n❌ Execution Error in {task_label}: {e}\n")
            finally:
                self.root.after(0, self.on_task_finished)

        self.running_thread = threading.Thread(target=worker, daemon=True)
        self.running_thread.start()

    def on_task_finished(self):
        self.refresh_treeview()
        if not self.task_queue.empty():
            next_args, next_label = self.task_queue.get_nowait()
            self.append_log(f"\n▶️ Auto-starting next queued task: {next_label}\n")
            self.run_cli_in_thread(next_args, next_label)
        else:
            self.current_task_label = None
            self.btn_stop_scan.config(state="disabled")
            self.update_queue_ui()
            self.append_log("\n🏁 All queued tasks completed.\n")

    def action_stop_task(self):
        if not self.task_queue.empty():
            if messagebox.askyesno("Stop & Clear Queue", "Stop current task and clear remaining queued tasks?"):
                self.action_clear_queue()
        messagebox.showinfo("Stop Task", "The active task will complete its current network request and stop.")
        self.on_task_finished()

    def on_configure(self, event=None):
        if event is None or event.widget == self.root:
            if self.root.state() == "normal":
                save_setting("window_geometry", self.root.geometry())

    def on_close(self):
        try:
            if self.root.state() == "normal":
                save_setting("window_geometry", self.root.geometry())
        except Exception as e:
            log_debug(f"Failed to save window geometry: {e}")
        self.root.destroy()

    # -------------------------------------------------------------------
    # Actions for Tab 1 (Scanner) & Tab 2 (Manual)
    # -------------------------------------------------------------------
    def action_start_scan(self):
        cmd_args = []
        mode = self.scan_mode_var.get()
        if mode == "more_seasons":
            cmd_args.append("--more-seasons")
        elif mode == "new_seasons":
            cmd_args.append("--new-seasons")

        target = self.scan_filter_var.get().strip()
        if target:
            cmd_args.append(target)

        q = self.scan_quality_var.get()
        if q and q.lower() != "auto":
            cmd_args.extend(["-q", q])

        lang = self.scan_lang_var.get()
        if lang and lang.lower() != "auto":
            cmd_args.extend(["-l", lang])

        p = self.scan_parallel_var.get()
        if p and str(p).lower() != "auto":
            cmd_args.extend(["--parallel", str(p)])

        if self.scan_yes_var.get():
            cmd_args.append("-y")

        task_label = f"Scan ({mode}{': ' + target if target else ''})"
        self.run_cli_in_thread(cmd_args, task_label)

    def action_start_manual(self):
        cmd_args = []
        name = self.manual_name_var.get().strip()
        url = self.manual_url_var.get().strip()

        if name:
            cmd_args.append(name)

        if url:
            cmd_args.extend(["--url", url])

        ep = self.manual_episodes_var.get().strip()
        if ep:
            cmd_args.extend(["-ep", ep])

        if self.manual_all_seasons_var.get():
            cmd_args.append("--all-seasons")

        q = self.manual_quality_var.get()
        if q and q.lower() != "auto":
            cmd_args.extend(["-q", q])

        lang = self.manual_lang_var.get()
        if lang and lang.lower() != "auto":
            cmd_args.extend(["-l", lang])

        p = self.manual_parallel_var.get()
        if p and str(p).lower() != "auto":
            cmd_args.extend(["--parallel", str(p)])

        if self.manual_yes_var.get():
            cmd_args.append("-y")

        if not name and not url:
            messagebox.showwarning("Input Required", "Please enter an Anime Name or a direct AnimePahe URL.")
            return

        task_label = f"Download ({name or url})"
        self.run_cli_in_thread(cmd_args, task_label)

    # -------------------------------------------------------------------
    # Actions for Tab 3 (Treeview Database Manager)
    # -------------------------------------------------------------------
    def refresh_treeview(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        filter_text = self.db_filter_var.get().lower().strip()

        try:
            conn = sqlite3.connect(config.DB_PATH)
            c = conn.cursor()
            c.execute("SELECT folder_path, anime_id, anime_title, auto_download, last_updated FROM tracking ORDER BY last_updated DESC")
            rows = c.fetchall()
            conn.close()

            for folder, aid, title, auto, updated in rows:
                folder_name = os.path.basename(folder) if folder else "Unknown"
                display_aid = aid or "N/A (Skipped)"
                display_title = title or folder_name
                status = "✅ Active" if auto == 1 else "❌ Skipped"
                display_upd = updated[:19].replace("T", " ") if updated else "Never"

                if filter_text and not (filter_text in folder_name.lower() or filter_text in display_title.lower() or filter_text in display_aid.lower()):
                    continue

                self.tree.insert("", tk.END, values=(folder, display_aid, display_title, status, display_upd))
        except Exception as e:
            log_debug(f"Treeview refresh error: {e}")

    def get_selected_folder(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Selection Required", "Please select a row from the database table.")
            return None
        item = self.tree.item(sel[0])
        return item["values"][0]

    def action_enable_auto(self):
        folder = self.get_selected_folder()
        if folder:
            tracked = get_tracked(folder)
            if tracked:
                save_tracked(folder, tracked[0], tracked[1], True)
                self.refresh_treeview()
                messagebox.showinfo("Status Updated", f"Set auto-download = True for:\n{folder}")

    def action_set_skipped(self):
        folder = self.get_selected_folder()
        if folder:
            save_tracked(folder, None, None, False)
            self.refresh_treeview()
            messagebox.showinfo("Status Updated", f"Added folder to skip list:\n{folder}")

    def action_delete_tracking(self):
        folder = self.get_selected_folder()
        if folder and messagebox.askyesno("Confirm Delete", f"Remove tracking record for:\n{folder}?"):
            try:
                conn = sqlite3.connect(config.DB_PATH)
                c = conn.cursor()
                c.execute("DELETE FROM tracking WHERE folder_path = ?", (folder,))
                conn.commit()
                conn.close()
                self.refresh_treeview()
            except Exception as e:
                messagebox.showerror("Error", str(e))

    def action_open_folder(self):
        folder = self.get_selected_folder()
        if folder:
            if os.path.exists(folder):
                os.startfile(folder)
            else:
                messagebox.showwarning("Not Found", f"Directory does not exist on disk:\n{folder}")

    def action_add_skip_folder(self):
        folder_input = simpledialog.askstring("Skip Folder", "Enter folder name or relative path to skip:")
        if folder_input:
            target_path = os.path.abspath(os.path.join(config.BASE_DOWNLOAD_DIR, folder_input))
            save_tracked(target_path, None, None, False)
            self.refresh_treeview()
            messagebox.showinfo("Skip Folder Added", f"Added to skip list:\n{target_path}")

    # -------------------------------------------------------------------
    # Actions for Tab 4 (Settings) & Header
    # -------------------------------------------------------------------
    def action_open_base_folder(self):
        folder = getattr(config, 'BASE_DOWNLOAD_DIR', None) or (hasattr(self, 'cfg_dir_var') and self.cfg_dir_var.get())
        if folder and os.path.exists(folder):
            os.startfile(folder)
        elif folder:
            try:
                os.makedirs(folder, exist_ok=True)
                os.startfile(folder)
            except Exception as e:
                messagebox.showerror("Folder Error", f"Could not open directory:\n{folder}\n{e}")
        else:
            messagebox.showwarning("Not Set", "Base download folder is not configured.")

    def action_browse_dir(self):
        d = filedialog.askdirectory(initialdir=self.cfg_dir_var.get())
        if d:
            self.cfg_dir_var.set(d)

    def action_save_settings(self):
        try:
            # Update memory variables
            config.BASE_DOWNLOAD_DIR = self.cfg_dir_var.get()
            config.DEFAULT_QUALITY = self.cfg_quality_var.get()
            config.DEFAULT_LANGUAGE = self.cfg_lang_var.get()
            config.DEFAULT_PARALLEL_DOWNLOADS = self.cfg_parallel_var.get()
            config.FORCE_IPV4 = self.cfg_ipv4_var.get()
            config.AUTO_RUN_SCANNER_ON_STARTUP = self.cfg_auto_scan_var.get()
            config.OPEN_BROWSER_ON_FAIL = self.cfg_browser_fail_var.get()
            config.ENABLE_SEGMENTED_DOWNLOAD = self.cfg_segmented_var.get()
            config.DOWNLOAD_SEGMENTS = self.cfg_segments_var.get()
            config.MAX_DISTANCE_THRESHOLD = self.cfg_distance_var.get()

            # Save into tracking.db settings table
            save_setting("BASE_DOWNLOAD_DIR", config.BASE_DOWNLOAD_DIR)
            save_setting("DEFAULT_QUALITY", config.DEFAULT_QUALITY)
            save_setting("DEFAULT_LANGUAGE", config.DEFAULT_LANGUAGE)
            save_setting("DEFAULT_PARALLEL_DOWNLOADS", config.DEFAULT_PARALLEL_DOWNLOADS)
            save_setting("FORCE_IPV4", config.FORCE_IPV4)
            save_setting("AUTO_RUN_SCANNER_ON_STARTUP", config.AUTO_RUN_SCANNER_ON_STARTUP)
            save_setting("OPEN_BROWSER_ON_FAIL", config.OPEN_BROWSER_ON_FAIL)
            save_setting("ENABLE_SEGMENTED_DOWNLOAD", config.ENABLE_SEGMENTED_DOWNLOAD)
            save_setting("DOWNLOAD_SEGMENTS", config.DOWNLOAD_SEGMENTS)
            save_setting("MAX_DISTANCE_THRESHOLD", config.MAX_DISTANCE_THRESHOLD)

            messagebox.showinfo("Settings Saved", "Settings successfully saved to database!")
        except Exception as e:
            messagebox.showerror("Save Error", f"Could not save settings: {e}")


def main():
    root = tk.Tk()
    app = AnimePaheGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
