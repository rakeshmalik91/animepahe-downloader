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

from .utils import log_debug, detect_lang_from_files, get_latest_episode_local, is_episode_already_present, send_windows_notification, ensure_working_mirror
from .db import update_last_checked, save_tracked, get_tracked
from .scraper import search_anime, get_direct_link, resolve_kwik_direct
from .downloader import download_file

def process_one_folder(client, folder_path, anime_id=None, anime_title=None, quality="720p", lang=None, episodes_filter=None, parallel=1):
    scraper = cloudscraper.create_scraper(browser={'browser': 'firefox', 'platform': 'windows', 'mobile': False})
    pos_lock = threading.Lock()
    if not anime_id:
        folder_name = os.path.basename(folder_path)
        # Normalize folder name colons for query
        search_query = folder_name.replace('：', ' ').replace(':', ' ')
        # Clean query: Remove common release noise
        search_query = re.sub(r'(?i)\s+(720p|1080p|360p|SubsPlease|Dual-Audio|BD|Web-DL|H\.264|x264|HEVC)', '', search_query).strip()
        search_query = re.sub(r'\[.*?\]|\(.*?\)', '', search_query).strip()
        if not search_query: return False, None, None
        tqdm.write(f"\nSearching for series: '{search_query}'...", file=sys.stdout)
        anime_id, anime_title, _, dist = search_anime(client, search_query)
        if anime_id and dist > getattr(config, 'MAX_DISTANCE_THRESHOLD', 20):
            tqdm.write(f"  [Warning] Best match '{anime_title}' has a high name distance from '{search_query}'.", file=sys.stdout)
            ans = input(f"  Is '{anime_title}' correct? [y(es)/n(o)/u(rl)]: ").lower()
            if ans == 'u':
                new_url = input("    Enter AnimePahe URL: ").strip()
                match = re.search(r'/anime/([a-f0-9-]+)', new_url)
                if match:
                    anime_id = match.group(1)
                    anime_title = None # Will fetch later
            elif ans == 'n':
                anime_id, anime_title = None, None
    
    # If we have an ID but still no title (e.g. from --url), fetch it from the page
    if anime_id and not anime_title:
        try:
            res = client.get(f"{config.ANIMEPAHE_URL}/anime/{anime_id}", timeout=15)
            if res.status_code == 200:
                match = re.search(r'<h1.*?><span.*?>(.*?)</span>', res.text, re.DOTALL)
                if not match:
                    match = re.search(r'<h1>(.*?)<span>', res.text)
                if not match:
                    match = re.search(r'<title>(.*?)\s*(?:::|-)\s*animepahe</title>', res.text, re.IGNORECASE)
                if match:
                    anime_title = match.group(1).strip()
                    anime_title = re.sub(r'[\\/*?:"<>|：]', ' ', anime_title)
                    anime_title = re.sub(r'\s+', ' ', anime_title).strip()
        except: pass
        if not anime_title: anime_title = os.path.basename(folder_path)

    last_ep = get_latest_episode_local(folder_path) or 0
    try:
        rel_folder = os.path.relpath(folder_path, config.BASE_DOWNLOAD_DIR)
    except ValueError:
        rel_folder = os.path.basename(folder_path)
    tqdm.write(f"\nChecking updates for {anime_title} (id: {anime_id})\n  in '{rel_folder}' (last: {last_ep})...", file=sys.stdout)
    
    # Silent Season Consistency Check
    folder_name = os.path.basename(folder_path)
    parent_name = os.path.basename(os.path.dirname(folder_path))
    f_s_match = re.search(r'(?:Season|S)\s*(\d+)', folder_name, re.IGNORECASE)
    if not f_s_match and parent_name:
         f_s_match = re.search(r'(?:Season|S)\s*(\d+)', parent_name, re.IGNORECASE)
         
    if f_s_match:
         f_s = f_s_match.group(1)
         t_s_match = re.search(r'(?:Season|S)\s*(\d+)', anime_title.replace('_', ' '), re.IGNORECASE)
         t_s = t_s_match.group(1) if t_s_match else "1"
         
         if f_s != t_s:
              search_q = f"{parent_name if parent_name and len(parent_name)>3 else folder_name} {folder_name}"
              search_q = re.sub(r'\s*\(\d{4}[^)]*\)', '', search_q).strip()
              new_id, new_title, _, dist = search_anime(client, search_q)
              
              if new_id and dist > getattr(config, 'MAX_DISTANCE_THRESHOLD', 20):
                  # This is a silent background check. If the distance is too high, 
                  # the match is likely wrong, so just ignore it without prompting.
                  new_id, new_title = None, None
                  
              if new_id and new_id != anime_id:
                   n_s_match = re.search(r'(?:Season|S)\s*(\d+)', new_title.replace('_', ' '), re.IGNORECASE)
                   n_s = n_s_match.group(1) if n_s_match else "1"
                   if n_s == f_s:
                        # Guard: verify the new title's base name is actually related to
                        # the folder's expected anime, not just a different anime that
                        # happens to share the same season number (e.g. "Kingdom Season 4"
                        # vs "Re Zero Season 4").
                        import Levenshtein as _lev
                        new_base = re.sub(r'(?:Season|S)\s*\d+', '', new_title, flags=re.IGNORECASE).strip()
                        expected_base = re.sub(r'(?:Season|S)\s*\d+', '', parent_name if parent_name and len(parent_name) > 3 else folder_name, flags=re.IGNORECASE)
                        expected_base = re.sub(r'\s*\(\d{4}[^)]*\)', '', expected_base).strip()
                        nb_norm = re.sub(r'[^a-z0-9 ]', ' ', new_base.lower()).strip()
                        eb_norm = re.sub(r'[^a-z0-9 ]', ' ', expected_base.lower()).strip()
                        base_dist = _lev.distance(nb_norm, eb_norm)
                        base_threshold = getattr(config, 'MAX_DISTANCE_THRESHOLD', 20)
                        if base_dist > base_threshold:
                             log_debug(f"Season consistency check: rejected '{new_title}' for '{folder_name}' "
                                       f"(base title distance {base_dist} > {base_threshold}: '{nb_norm}' vs '{eb_norm}')")
                        else:
                             tqdm.write(f"  Note: Correcting mismatched ID for '{folder_name}'.", file=sys.stdout)
                             tqdm.write(f"        Switching from '{anime_title}' -> '{new_title}'.", file=sys.stdout)
                             save_tracked(folder_path, new_id, new_title, True)
                             anime_id, anime_title = new_id, new_title
                             last_ep = get_latest_episode_local(folder_path) or 0
    
    try:
        anime_page_url = f"{config.ANIMEPAHE_URL}/anime/{anime_id}"
        api_url = f"{config.ANIMEPAHE_URL}/api?m=release&id={anime_id}&sort=episode_desc&page=1"
        res = client.get(api_url, headers={
            "Referer": anime_page_url,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01"
        })
        
        if res.status_code != 200:
            log_debug(f"Release API error (Status {res.status_code}). Attempting mirror rotation...")
            if ensure_working_mirror(client):
                api_url = f"{config.ANIMEPAHE_URL}/api?m=release&id={anime_id}&sort=episode_desc&page=1"
                res = client.get(api_url, headers={
                    "Referer": anime_page_url,
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01"
                })
        
        if res.status_code == 404:
            search_query = anime_title if anime_title else os.path.basename(folder_path)
            search_query = search_query.replace('：', ' ').replace(':', ' ')
            new_id, new_title, _, dist = search_anime(client, search_query)
            if new_id and dist > getattr(config, 'MAX_DISTANCE_THRESHOLD', 20):
                tqdm.write(f"  [Warning] Best match '{new_title}' has a high name distance from '{search_query}'.", file=sys.stdout)
                ans = input(f"  Is '{new_title}' correct? [y(es)/n(o)/u(rl)]: ").lower()
                if ans == 'u':
                    new_url = input("    Enter AnimePahe URL: ").strip()
                    match = re.search(r'/anime/([a-f0-9-]+)', new_url)
                    if match:
                        new_id = match.group(1)
                        new_title = anime_title
                elif ans == 'n':
                    new_id, new_title = None, None
            if new_id and new_id != anime_id:
                save_tracked(folder_path, new_id, new_title, True)
                anime_id, anime_title = new_id, new_title
                anime_page_url = f"{config.ANIMEPAHE_URL}/anime/{anime_id}"
                api_url = f"{config.ANIMEPAHE_URL}/api?m=release&id={anime_id}&sort=episode_desc&page=1"
                res = client.get(api_url, headers={
                    "Referer": anime_page_url,
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01"
                })
        
        if res.status_code != 200:
            tqdm.write(f"  Error: Could not reach API (Status {res.status_code}).", file=sys.stdout)
            return False, anime_id, anime_title

        try:
            data = res.json()
        except:
            tqdm.write("  Error: API blocked by security challenge.", file=sys.stdout)
            return False, anime_id, anime_title

        episodes = data.get('data', [])
        episodes.sort(key=lambda x: x.get('episode', 0))
        
        if episodes_filter:
            new_episodes = [ep for ep in episodes if ep.get('episode', 0) in episodes_filter]
        else:
            new_episodes = [ep for ep in episodes if ep.get('episode', 0) > last_ep]
        
        if not new_episodes:
            from datetime import datetime, timedelta
            latest_mtime = 0
            if os.path.exists(folder_path):
                for root, _, files in os.walk(folder_path):
                    for f in files:
                        if f.endswith('.mp4') or f.endswith('.mkv'):
                            try:
                                mtime = os.path.getmtime(os.path.join(root, f))
                                if mtime > latest_mtime: latest_mtime = mtime
                            except: pass

            date_str = ""
            if latest_mtime > 0:
                file_dt = datetime.fromtimestamp(latest_mtime)
                date_str = f" (Last download: {file_dt.strftime('%Y-%m-%d')})"

            tqdm.write(f"  Already up to date.{date_str}", file=sys.stdout)

            skip_days = getattr(config, 'AUTO_SKIP_DAYS', 30)
            tracked = get_tracked(folder_path)
            if tracked and tracked[3]:
                try:
                    last_upd = datetime.fromisoformat(tracked[3])
                    if latest_mtime > 0:
                        if file_dt < last_upd: last_upd = file_dt
                    if datetime.now() - last_upd > timedelta(days=skip_days):
                        tqdm.write(f"  No new episodes for {skip_days}+ days. Auto-skipping.", file=sys.stdout)
                        save_tracked(folder_path, anime_id, anime_title, False, update_time=False)
                except: pass
            return True, anime_id, anime_title

        # Parse existing files and determine maximum episode number & padding
        matched_files = []
        max_ep_num = 0
        
        # Check all new episodes that are going to be downloaded
        for ep in new_episodes:
            try:
                ep_val = int(ep.get('episode', 0))
                if ep_val > max_ep_num:
                    max_ep_num = ep_val
            except (ValueError, TypeError):
                pass

        # Scan for existing files and find name patterns and max episode number
        for root, _, flist in os.walk(folder_path):
            for f in flist:
                if not (f.endswith('.mp4') or f.endswith('.mkv')): continue
                m = re.match(r'^(.*(?:_-_| - |Episode |\[|\s))(\d{1,4})([-_\s].*)\.(mp4|mkv)$', f)
                if not m:
                    m = re.match(r'^(.*[^0-9])(\d{1,4})([^0-9]+(?:720|1080|360)p.*)\.(mp4|mkv)$', f)
                if m:
                    prefix = m.group(1)
                    ep_str = m.group(2)
                    suffix = m.group(3)
                    ext = m.group(4)
                    try:
                        ep_val = int(ep_str)
                    except ValueError:
                        continue
                    
                    matched_files.append({
                        'root': root,
                        'filename': f,
                        'prefix': prefix,
                        'ep_str': ep_str,
                        'ep_num': ep_val,
                        'suffix': suffix,
                        'ext': ext
                    })
                    if ep_val > max_ep_num:
                        max_ep_num = ep_val

        # Target padding must be at least 2, and enough to fit the maximum episode number
        target_padding = max(2, len(str(max_ep_num)))

        # Rename any existing files that have less padding than target_padding
        name_prefix, name_suffix = None, None
        for item in matched_files:
            # Set name_prefix and name_suffix from the first matched file
            if not name_prefix:
                name_prefix = item['prefix']
                name_suffix = item['suffix']
            
            curr_pad = len(item['ep_str'])
            if curr_pad < target_padding:
                old_path = os.path.join(item['root'], item['filename'])
                new_filename = f"{item['prefix']}{str(item['ep_num']).zfill(target_padding)}{item['suffix']}.{item['ext']}"
                new_path = os.path.join(item['root'], new_filename)
                if not os.path.exists(new_path):
                    try:
                        os.rename(old_path, new_path)
                        tqdm.write(f"  Renamed: {item['filename']} -> {new_filename}", file=sys.stdout)
                    except Exception as e:
                        log_debug(f"Failed to rename {item['filename']} to {new_filename}: {e}")
                else:
                    log_debug(f"Skipped renaming {item['filename']} as {new_filename} already exists")

        name_padding = target_padding

        # Determine language preference
        effective_lang = lang
        is_default_fallback = False
        if not effective_lang:
            detected = detect_lang_from_files(folder_path)
            if detected:
                effective_lang = detected
                tqdm.write(f"  Language detected from files: {effective_lang}", file=sys.stdout)
            else:
                effective_lang = getattr(config, 'DEFAULT_LANGUAGE', 'en')
                if last_ep <= 0:
                    is_default_fallback = True
                    tqdm.write(f"  No existing files, using default language: {effective_lang}", file=sys.stdout)
                else:
                    tqdm.write(f"  Could not detect language from existing files, using default: {effective_lang}", file=sys.stdout)

        tqdm.write(f"  Found {len(new_episodes)} new episode(s).", file=sys.stdout)
        
        abort_all = threading.Event()
        prompt_lock = threading.Lock()
        
        def _process_single_episode(ep, position=None, is_first=False, start_event=None):
            try:
                _process_single_episode_impl(ep, position, is_first, start_event)
            finally:
                if is_first and start_event:
                    start_event.set()

        def _process_single_episode_impl(ep, position=None, is_first=False, start_event=None):
            nonlocal effective_lang, is_default_fallback
            if abort_all.is_set(): return
            ep_num = ep['episode']
            
            def safe_print(msg):
                with pos_lock:
                    tqdm.write(msg, file=sys.stdout)

            if position is None:
                safe_print(f"    [Episode {ep_num}]")
            
            if is_episode_already_present(folder_path, ep_num, anime_title):
                safe_print(f"    - Episode {ep_num} already present (detected in subfolder). Skipping.")
                return
                
            if not is_first and start_event:
                start_event.wait()
                
            direct, actual_lang, avail_langs = get_direct_link(client, anime_id, ep['session'], quality, effective_lang)
            
            if not direct and avail_langs:
                other_langs = avail_langs - {effective_lang}
                if other_langs:
                    other = next(iter(other_langs))
                    
                    if is_default_fallback:
                        with prompt_lock:
                            if is_default_fallback:
                                lang_label = 'English dub' if other == 'en' else 'Japanese sub'
                                ans = input(f"    - {effective_lang} not available. Download {lang_label} instead? [y/n]: ").lower()
                                if ans == 'y':
                                    effective_lang = other
                                is_default_fallback = False
                        
                        if effective_lang == other:
                            direct, actual_lang, _ = get_direct_link(client, anime_id, ep['session'], quality, effective_lang)
                        else:
                            safe_print(f"    - Skipped (no {effective_lang} available).")
                            return
                    elif getattr(config, 'AUTO_REJECT_LANGUAGE_FALLBACK', False) or parallel > 1:
                        # Auto-skip if in parallel mode to avoid interactive mess
                        ans = 'n'
                        safe_print(f"    - Skipped (no {effective_lang} available).")
                        return
                    else:
                        lang_label = 'English dub' if other == 'en' else 'Japanese sub'
                        ans = input(f"    - {effective_lang} not available. Download {lang_label} instead? [y/n]: ").lower()
                        if ans == 'y':
                            direct, actual_lang, _ = get_direct_link(client, anime_id, ep['session'], quality, other)
                        else:
                            safe_print(f"    - Skipped (no {effective_lang} available).")
                            return

            if direct:
                if name_prefix and name_suffix:
                    filename = f"{name_prefix}{str(ep_num).zfill(name_padding)}{name_suffix}.mp4"
                else:
                    lang_tag = "_EngDub" if (actual_lang or effective_lang) == 'en' else "_SubsPlease"
                    filename = f"AnimePahe_{anime_title}_-_{str(ep_num).zfill(name_padding)}_{quality}{lang_tag}.mp4"
                save_path = os.path.join(folder_path, filename)
                
                retry_count = 0
                max_retries = getattr(config, 'MAX_DOWNLOAD_RETRIES', 5)
                bypassed_this_ep = False
                 
                while not abort_all.is_set():
                    if is_first and start_event:
                        start_event.set()
                        
                    if download_file(direct, save_path, f"{config.KWIK_URL}/", position=position):
                        safe_print(f"    - Completed: {filename}")
                        send_windows_notification("Anime Downloaded", f"{anime_title} - Episode {ep_num}", folder_path)
                        # Immediately ensure DB has the correct tracked URL upon a successful download
                        from .db import get_tracked, save_tracked
                        t_info = get_tracked(folder_path)
                        if not t_info or t_info[0] != anime_id:
                            save_tracked(folder_path, anime_id, anime_title, True)
                        break
                    else:
                        if retry_count < max_retries:
                            retry_count += 1
                            delay = getattr(config, 'DOWNLOAD_RETRY_BASE_DELAY', 2) * (getattr(config, 'DOWNLOAD_RETRY_MULTIPLIER', 2) ** (retry_count - 1))
                            safe_print(f"    - Episode {ep_num} failed. Retrying in {delay}s ({retry_count}/{max_retries})...")
                            time.sleep(delay)
                            
                            bypass_mode = True
                            if not bypassed_this_ep:
                                bypass_mode = "force"
                                bypassed_this_ep = True
                            direct, _, _ = get_direct_link(client, anime_id, ep['session'], quality, actual_lang, retry_with_browser=bypass_mode)
                            if not direct: break
                            continue
                        
                        if parallel == 1:
                            if getattr(config, 'OPEN_BROWSER_ON_FAIL', False):
                                webbrowser.open(direct)
                            ans = input(f"    - Episode {ep_num} download failed. [r(etry)/s(kip)/f(orever-skip)/q(uit)]: ").lower()
                            if ans == 'r':
                                retry_count = 0; direct, _, _ = get_direct_link(client, anime_id, ep['session'], quality, actual_lang); continue
                            elif ans == 'f':
                                save_tracked(folder_path, anime_id, anime_title, False)
                                abort_all.set(); return
                            elif ans == 'q':
                                abort_all.set(); return
                        break
            else:
                safe_print(f"    - Extraction failed for Episode {ep_num}.")

        if parallel > 1:
            tqdm.write(f"  Downloading in parallel (limit: {parallel})...", file=sys.stdout)
            # Thread-safe position management for tqdm bars
            positions = list(range(1, parallel + 1))
            first_started = threading.Event()
            first_flag_lock = threading.Lock()
            first_assigned = [False]

            def _worker(ep):
                with pos_lock:
                    pos = positions.pop(0) if positions else 1
                
                is_first = False
                with first_flag_lock:
                    if not first_assigned[0]:
                        is_first = True
                        first_assigned[0] = True

                try:
                    _process_single_episode(ep, position=pos, is_first=is_first, start_event=first_started)
                finally:
                    with pos_lock:
                        positions.append(pos)

            with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as executor:
                executor.map(_worker, new_episodes)
        else:
            for ep in new_episodes:
                _process_single_episode(ep)
                if abort_all.is_set(): break

        update_last_checked(folder_path)
        return True, anime_id, anime_title
    except Exception as e:
        log_debug(f"Process folder error: {e}")
        return False, anime_id, anime_title

