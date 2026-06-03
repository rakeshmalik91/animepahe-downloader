import re
import os
import sys
import time
import socket
import webbrowser
import httpx
import cloudscraper
import requests
import sqlite3
from tqdm import tqdm
import concurrent.futures
import threading
from datetime import datetime
import config

from .utils import log_debug, normalize_path

def init_db():
    conn = sqlite3.connect(config.DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tracking 
                 (folder_path TEXT PRIMARY KEY, anime_id TEXT, anime_title TEXT, auto_download INTEGER, last_updated TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS sessions 
                 (site TEXT PRIMARY KEY, cookies TEXT, user_agent TEXT, last_updated TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS mirrors 
                 (site_type TEXT PRIMARY KEY, url TEXT, last_updated TEXT)''')
    conn.commit()
    conn.close()

def get_tracked(folder_path):
    """Get tracking info with normalization fallback and ancestral detection.
    Returns (anime_id, anime_title, auto_download, last_updated) or None."""
    try:
        conn = sqlite3.connect(config.DB_PATH)
        c = conn.cursor()
        
        # 1. Direct match for current folder (fast path)
        c.execute("SELECT anime_id, anime_title, auto_download, last_updated FROM tracking WHERE folder_path = ?", (folder_path,))
        row = c.fetchone()
        if row:
            conn.close()
            return row
        
        # 2. Fuzzy match by normalized path (covers casing/slashes)
        norm_target = normalize_path(folder_path)
        c.execute("SELECT folder_path, anime_id, anime_title, auto_download, last_updated FROM tracking")
        db_entries = c.fetchall()
        
        for db_path, aid, title, auto, last_upd in db_entries:
            if normalize_path(db_path) == norm_target:
                conn.close()
                return (aid, title, auto, last_upd)

        # 3. Check if any PARENT folder is tracked (skip or active) using fuzzy matching
        # Move up the tree and check each parent
        current = os.path.dirname(os.path.abspath(folder_path))
        base = os.path.abspath(config.BASE_DOWNLOAD_DIR)
        
        while len(current) >= len(base) and current != base:
            norm_current = normalize_path(current)
            for db_path, aid, title, auto, last_upd in db_entries:
                if normalize_path(db_path) == norm_current:
                    conn.close()
                    return (aid, title, auto, last_upd)
            
            next_parent = os.path.dirname(current)
            if next_parent == current: break # Root reached
            current = next_parent

        conn.close()
        return None
    except Exception as e:
        log_debug(f"DB get_tracked error: {e}")
        return None

def save_tracked(folder_path, anime_id, anime_title, auto_download, update_time=True):
    from datetime import datetime
    conn = sqlite3.connect(config.DB_PATH)
    c = conn.cursor()
    ts = datetime.now().isoformat() if update_time else None
    c.execute("REPLACE INTO tracking VALUES (?, ?, ?, ?, ?)", 
              (folder_path, anime_id, anime_title, 1 if auto_download else 0, ts))
    conn.commit()
    conn.close()

def update_last_checked(folder_path):
    """Update only the last_updated timestamp without changing other fields."""
    from datetime import datetime
    conn = sqlite3.connect(config.DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE tracking SET last_updated = ? WHERE folder_path = ?",
              (datetime.now().isoformat(), folder_path))
    conn.commit()
    conn.close()

def cleanup_db():
    """Remove tracking entries for folders that no longer exist on disk."""
    try:
        conn = sqlite3.connect(config.DB_PATH)
        c = conn.cursor()
        c.execute("SELECT folder_path FROM tracking")
        rows = c.fetchall()
        deleted_count = 0
        for (folder_path,) in rows:
            if not os.path.exists(folder_path):
                c.execute("DELETE FROM tracking WHERE folder_path = ?", (folder_path,))
                deleted_count += 1
        conn.commit()
        conn.close()
        if deleted_count > 0:
            log_debug(f"Cleaned up {deleted_count} stale database entries.")
            tqdm.write(f"Cleaned up {deleted_count} stale database entries.", file=sys.stdout)
    except Exception as e:
        log_debug(f"DB cleanup error: {e}")

def get_folder_by_id(anime_id):
    """Retrieve the folder path associated with an anime ID from the DB, verifying disk existence."""
    if not anime_id: return None
    try:
        conn = sqlite3.connect(config.DB_PATH)
        c = conn.cursor()
        c.execute("SELECT folder_path FROM tracking WHERE anime_id = ? AND auto_download = 1", (anime_id,))
        rows = c.fetchall()
        conn.close()
        for (folder_path,) in rows:
            if os.path.exists(folder_path):
                return folder_path
        return None
    except: return None

def get_kwik_session():
    """Returns (cookies_list, user_agent) or (None, None).
    cookies_list is a list of dicts: [{'name':..., 'value':..., 'domain':...}, ...]
    """
    try:
        import json
        conn = sqlite3.connect(config.DB_PATH)
        c = conn.cursor()
        c.execute("SELECT cookies, user_agent FROM sessions WHERE site = 'kwik'")
        row = c.fetchone()
        conn.close()
        if row:
            return json.loads(row[0]), row[1]
    except: pass
    return None, None

def save_kwik_session(cookies, ua):
    """Save kwik cookies and UA to DB."""
    try:
        import json
        from datetime import datetime
        conn = sqlite3.connect(config.DB_PATH)
        c = conn.cursor()
        c.execute("REPLACE INTO sessions VALUES ('kwik', ?, ?, ?)", 
                  (json.dumps(cookies), ua, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        log_debug(f"DB save_kwik_session error: {e}")
        return False

def get_animepahe_session():
    """Returns (cookies_list, user_agent) or (None, None)."""
    try:
        import json
        conn = sqlite3.connect(config.DB_PATH)
        c = conn.cursor()
        c.execute("SELECT cookies, user_agent FROM sessions WHERE site = 'animepahe'")
        row = c.fetchone()
        conn.close()
        if row:
            return json.loads(row[0]), row[1]
    except: pass
    return None, None

def save_animepahe_session(cookies, ua):
    """Save animepahe cookies and UA to DB."""
    try:
        import json
        from datetime import datetime
        conn = sqlite3.connect(config.DB_PATH)
        c = conn.cursor()
        c.execute("REPLACE INTO sessions VALUES ('animepahe', ?, ?, ?)", 
                  (json.dumps(cookies), ua, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        log_debug(f"DB save_animepahe_session error: {e}")
        return False

def get_last_working_mirror(site_type):
    """Returns the last working URL for a site type (animepahe or kwik)."""
    try:
        conn = sqlite3.connect(config.DB_PATH)
        c = conn.cursor()
        c.execute("SELECT url FROM mirrors WHERE site_type = ?", (site_type,))
        row = c.fetchone()
        conn.close()
        if row: return row[0]
    except: pass
    return None

def save_working_mirror(site_type, url):
    """Save the working URL for a site type."""
    try:
        from datetime import datetime
        conn = sqlite3.connect(config.DB_PATH)
        c = conn.cursor()
        c.execute("REPLACE INTO mirrors VALUES (?, ?, ?)", 
                  (site_type, url, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        log_debug(f"DB save_working_mirror error: {e}")
        return False

