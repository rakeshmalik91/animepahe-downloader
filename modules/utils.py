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

def _ensure_working_site_mirror(client, site_type, verbose=False):
    """Generic mirror checker for AnimePahe or Kwik."""
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
        mirrors = config.JIKAN_API_URLS.copy()
        current_url = getattr(config, "JIKAN_API_URL", last_working or mirrors[0])
        display_name = "Jikan"
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

