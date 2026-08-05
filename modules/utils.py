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

PROMPT_HANDLER = None

def set_prompt_handler(handler):
    global PROMPT_HANDLER
    PROMPT_HANDLER = handler

def prompt_user(prompt_text, default="n"):
    global PROMPT_HANDLER
    if PROMPT_HANDLER is not None:
        try:
            res = PROMPT_HANDLER(prompt_text, default)
            return res.strip().lower() if isinstance(res, str) else res
        except Exception as e:
            log_debug(f"Prompt handler error: {e}")
            return default
    try:
        return input(prompt_text).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return default

def log_debug(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        content = ""
        if os.path.exists(config.LOG_PATH):
            with open(config.LOG_PATH, "r", encoding="utf-8") as f:
                content = f.read()
        with open(config.LOG_PATH, "w", encoding="utf-8") as f:
            f.write(line + content)
    except Exception:
        pass

def load_animepahe_session_to_client(client):
    try:
        from .db import get_animepahe_session
        cookies, ua = get_animepahe_session()
        if cookies:
            for c in cookies:
                client.cookies.set(c['name'], c['value'], domain=c.get('domain'))
            log_debug("Loaded cached AnimePahe session cookies.")
        if ua:
            client.headers.update({"User-Agent": ua})
    except Exception as e:
        log_debug(f"Failed to load AnimePahe session: {e}")

def ensure_working_mirror(client, verbose=False):
    return _ensure_working_site_mirror(client, "animepahe", verbose)

def ensure_working_kwik_mirror(client, verbose=False):
    return _ensure_working_site_mirror(client, "kwik", verbose)

def ensure_working_jikan_mirror(client, verbose=False):
    return _ensure_working_site_mirror(client, "jikan", verbose)

def ensure_working_anilist_mirror(client, verbose=False):
    return _ensure_working_site_mirror(client, "anilist", verbose)

def ensure_working_kitsu_mirror(client, verbose=False):
    return _ensure_working_site_mirror(client, "kitsu", verbose)

def _ensure_working_site_mirror(client, site_type, verbose=False):
    """Generic mirror checker for AnimePahe, Kwik, Jikan, AniList, or Kitsu."""
    from .db import get_last_working_mirror, save_working_mirror
    
    if site_type == "animepahe":
        load_animepahe_session_to_client(client)
        # First priority: check database for last known working mirror
        last_working = get_last_working_mirror("animepahe")
        mirrors = config.ANIMEPAHE_URLS.copy()
        current_url = getattr(config, "ANIMEPAHE_URL", last_working or mirrors[0])
        display_name = "AnimePahe"
    elif site_type == "jikan":
        last_working = get_last_working_mirror("jikan")
        mirrors = getattr(config, "JIKAN_API_URLS", ["https://api.jikan.moe/v4"]).copy()
        current_url = getattr(config, "JIKAN_API_URL", last_working or mirrors[0])
        display_name = "Jikan"
    elif site_type == "anilist":
        last_working = get_last_working_mirror("anilist")
        mirrors = getattr(config, "ANILIST_API_URLS", ["https://graphql.anilist.co"]).copy()
        current_url = getattr(config, "ANILIST_API_URL", last_working or mirrors[0])
        display_name = "AniList"
    elif site_type == "kitsu":
        last_working = get_last_working_mirror("kitsu")
        mirrors = getattr(config, "KITSU_API_URLS", ["https://kitsu.io/api/edge"]).copy()
        current_url = getattr(config, "KITSU_API_URL", last_working or mirrors[0])
        display_name = "Kitsu"
    else:
        last_working = get_last_working_mirror("kwik")
        mirrors = config.KWIK_URLS.copy()
        current_url = getattr(config, "KWIK_URL", last_working or mirrors[0])
        display_name = "Kwik"

    if verbose: tqdm.write(f"Checking {display_name} mirrors...", file=sys.stdout)
    
    # Priority order: current_url, last_working (if different), then others
    ordered_mirrors = []
    if current_url: ordered_mirrors.append(current_url)
    if last_working and last_working not in ordered_mirrors:
        ordered_mirrors.append(last_working)
    
    for m in mirrors:
        if m not in ordered_mirrors:
            ordered_mirrors.append(m)

    working_mirror_found = False
    first_cf_blocked_mirror = None
    for mirror in ordered_mirrors:
        try:
            if verbose: tqdm.write(f" - {mirror.replace('https://', '')}...", end=' ', file=sys.stdout)
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": f"{mirror}/"
            }
            res = client.get(mirror, headers=headers, timeout=7)
            
            if res.status_code == 403 and site_type == "animepahe":
                if not first_cf_blocked_mirror:
                    first_cf_blocked_mirror = mirror
                    
            # AnimePahe returns 403 for Cloudflare challenges. We only accept status < 400.
            is_ok = res.status_code < 400 if site_type == "animepahe" else res.status_code < 500
            if is_ok:
                if hasattr(res.url, 'scheme'):
                    # httpx style
                    path = getattr(res.url, 'path', '')
                    final_url = f"{res.url.scheme}://{res.url.host}{path}".rstrip('/')
                    host = res.url.host
                else:
                    # requests/cloudscraper style
                    from urllib.parse import urlparse
                    parsed = urlparse(res.url)
                    final_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip('/')
                    host = parsed.netloc

                if verbose: 
                    if final_url.rstrip('/') != mirror.rstrip('/'):
                        tqdm.write(f"OK (redirected to {host})", file=sys.stdout)
                    else:
                        tqdm.write("OK", file=sys.stdout)
                
                # Save as working mirror in DB
                save_working_mirror(site_type, mirror)
                
                if site_type == "animepahe":
                    config.ANIMEPAHE_URL = final_url
                    client.cookies.set("__ddg2_", "", domain=host)
                    client.headers.update({"Referer": f"{final_url}/"})
                elif site_type == "jikan":
                    config.JIKAN_API_URL = final_url
                elif site_type == "anilist":
                    config.ANILIST_API_URL = final_url
                elif site_type == "kitsu":
                    config.KITSU_API_URL = final_url
                else:
                    config.KWIK_URL = final_url
                
                log_debug(f"Selected working {display_name} mirror: {final_url} (was {mirror})")
                working_mirror_found = True
                return True
            else:
                if verbose: tqdm.write(f"FAIL ({res.status_code})", file=sys.stdout)
        except Exception as e:
            if verbose: tqdm.write("FAIL", file=sys.stdout)
            log_debug(f"{display_name} mirror {mirror} failed: {e}")
            continue
    
    if site_type == "animepahe" and not working_mirror_found:
        bypass_target = first_cf_blocked_mirror or current_url
        try:
            from .scraper import get_browser_cookies
            from .db import save_animepahe_session
            tqdm.write(f"\nAll mirrors return 403. Opening browser to solve Cloudflare challenge...", file=sys.stdout)
            cookies, ua, _ = get_browser_cookies(bypass_target)
            if cookies and ua:
                save_animepahe_session(cookies, ua)
                load_animepahe_session_to_client(client)
                
                tqdm.write(f" - {bypass_target.replace('https://', '')} (after bypass)...", end=' ', file=sys.stdout)
                headers = {
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Referer": f"{bypass_target}/"
                }
                res = client.get(bypass_target, headers=headers, timeout=7)
                if res.status_code < 400:
                    if verbose: tqdm.write("OK", file=sys.stdout)
                    save_working_mirror("animepahe", bypass_target)
                    config.ANIMEPAHE_URL = bypass_target
                    log_debug(f"Selected working AnimePahe mirror after bypass: {bypass_target}")
                    return True
                else:
                    if verbose: tqdm.write(f"FAIL ({res.status_code})", file=sys.stdout)
        except Exception as e:
            log_debug(f"AnimePahe browser bypass failed: {e}")
            
    return False

