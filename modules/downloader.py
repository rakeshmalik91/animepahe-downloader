import re
import os
import sys
import time
import socket
import webbrowser
import httpx
import cloudscraper
try:
    from curl_cffi import requests
except ImportError:
    import requests
import sqlite3
from tqdm import tqdm
import concurrent.futures
import threading
from datetime import datetime
import config

from .utils import log_debug

def _segmented_download(url, filename, headers, total_size, segments, position=None):
    slow_threshold = getattr(config, 'SLOW_DOWNLOAD_THRESHOLD_KBPS', 500)
    part_size = total_size // segments
    ranges = []
    for i in range(segments):
        start = i * part_size
        end = start + part_size - 1 if i < segments - 1 else total_size - 1
        ranges.append((start, end))
        
    part_files = [f"{filename}.part{i}" for i in range(segments)]
    abort_flag = threading.Event()
    lock = threading.Lock()
    speed_state = {'downloaded': 0, 'start_time': time.time()}

    total_existing = 0
    for part in part_files:
        if os.path.exists(part):
            total_existing += os.path.getsize(part)

    def download_part(i, start, end, bar):
        part_filename = part_files[i]
        existing_size = 0
        if os.path.exists(part_filename):
            existing_size = os.path.getsize(part_filename)
            
        part_total = end - start + 1
        if existing_size >= part_total:
            # We already fully downloaded this part
            with lock:
                speed_state['downloaded'] += part_total
            return True
            
        part_headers = headers.copy()
        part_headers['Range'] = f'bytes={start + existing_size}-{end}'
        
        from .db import get_kwik_session
        kwik_cookies, kwik_ua = get_kwik_session()
        
        session = requests.Session(impersonate="chrome124")
        if kwik_cookies:
            if isinstance(kwik_cookies, dict):
                domain_f = re.search(r'https?://([^/]+)', url).group(1) if re.search(r'https?://([^/]+)', url) else None
                for name, value in kwik_cookies.items():
                    session.cookies.set(name, value, domain=domain_f)
            else:
                for c in kwik_cookies:
                    session.cookies.set(c['name'], c['value'], domain=c.get('domain'))
        
        # Ensure UA consistency
        if kwik_ua:
            part_headers["User-Agent"] = kwik_ua

        try:
            r = session.get(url, headers=part_headers, stream=True, timeout=30)
            r.raise_for_status()
            # Fallback to wb if range is not respected, otherwise append
            mode = 'ab' if r.status_code == 206 else 'wb'
            with open(part_filename, mode) as f:
                for chunk in r.iter_content(chunk_size=1024*256):
                    if abort_flag.is_set():
                        return False
                    if chunk:
                        size = f.write(chunk)
                        with lock:
                            speed_state['downloaded'] += size
                            bar.update(size)
            r.close()
        except Exception as e:
            abort_flag.set()
            return False
        return True

    success = False
    try:
        with tqdm(
            desc=f"    [Seg] {os.path.basename(filename)[:34]}...",
            initial=total_existing,
            total=total_size,
            unit='iB',
            unit_scale=True,
            unit_divisor=1024,
            leave=False,
            position=position,
            file=sys.stdout
        ) as bar:
            with concurrent.futures.ThreadPoolExecutor(max_workers=segments) as executor:
                futures = [executor.submit(download_part, i, start, end, bar) for i, (start, end) in enumerate(ranges)]
                
                while not abort_flag.is_set():
                    if all(f.done() for f in futures):
                        break
                    time.sleep(1)
                    with lock:
                        elapsed = time.time() - speed_state['start_time']
                        if elapsed > 10:
                            speed_kbps = (speed_state['downloaded'] / 1024) / elapsed
                            if speed_kbps < slow_threshold:
                                log_debug(f"Segmented download slow ({speed_kbps:.2f} kbps), aborting.")
                                abort_flag.set()
                                
                success = all(f.result() if f.done() else False for f in futures) and not abort_flag.is_set()
                
        if success:
            with open(filename, 'wb') as outfile:
                for part_file in part_files:
                    with open(part_file, 'rb') as infile:
                        while True:
                            chunk = infile.read(1024*1024)
                            if not chunk: break
                            outfile.write(chunk)
                    os.remove(part_file)
            return True
            
    except Exception as e:
        log_debug(f"Segmented error: {e}")
        abort_flag.set()
        
    # Cleanup part files on failure/fallback to prevent disk clutter
    for part_file in part_files:
        if os.path.exists(part_file):
            try: os.remove(part_file)
            except: pass
    return False