def normalize_path(path):
    """Normalize colons, slashes and case for robust comparison."""
    if not path: return ""
    # Replace all colon variations with spaces
    n = path.replace('：', ' ').replace(':', ' ').lower()
    # Collapse multiple spaces and handle path separators
    n = re.sub(r'\s+', ' ', n).strip()
    return os.path.normpath(n)

def get_latest_episode_local(folder):
    if not os.path.exists(folder): return -1
    max_ep = -1
    patterns = [r' - (\d+)', r'_-_(\d+)_', r'Episode (\d+)', r'\[(\d+)\]', r'\((\d+)\)']
    for root, dirs, files in os.walk(folder):
        for f in files:
            for pattern in patterns:
                match = re.search(pattern, f)
                if match:
                    try:
                        ep = int(match.group(1))
                        if ep > max_ep: max_ep = ep
                        break 
                    except ValueError: continue
    return max_ep

def is_episode_already_present(folder, ep_num, anime_title):
    """Check if a specific episode number exists anywhere in the folder tree."""
    # Stricter patterns to avoid matching "Season 4" as "Episode 4"
    num_patterns = [
        rf'_-_0*{ep_num}_',           # AnimePahe style
        rf'\s-\s0*{ep_num}(?:\s|\[)', # common " - 04" style
        rf'Episode\s+0*{ep_num}\b',   # "Episode 04" style
        rf'\[0*{ep_num}\]',           # "[04]" style
        rf'\(0*{ep_num}\)'            # "(04)" style
    ]
    for root, dirs, files in os.walk(folder):
        for f in files:
            if not (f.lower().endswith('.mp4') or f.lower().endswith('.mkv')):
                continue
            for patt in num_patterns:
                if re.search(patt, f, re.IGNORECASE):
                    return True
    return False