def download_file(url, filename, referer, position=None):
    from .db import get_kwik_session
    kwik_cookies, kwik_ua = get_kwik_session()
    
    user_agent = kwik_ua if kwik_ua else getattr(config, 'USER_AGENT', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
    
    headers = {
        "User-Agent": user_agent,
        "Referer": "https://kwik.cx/",
        "Accept": "video/webm,video/ogg,video/*;q=0.9,application/ogg;q=0.7,audio/*;q=0.6,*/*;q=0.5",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "video",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
    }
    
    enable_sg = getattr(config, 'ENABLE_SEGMENTED_DOWNLOAD', False)
    
    session = requests.Session(impersonate="chrome124")
    if kwik_cookies:
        log_debug(f"Downloader using UA: {user_agent}")
        
        # Injected cookies with proper domain matching
        injected = 0
        url_domain = re.search(r'https?://([^/]+)', url).group(1) if re.search(r'https?://([^/]+)', url) else ""
        
        if isinstance(kwik_cookies, dict):
            for name, value in kwik_cookies.items():
                session.cookies.set(name, value, domain="kwik.cx")
                injected += 1
        else:
            for c in kwik_cookies:
                c_domain = c.get('domain', '')
                # Ensure cookies for .owocdn.top work on vault-*.owocdn.top
                session.cookies.set(c['name'], c['value'], domain=c_domain)
                injected += 1
        
        log_debug(f"Downloader injected {injected} cookies.")

    try:
        # Initial request to get headers and size
        # Force the User-Agent to match exactly what solved CF
        headers["User-Agent"] = user_agent
        r = session.get(url, headers=headers, stream=True, timeout=60)
        if r.status_code != 200:
            title_match = re.search(r'<title>(.*?)</title>', r.text, re.IGNORECASE)
            title = title_match.group(1).strip() if title_match else "No Title"
            log_debug(f"Direct download failed: Status {r.status_code}, Title: '{title}', Body snippet: {r.text[:200]}")
            r.close()
            return False
        
        total_size = int(r.headers.get('content-length', 0))
        accept_ranges = r.headers.get('accept-ranges', '').lower() == 'bytes'
        r.close()
            
        if os.path.exists(filename) and os.path.getsize(filename) == total_size:
            return True
            
        segments = getattr(config, 'DOWNLOAD_SEGMENTS', 4)
        if enable_sg and accept_ranges and total_size > 1024 * 1024 and segments > 1:
            if _segmented_download(url, filename, headers, total_size, segments, position=position):
                return True
            log_debug("Segmented download failed or was too slow, falling back to normal loop")
            
        # Fallback to normal stream downloading
        existing_size = os.path.getsize(filename) if os.path.exists(filename) else 0
        fallback_mode = 'wb'
        if existing_size > 0 and accept_ranges:
            headers['Range'] = f'bytes={existing_size}-'
            fallback_mode = 'ab'
        else:
            existing_size = 0

        r = session.get(url, headers=headers, stream=True, timeout=60)
        r.raise_for_status()
        with open(filename, fallback_mode) as f, tqdm(
            desc=f"    {os.path.basename(filename)[:40]}...",
            initial=existing_size,
            total=total_size,
            unit='iB',
            unit_scale=True,
            unit_divisor=1024,
            leave=False,
            position=position,
            file=sys.stdout
        ) as bar:
            for chunk in r.iter_content(chunk_size=1024*1024):
                if chunk:
                    size = f.write(chunk)
                    bar.update(size)
        r.close()
        return True
    except Exception as e:
        log_debug(f"Download error: {e}")
        return False