def detect_lang_from_files(folder_path):
    """Detect language preference from existing filenames. Returns 'en', 'jap', or None."""
    eng_indicators = ['eng_dub', 'eng.dub', 'english', 'yameii', '_eng_', '.eng.']
    jap_indicators = ['subsplease', 'judas', 'erai-raws', '_jpn_', '_jap_', 'horriblesubs']
    for root, _, files in os.walk(folder_path):
        for f in files:
            if not (f.endswith('.mp4') or f.endswith('.mkv')): continue
            fl = f.lower()
            for ind in eng_indicators:
                if ind in fl: return 'en'
            for ind in jap_indicators:
                if ind in fl: return 'jap'
    return None

def send_windows_notification(title, message, folder_path=None):
    if os.name != 'nt':
        return
    if not getattr(config, 'ENABLE_NOTIFICATIONS', True):
        return
    try:
        import subprocess
        import base64
        
        if folder_path:
            folder_path_escaped = folder_path.replace('"', '""').replace("'", "''")
            click_action = f"""
            $action = {{
                Start-Process -FilePath "explorer.exe" -ArgumentList '"{folder_path_escaped}"'
                $global:clicked = $true
            }}
            Register-ObjectEvent -InputObject $notify -EventName BalloonTipClicked -Action $action | Out-Null
            """
        else:
            click_action = ""

        ps_script = f"""
        Add-Type -AssemblyName System.Windows.Forms
        $notify = New-Object System.Windows.Forms.NotifyIcon
        $notify.Icon = [System.Drawing.SystemIcons]::Information
        $notify.Visible = $true
        
        {click_action}
        
        $notify.ShowBalloonTip(5000, "{title}", "{message}", [System.Windows.Forms.ToolTipIcon]::None)
        
        $global:clicked = $false
        $timeout = [DateTime]::Now.AddSeconds(7)
        while (([DateTime]::Now -lt $timeout) -and (-not $global:clicked)) {{
            [System.Windows.Forms.Application]::DoEvents()
            Start-Sleep -Milliseconds 100
        }}
        
        $notify.Visible = $false
        $notify.Dispose()
        """
        encoded = base64.b64encode(ps_script.encode('utf-16le')).decode('utf-8')
        subprocess.Popen(['powershell', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded], 
                         creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        log_debug(f"Failed to send notification: {e}")

def is_season_folder_name(name):
    """
    Returns True if the given folder name represents a season, part, cour, OVA, special, or movie subfolder.
    """
    if not name:
        return False
    # Strip any trailing year tag if accidentally present (e.g. "Season 1 (2020)")
    clean_name = re.sub(r'\s*\(\d{4}(?:-\d{4}|-)?\)$', '', name.strip())
    
    patterns = [
        r'^(?:Season|S|Part|Cour)\s*\d+$',
        r'^\d+(?:st|nd|rd|th)\s*Season$',
        r'^(?:Season|S)\s*\d+(?:\s*(?:Part|Cour)\s*\d+)?$',
        r'^(?:Part|Cour)\s*\d+$',
        r'^(?:OVA|OVAs|Special|Specials|Movie|Movies)$'
    ]
    return any(re.match(p, clean_name, re.IGNORECASE) for p in patterns)

def parse_year_tag(start_year, end_year=None, status=None, is_ongoing=False):
    """
    Format year tag string:
    - (2020)
    - (2020-2024)
    - (2020-)
    """
    if not start_year:
        return ""
    try:
        sy = int(start_year)
    except (ValueError, TypeError):
        return ""
        
    ey = None
    if end_year:
        try:
            ey = int(end_year)
        except (ValueError, TypeError):
            ey = None
            
    ongoing = is_ongoing
    if status and isinstance(status, str):
        st_lower = status.lower()
        if any(x in st_lower for x in ['currently airing', 'releasing', 'ongoing']):
            ongoing = True
            
    if ongoing and (ey is None or ey >= datetime.now().year):
        return f"({sy}-)"
    elif ey and ey != sy:
        return f"({sy}-{ey})"
    else:
        return f"({sy})"

def format_anime_folder_name(folder_name, year_tag=""):
    """
    Appends year_tag at end of anime folder name if not already there.
    Ensures year_tag is NOT added to season folders (and strips year from season folders if present).
    """
    if not folder_name:
        return folder_name
        
    folder_name = folder_name.strip()
    
    # If folder is a season folder (e.g. "Season 1"), never add year and strip year if present
    if is_season_folder_name(folder_name):
        return re.sub(r'\s*\(\d{4}(?:-\d{4}|-)?\)$', '', folder_name).strip()
        
    if not year_tag:
        return folder_name
        
    year_tag = year_tag.strip()
    
    # Check if folder_name already ends with a valid year tag format: (2020), (2020-2024), (2020-)
    if re.search(r'\(\d{4}(?:-\d{4}|-)?\)$', folder_name):
        return folder_name
        
    return f"{folder_name} {year_tag}".strip()

def get_anime_parent_folder(folder_path):
    """
    Finds the main anime directory path for folder_path.
    """
    if not folder_path:
        return folder_path
        
    base = os.path.abspath(getattr(config, 'BASE_DOWNLOAD_DIR', ''))
    folder_abs = os.path.abspath(folder_path)
    
    if not base or not folder_abs.startswith(base + os.sep):
        if is_season_folder_name(os.path.basename(folder_abs)):
            return os.path.dirname(folder_abs)
        return folder_abs
        
    rel = folder_abs[len(base):].strip('\\/')
    parts = [p for p in rel.split(os.sep) if p]
    
    if len(parts) >= 2 and is_season_folder_name(parts[-1]):
        return os.path.dirname(folder_abs)
    elif len(parts) >= 2:
        return os.path.join(base, parts[0])
    else:
        return folder_abs

def ensure_folder_year(folder_path, anime_title=None, anime_id=None, meta=None, client=None):
    """
    Renames top-level anime folder to include release year tag (starting year) at the end if missing or incorrect.
    Ensures season folders do NOT have year tag appended (and strips year from season folders if present).
    Returns updated folder_path.
    """
    if not folder_path or not os.path.exists(folder_path):
        return folder_path

    if not getattr(config, 'ENABLE_YEAR_TAGS', True):
        return folder_path

    # Clean season folder name if it accidentally has a year tag attached

    folder_name = os.path.basename(folder_path)
    parent_dir = os.path.dirname(folder_path)
    if is_season_folder_name(folder_name):
        clean_season_name = format_anime_folder_name(folder_name, "")
        if clean_season_name != folder_name:
            new_season_path = os.path.join(parent_dir, clean_season_name)
            if not os.path.exists(new_season_path):
                try:
                    os.rename(folder_path, new_season_path)
                    from .db import rename_tracked_folder
                    rename_tracked_folder(folder_path, new_season_path)
                    folder_path = new_season_path
                except Exception as e:
                    log_debug(f"Error cleaning season folder name: {e}")

    target_anime_folder = get_anime_parent_folder(folder_path)
    if not os.path.exists(target_anime_folder):
        return folder_path

    anime_folder_name = os.path.basename(target_anime_folder)
    anime_parent_dir = os.path.dirname(target_anime_folder)

    # Clean base anime title by removing any existing year tag: e.g. "Bleach Thousand-Year Blood War (2026-)" -> "Bleach Thousand-Year Blood War"
    clean_anime_title = re.sub(r'\s*\(\d{4}(?:-\d{4}|-)?\)$', '', anime_folder_name).strip()

    years = []
    is_ongoing = False

    # 1. From meta if provided
    if meta:
        if meta.get('year'):
            try: years.append(int(meta.get('year')))
            except (ValueError, TypeError): pass
        if meta.get('status'):
            st = str(meta.get('status')).lower()
            if any(x in st for x in ['currently airing', 'releasing', 'ongoing']):
                is_ongoing = True

    # 2. Search AnimePahe API for all entries matching the base anime title to get franchise years
    if client:
        query_title = anime_title or clean_anime_title
        query_clean = re.sub(r'\s*\(\d{4}[^)]*\)', '', query_title).strip()
        query_clean = query_clean.replace('：', ' ').replace(':', ' ')
        try:
            from .scraper import search_anime
            results, api_ok = search_anime(client, query_clean, return_all=True)
            if results:
                for r_aid, r_clean, r_title, r_meta in results:
                    if r_meta and r_meta.get('year'):
                        try: years.append(int(r_meta.get('year')))
                        except (ValueError, TypeError): pass
                    if r_meta and r_meta.get('status'):
                        st = str(r_meta.get('status')).lower()
                        if any(x in st for x in ['currently airing', 'releasing', 'ongoing']):
                            is_ongoing = True
        except Exception as e:
            log_debug(f"Search API year fetch error: {e}")

    # 3. Check physical subfolders for year indicators
    try:
        for sub in os.listdir(target_anime_folder):
            m_sub_y = re.search(r'\b(19|20)\d{2}\b', sub)
            if m_sub_y:
                years.append(int(m_sub_y.group(0)))
    except Exception:
        pass

    # 4. Extract year from title string if available
    if anime_title:
        m_title_y = re.findall(r'\b(19|20)\d{2}\b', anime_title)
        for y_str in m_title_y:
            years.append(int(y_str))

    if not years:
        return folder_path

    start_year = min(years)
    latest_year = max(years)

    year_tag = parse_year_tag(start_year, latest_year if not is_ongoing else None, is_ongoing=is_ongoing)
    if not year_tag:
        return folder_path

    new_anime_folder_name = f"{clean_anime_title} {year_tag}".strip()
    if new_anime_folder_name != anime_folder_name:
        new_anime_path = os.path.join(anime_parent_dir, new_anime_folder_name)
        if not os.path.exists(new_anime_path):
            try:
                os.rename(target_anime_folder, new_anime_path)
                from .db import rename_tracked_folder
                rename_tracked_folder(target_anime_folder, new_anime_path)
                log_debug(f"Renamed anime folder with correct year tag: '{anime_folder_name}' -> '{new_anime_folder_name}'")
                
                if os.path.abspath(folder_path) == os.path.abspath(target_anime_folder):
                    return new_anime_path
                else:
                    rel_sub = os.path.relpath(folder_path, target_anime_folder)
                    return os.path.join(new_anime_path, rel_sub)
            except Exception as e:
                log_debug(f"Error renaming anime folder: {e}")

    return folder_path





