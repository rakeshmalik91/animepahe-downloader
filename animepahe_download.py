"""
AnimePahe Auto-Downloader

Usage:
  1. Automated Tracking Mode (No arguments):
     python animepahe_download.py
     - Scans your configured BASE_DOWNLOAD_DIR (in config.py).
     - Identifies new anime folders based on names.
     - Automatically checks AnimePahe for new episodes and downloads them sequentially.
     - Saves tracked folder metadata internally to 'tracking.db' to remember for the next run.

  2. Manual/Specific Mode:
     python animepahe_download.py "Anime Name 1, Anime Name 2"
     - Directly tries to search and download specific anime entries into a matching folder. Supports comma-separated lists.

  3. Season Scanning Mode:
     python animepahe_download.py --more-seasons
     - Scans your existing library (or a specific folder using "Name" --more-seasons) to find untracked sequel seasons for Anime you already have.
     python animepahe_download.py "Anime Name" --new-seasons
     - Similar to --more-seasons, but strictly filters for seasons that are sequentially newer than your maximum local season/year.
     python animepahe_download.py "Anime Name" --all-seasons
     - Searches AnimePahe and queries you to track all variations, movies, and seasons of an anime.

  Optional flags:
     -q, --quality    Video quality (e.g. 360p, 720p, 1080p). Defaults to config.DEFAULT_QUALITY.
     -l, --lang       Audio language ('en' for dub, 'jap' for sub). Defaults to auto-detect or config.DEFAULT_LANGUAGE.
     --url            Direct URL to the AnimePahe series page if name search fails.
     --more-seasons   Scan tracking.db and your library for completely untracked sequels and season continuations.
     --new-seasons    Scan existing folders and ONLY look for sequentially newer seasons.
     --all-seasons    Search all series for a given anime and optionally download them.
     -y, --yes        Skip confirmation prompts and auto-download results.
     -ep, --episodes  Specific episode(s) to download (e.g. 1, 3, 5-7).
     --parallel N     Download N episodes in parallel (default: config.DEFAULT_PARALLEL_DOWNLOADS).
     --skip-folder    Manually add a folder (relative to base directory) to the skip list to ignore it forever.
     --unskip-folder  Manually remove a folder from the skip list so it can be checked again.

Examples:
  python animepahe_download.py
  python animepahe_download.py "Frieren, Jujutsu"
  python animepahe_download.py "Jujutsu Kaisen" -q 1080p -l jap
  python animepahe_download.py "Attack on Titan" --url https://animepahe.si/anime/1234abcd
  python animepahe_download.py --more-seasons
  python animepahe_download.py "Evangelion Rebuild, Eighty Six" --more-seasons
  python animepahe_download.py "Jujutsu" --new-seasons
  python animepahe_download.py "Eighty Six" --all-seasons -y
  python animepahe_download.py "Frieren" -ep 1,3,5-7
  python animepahe_download.py "One Piece" --parallel 4

Settings like base directory, segments, retries, and language fallbacks can be configured in 'config.py'.
"""

import os
import re
import argparse
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

# Import local configuration
import config

# Force IPv4 globally if enabled in config
if config.FORCE_IPV4:
    import requests.packages.urllib3.util.connection as urllib3_conn
    def allowed_gai_family():
        return socket.AF_INET
    urllib3_conn.allowed_gai_family = allowed_gai_family

from modules.utils import log_debug, normalize_path, ensure_working_mirror, ensure_working_kwik_mirror, ensure_working_jikan_mirror
from modules.db import init_db, get_folder_by_id, get_tracked, save_tracked, cleanup_db
from modules.scraper import search_anime
from modules.processor import process_one_folder

def main():
    init_db()
    log_debug(f"\n--- NEW SESSION: {' '.join(sys.argv)} ---")
    
    parser = argparse.ArgumentParser(description="AnimePahe Auto-Downloader")
    parser.add_argument("name", nargs='?', help="Anime name(s) or folder name(s), comma-separated (optional)")
    parser.add_argument("--url", help="AnimePahe series URL (optional)")
    parser.add_argument("-q", "--quality", default=config.DEFAULT_QUALITY, help="360p, 720p, or 1080p")
    parser.add_argument("-l", "--lang", choices=['en', 'jap'], default=None, help="Audio language: en (English dub) or jap (Japanese sub). Auto-detects from existing files if not specified.")
    parser.add_argument("--all-seasons", action="store_true", help="Search and optionally download all seasons/movies for the given name")
    parser.add_argument("--more-seasons", action="store_true", help="Scan existing folders for new untracked seasons")
    parser.add_argument("--new-seasons", action="store_true", help="Scan existing folders and ONLY look for newer seasons based on season number or release year fallback")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompts and auto-download")
    parser.add_argument("-ep", "--episodes", help="Specific episode(s) to download (e.g. 1,2,5-10)")
    parser.add_argument("--parallel", type=int, default=config.DEFAULT_PARALLEL_DOWNLOADS, help="Number of parallel episode downloads")
    parser.add_argument("--skip-folder", help="Manually add a folder to the skip list forever")
    parser.add_argument("--unskip-folder", help="Manually remove a folder from the skip list")
    
    args = parser.parse_args()
    
    if args.skip_folder:
        target_path = os.path.abspath(os.path.join(config.BASE_DOWNLOAD_DIR, args.skip_folder))
        if os.path.exists(target_path):
            save_tracked(target_path, None, None, False)
            tqdm.write(f"Added '{target_path}' to the skip list forever.", file=sys.stdout)
        else:
            tqdm.write(f"Error: Folder '{args.skip_folder}' does not exist.", file=sys.stdout)
        return

    if args.unskip_folder:
        target_path = os.path.join(config.BASE_DOWNLOAD_DIR, args.unskip_folder)
        try:
            conn = sqlite3.connect(config.DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM tracking WHERE folder_path = ?", (target_path,))
            deleted = c.rowcount
            # Also unskip any subfolders
            c.execute("DELETE FROM tracking WHERE folder_path LIKE ?", (f"{target_path}\\%",))
            deleted += c.rowcount
            
            # Also try substring match as fallback if they didn't provide full path
            if deleted == 0:
                 c.execute("DELETE FROM tracking WHERE folder_path LIKE ?", (f"%{args.unskip_folder}%",))
                 deleted += c.rowcount

            conn.commit()
            conn.close()
            if deleted > 0:
                tqdm.write(f"Removed {deleted} matching entry(ies) for '{args.unskip_folder}' from the skip list so they can be checked again.", file=sys.stdout)
            else:
                tqdm.write(f"No fully or partially matching folders found in the skip list for '{args.unskip_folder}'.", file=sys.stdout)
        except Exception as e:
            tqdm.write(f"Error: {e}", file=sys.stdout)
        return
    
    # Parse episodes if provided
    target_episodes = None
    if args.episodes:
        target_episodes = set()
        for part in args.episodes.split(','):
            if '-' in part:
                try:
                    start, end = map(int, part.split('-'))
                    target_episodes.update(range(start, end + 1))
                except: pass
            else:
                try:
                    target_episodes.add(int(part))
                except: pass
        if not target_episodes:
            tqdm.write("Error: Invalid episode format. Use e.g. 1,3,5-10", file=sys.stdout)
            return

    transport = httpx.HTTPTransport(local_address="0.0.0.0") if config.FORCE_IPV4 else None
    client = httpx.Client(http2=True, transport=transport, timeout=15, follow_redirects=True)
    
    # Initialize headers/cookies with first available URL as fallback
    temp_url = config.ANIMEPAHE_URLS[0]
    client.cookies.set("__ddg2_", "", domain=temp_url.replace("https://", ""))
    client.headers.update({
        "User-Agent": config.USER_AGENT,
        "Referer": f"{temp_url}/",
        "Accept": "application/json, text/javascript, */*; q=0.01"
    })
    
    # Try current mirrors, or rotate if down
    if not ensure_working_mirror(client, verbose=True):
        tqdm.write(f"Error: All AnimePahe mirrors appear to be down.", file=sys.stdout)
        client.close()
        return
    
    if not ensure_working_kwik_mirror(client, verbose=True):
        tqdm.write(f"Warning: All Kwik mirrors appear to be down. Downloads might fail.", file=sys.stdout)
    
    if not ensure_working_jikan_mirror(client, verbose=True):
        tqdm.write(f"Warning: Jikan API (MAL fallback) appears to be down. Identification might be less accurate.", file=sys.stdout)

    ap_display = config.ANIMEPAHE_URL.replace('https://', '')
    kw_display = config.KWIK_URL.replace('https://', '')
    jk_display = getattr(config, 'JIKAN_API_URL', 'Down').replace('https://', '')
    tqdm.write(f"--- AnimePahe Downloader (Using: {ap_display} | Kwik: {kw_display} | Jikan: {jk_display}) ---", file=sys.stdout)
    
    if args.more_seasons or args.new_seasons:
        if args.name:
            target_names = [n.strip().lower() for n in args.name.split(',') if n.strip()]
            tqdm.write(f"Scanning specific folder(s) for new seasons: {', '.join(target_names)}", file=sys.stdout)
            dirs_to_scan = [d for d in os.listdir(config.BASE_DOWNLOAD_DIR) 
                            if any(tn in d.lower() for tn in target_names) and os.path.isdir(os.path.join(config.BASE_DOWNLOAD_DIR, d))]
            if not dirs_to_scan:
                tqdm.write(f"Error: Could not find any folder matching '{args.name}'.", file=sys.stdout)
                return
        else:
            tqdm.write(f"Scanning base folder for new seasons: {config.BASE_DOWNLOAD_DIR}", file=sys.stdout)
            dirs_to_scan = os.listdir(config.BASE_DOWNLOAD_DIR)
            
        for d in dirs_to_scan:
            parent_dir = os.path.join(config.BASE_DOWNLOAD_DIR, d)
            if not os.path.isdir(parent_dir): continue
            if d.lower() in ["completed", "temp", "scripts", "__pycache__", ".git"]: continue
            
            tracked = get_tracked(parent_dir)
            if tracked and tracked[2] == 0: continue  # Skip explicitly disabled/ignored folders
            
            search_query = d.replace('：', ' ').replace(':', ' ')
            search_query = re.sub(r'(?i)\s+(720p|1080p|360p|SubsPlease|Dual-Audio|BD|Web-DL)', '', search_query).strip()
            search_query = re.sub(r'\[.*?\]|\(.*?\)', '', search_query).strip()
            
            results, api_ok = search_anime(client, search_query, return_all=True)
            if not results: continue
            
            has_new = False
            
            def get_season_year(tit, meta):
                s = 1
                m = re.search(r'(?:Season|S)\s*(\d+)', tit, re.IGNORECASE)
                if m: s = int(m.group(1))
                else:
                    m = re.search(r'(\d+)(?:st|nd|rd|th)\s*Season', tit, re.IGNORECASE)
                    if m: s = int(m.group(1))
                y = 0
                if meta and meta.get('year'): y = int(meta.get('year'))
                else:
                    m2 = re.search(r'\((\d{4})[-\s]*\)', tit)
                    if m2: y = int(m2.group(1))
                    else:
                        m3 = re.search(r'\b(19|20)\d{2}\b', tit)
                        if m3: y = int(m3.group(0))
                return s, y
            
            max_local_season = 0
            max_local_year = 0
            untracked = []
            
            # Check physical parent folder and subfolders for any season/year indicators
            parent_name = os.path.basename(parent_dir)
            ps, py = get_season_year(parent_name, None)
            if ps > max_local_season: max_local_season = ps
            if py > max_local_year: max_local_year = py
            
            for subdir in os.listdir(parent_dir):
                if os.path.isdir(os.path.join(parent_dir, subdir)):
                    sub_s, sub_y = get_season_year(subdir, None)
                    if sub_s > max_local_season: max_local_season = sub_s
                    if sub_y > max_local_year: max_local_year = sub_y

            for r_aid, r_clean, r_title, r_meta in results:
                is_tracked = False
                try:
                    conn = sqlite3.connect(config.DB_PATH)
                    c = conn.cursor()
                    c.execute("SELECT folder_path FROM tracking WHERE anime_id = ?", (r_aid,))
                    if c.fetchone(): is_tracked = True
                    conn.close()
                except: pass
                
                disk_exists = False
                mapped_path = None
                clean_r_title = re.sub(r'[\W_]+', '', r_title).lower()
                clean_parent = re.sub(r'[\W_]+', '', d).lower()
                is_base_season = clean_r_title == clean_parent or clean_r_title in clean_parent or clean_parent in clean_r_title
                
                if not is_tracked:
                    for subdir in os.listdir(parent_dir):
                        if not os.path.isdir(os.path.join(parent_dir, subdir)): continue
                        clean_sub = re.sub(r'[\W_]+', '', subdir).lower()
                        clean_sub_no_season = re.sub(r'^(season|s)\d+', '', clean_sub)
                        
                        if clean_sub == clean_r_title or clean_r_title in clean_sub or clean_sub in clean_r_title:
                            disk_exists = True; mapped_path = os.path.join(parent_dir, subdir); break
                        if len(clean_sub_no_season) > 3 and (clean_sub_no_season in clean_r_title or clean_r_title in clean_sub_no_season):
                            disk_exists = True; mapped_path = os.path.join(parent_dir, subdir); break
                        if is_base_season and clean_sub in ['season1', 'season01', 's1', 's01']:
                            disk_exists = True; mapped_path = os.path.join(parent_dir, subdir); break
                            
                if is_tracked or disk_exists:
                    s, y = get_season_year(r_title, r_meta)
                    if s > max_local_season: max_local_season = s
                    
                    # If local folder didn't have a year in the name, try to find it using API metadata
                    if y > max_local_year:
                        max_local_year = y
                        
                    if disk_exists:
                        untracked.append(('disk', r_aid, r_clean, r_title, r_meta, mapped_path))
                else:
                    untracked.append(('new', r_aid, r_clean, r_title, r_meta, None))
                    
            for utype, r_aid, r_clean, r_title, r_meta, mapped_path in untracked:
                if utype == 'new':
                    if args.new_seasons:
                        s, y = get_season_year(r_title, r_meta)
                        is_newer = False
                        if s > max_local_season: is_newer = True
                        elif s == max_local_season and y > max_local_year: is_newer = True
                        if not is_newer: continue
                    has_new = True
                    break

            if has_new or any(u[0] == 'disk' for u in untracked):
                if has_new: tqdm.write(f"\n[Checking new seasons for: {d}]", file=sys.stdout)
                for utype, r_aid, r_clean, r_title, r_meta, mapped_path in untracked:
                    if utype == 'disk':
                        if has_new: tqdm.write(f"  [ALREADY ON DISK] '{r_title}' -> mapped to '{os.path.basename(mapped_path)}'", file=sys.stdout)
                        save_tracked(mapped_path, r_aid, r_title, True)
                        continue
                        
                    if args.new_seasons:
                        s, y = get_season_year(r_title, r_meta)
                        is_newer = False
                        if s > max_local_season: is_newer = True
                        elif s == max_local_season and y > max_local_year: is_newer = True
                        if not is_newer: continue
                        
                    ans = input(f"  New season found: '{r_title}'. Track and download? [y/N/i(gnore entire folder)]: ").lower()
                    if ans == 'y':
                        sub_folder_name = r_clean
                        if sub_folder_name.lower().startswith(d.lower()):
                            stripped = sub_folder_name[len(d):].strip()
                            stripped = re.sub(r'^[-:：_]+\s*', '', stripped).strip()
                            if stripped: sub_folder_name = stripped
                        sub_path = os.path.join(parent_dir, sub_folder_name)
                        os.makedirs(sub_path, exist_ok=True)
                        save_tracked(sub_path, r_aid, r_title, True)
                        process_one_folder(client, sub_path, r_aid, r_title, args.quality, args.lang, episodes_filter=target_episodes, parallel=args.parallel)
                    elif ans == 'i':
                        tqdm.write(f"    - Ignoring folder '{d}' forever for season scanning.", file=sys.stdout)
                        save_tracked(parent_dir, None, None, False)
                        break
                            
    if args.name:
        for current_name in [n.strip() for n in args.name.split(',') if n.strip()]:
            if args.all_seasons:
                search_query = current_name.replace('：', ' ').replace(':', ' ')
                search_query = re.sub(r'(?i)\s+(720p|1080p|360p|SubsPlease|Dual-Audio|BD|Web-DL)', '', search_query).strip()
                search_query = re.sub(r'\[.*?\]|\(.*?\)', '', search_query).strip()
                tqdm.write(f"Searching for all series matching: '{search_query}'...", file=sys.stdout)
                
                results, api_ok = search_anime(client, search_query, return_all=True)
                if not results:
                    tqdm.write(f"Error: No results found for '{search_query}'.", file=sys.stdout)
                    continue
                    
                tqdm.write(f"Found {len(results)} matches.", file=sys.stdout)
                base_folder = os.path.join(config.BASE_DOWNLOAD_DIR, re.sub(r'[\\/*?:"<>|：]', ' ', search_query).strip())
                
                for r_aid, r_clean, r_title, r_meta in results:
                    existing = get_folder_by_id(r_aid)
                    if existing:
                        tqdm.write(f"  [ALREADY TRACKED] '{r_title}' -> '{os.path.basename(existing)}'", file=sys.stdout)
                        continue
                    
                    if args.yes:
                        ans = 'y'
                        tqdm.write(f"  Auto-downloading '{r_title}'...", file=sys.stdout)
                    else:
                        ans = input(f"  Track and download '{r_title}'? [y/N]: ").lower()
                    
                    if ans == 'y':
                        sub_folder_name = r_clean
                        parent_name = os.path.basename(base_folder)
                        if sub_folder_name.lower().startswith(parent_name.lower()):
                            stripped = sub_folder_name[len(parent_name):].strip()
                            stripped = re.sub(r'^[-:：_]+\s*', '', stripped).strip()
                            if stripped: sub_folder_name = stripped
                            
                        sub_path = os.path.join(base_folder, sub_folder_name)
                        os.makedirs(sub_path, exist_ok=True)
                        save_tracked(sub_path, r_aid, r_title, True)
                        process_one_folder(client, sub_path, r_aid, r_title, args.quality, args.lang, episodes_filter=target_episodes, parallel=args.parallel)
                continue

            # 0. Strip BASE_DOWNLOAD_DIR if the user provided an absolute path
            clean_current = os.path.normpath(current_name)
            clean_base = os.path.normpath(config.BASE_DOWNLOAD_DIR)
            if clean_current.lower().startswith(clean_base.lower() + os.sep) or clean_current.lower() == clean_base.lower():
                current_name = clean_current[len(clean_base):].lstrip('\\/')

            # Sanitize each path component for Windows compatibility (preserve path separators)
            parts = re.split(r'[\\/]', current_name)
            safe_parts = []
            for part in parts:
                clean = re.sub(r'[/*?:"<>|：]', ' ', part)
                clean = re.sub(r'\s+', ' ', clean).strip()
                if clean:
                    safe_parts.append(clean)
            safe_name = os.sep.join(safe_parts)
            
            # 1. Normalize name and find potential path
            potential_path = os.path.join(config.BASE_DOWNLOAD_DIR, safe_name)
            
            # 2. Check DB for tracked info (now uses fuzzy normalization)
            tracked = get_tracked(potential_path)
            aid, title = (tracked[0], tracked[1]) if tracked else (None, None)
            
            # 3. If NOT in DB, check disk for "fuzzily identical" folders to avoid duplicates
            # (e.g. user passed ":" but disk has "：")
            if not tracked and not os.path.exists(potential_path):
                norm_target = normalize_path(potential_path)
                for d in os.listdir(config.BASE_DOWNLOAD_DIR):
                    d_path = os.path.join(config.BASE_DOWNLOAD_DIR, d)
                    if os.path.isdir(d_path) and normalize_path(d_path) == norm_target:
                        tqdm.write(f"  Found similar folder on disk: '{d}' (switching)", file=sys.stdout)
                        potential_path = d_path
                        break

            if args.url:
                match = re.search(r'/anime/([a-f0-9-]+)', args.url)
                if match: 
                    aid = match.group(1)
                    # If URL used, ignore old title to force update from API/Page
                    title = None
                    # Check for ID-based redirection
                    existing_folder = get_folder_by_id(aid)
                    if existing_folder and normalize_path(existing_folder) != normalize_path(potential_path):
                        tqdm.write(f"  Note: Redirection to tracked folder: '{os.path.basename(existing_folder)}'.", file=sys.stdout)
                        potential_path = existing_folder
            
            if not aid:
                folder_name = os.path.basename(potential_path)
                search_query = folder_name.replace('：', ' ').replace(':', ' ')
                search_query = re.sub(r'(?i)\s+(720p|1080p|360p|SubsPlease|Dual-Audio|BD|Web-DL)', '', search_query).strip()
                search_query = re.sub(r'\[.*?\]|\(.*?\)', '', search_query).strip()
                if not search_query: search_query = current_name
                tqdm.write(f"\nSearching for series: '{search_query}'...", file=sys.stdout)
                aid, title, _, dist = search_anime(client, search_query)
                
                if aid and dist > getattr(config, 'MAX_DISTANCE_THRESHOLD', 20):
                    tqdm.write(f"  [Warning] Best match '{title}' has a high name distance from '{search_query}'.", file=sys.stdout)
                    ans = input(f"  Is '{title}' correct? [y(es)/n(o)/u(rl)]: ").lower()
                    if ans == 'u':
                        new_url = input("    Enter AnimePahe URL: ").strip()
                        match = re.search(r'/anime/([a-f0-9-]+)', new_url)
                        if match:
                            aid = match.group(1)
                            title = None
                    elif ans == 'n':
                        aid, title = None, None
                
                if aid:
                    # One last check: if we found an ID, is it already tracked?
                    existing_folder = get_folder_by_id(aid)
                    if existing_folder and normalize_path(existing_folder) != normalize_path(potential_path):
                        tqdm.write(f"  Switching to tracked folder for this ID: '{os.path.basename(existing_folder)}'", file=sys.stdout)
                        potential_path = existing_folder
                    
            if not aid:
                tqdm.write(f"Error: Could not identify anime series for '{current_name}'.", file=sys.stdout)
                continue

            os.makedirs(potential_path, exist_ok=True)
            original_aid = aid
            original_title = title
            success, aid, title = process_one_folder(client, potential_path, aid, title, args.quality, args.lang, episodes_filter=target_episodes, parallel=args.parallel)
            if success:
                # Always update DB if URL was explicitly provided, if it wasn't tracked before, or if ID/title changed
                if args.url or not tracked or aid != original_aid or title != original_title:
                    save_tracked(potential_path, aid, title, True)
    elif args.url:
        match = re.search(r'/anime/([a-f0-9-]+)', args.url)
        if match:
            aid = match.group(1)
            existing_folder = get_folder_by_id(aid)
            title = None
            if existing_folder:
                potential_path = existing_folder
                tqdm.write(f"  Using tracked folder: '{os.path.basename(existing_folder)}'.", file=sys.stdout)
            else:
                url_to_fetch = f"{config.ANIMEPAHE_URL}/anime/{aid}"
                tqdm.write(f"Fetching title for {url_to_fetch}...", file=sys.stdout)
                try:
                    res = client.get(url_to_fetch, timeout=15)
                    if res.status_code == 200:
                        m = re.search(r'<h1.*?><span.*?>(.*?)</span>', res.text, re.DOTALL)
                        if not m:
                            m = re.search(r'<h1>(.*?)<span>', res.text)
                        if not m:
                            m = re.search(r'<title>(.*?)\s*(?:::|-)\s*animepahe</title>', res.text, re.IGNORECASE)
                        if m:
                            title = m.group(1).strip()
                            title = re.sub(r'[\\/*?:"<>|：]', ' ', title)
                            title = re.sub(r'\s+', ' ', title).strip()
                except Exception as e:
                    log_debug(f"Title fetch failed: {e}")
                
                if not title:
                    title = "Unknown Anime " + aid
                
                potential_path = os.path.join(config.BASE_DOWNLOAD_DIR, title)
                os.makedirs(potential_path, exist_ok=True)
                tqdm.write(f"  Created new folder: '{potential_path}'.", file=sys.stdout)
            
            success, aid, title = process_one_folder(client, potential_path, aid, title, args.quality, args.lang, episodes_filter=target_episodes, parallel=args.parallel)
            if success:
                save_tracked(potential_path, aid, title, True)
        else:
            tqdm.write("Error: Invalid AnimePahe URL format.", file=sys.stdout)
    else:
        # If no specific name was provided, perform a full library scan/update
        # This allows --new-seasons to discovery things and then the scan to update them
        tqdm.write(f"Scanning base folder: {config.BASE_DOWNLOAD_DIR}", file=sys.stdout)
        for root, dirs, files in os.walk(config.BASE_DOWNLOAD_DIR):
            depth = root[len(config.BASE_DOWNLOAD_DIR):].count(os.sep)
            if depth > 2: continue 
            if root == config.BASE_DOWNLOAD_DIR: continue

            folder_path = os.path.abspath(root)
            tracked = get_tracked(folder_path)
            
            if tracked:
                aid, title, auto, _ = tracked
                if auto == 1:
                    success, final_aid, final_title = process_one_folder(client, folder_path, aid, title, args.quality, args.lang, episodes_filter=target_episodes, parallel=args.parallel)
                    if success and final_aid and final_title and (final_aid != aid or final_title != title):
                        save_tracked(folder_path, final_aid, final_title, True)
                    continue
                else:
                    # Skip forever: prune subdirectories
                    dirs[:] = []
                    continue
            else:
                folder_name = os.path.basename(folder_path)
                if folder_name.lower() in ["completed", "temp", "scripts", "__pycache__", ".git"]: continue
                
                # Heuristic: folder names with "Season", "Part", "Year" or existing media files
                has_media = any(f.endswith('.mp4') or f.endswith('.mkv') for f in files)
                is_subfolder = depth > 1
                if not (has_media or is_subfolder): continue

                # For subfolders, always use parent folder name for context to improve search accuracy
                if depth > 1:
                    parent_name = os.path.basename(os.path.dirname(folder_path))
                    # Strip year from parent name for cleaner search
                    parent_clean = re.sub(r'\s*\(\d{4}[^)]*\)', '', parent_name).strip()
                    
                    # Prevent duplication if the subfolder already contains the anime name
                    if folder_name.lower().startswith(parent_clean.lower()):
                        search_query = folder_name
                    else:
                        search_query = f"{parent_clean} {folder_name}"
                else:
                    search_query = folder_name
                # Clean query
                search_query = search_query.replace('\uff1a', ' ').replace(':', ' ')
                search_query = re.sub(r'(?i)\s+(720p|1080p|SubsPlease|Dual-Audio|BD|Web-DL)', '', search_query).strip()
                search_query = re.sub(r'\[.*?\]|\(.*?\)', '', search_query).strip()
                if not search_query: continue
                
                rel_path = os.path.relpath(folder_path, config.BASE_DOWNLOAD_DIR)
                aid, title, api_ok, dist = search_anime(client, search_query)
                dist_confirmed_url = False
                if aid and dist > getattr(config, 'MAX_DISTANCE_THRESHOLD', 20):
                    tqdm.write(f"  [Warning] Best match '{title}' has a high name distance from '{search_query}'.", file=sys.stdout)
                    ans = input(f"  Is '{title}' correct? [y(es)/n(o)/u(rl)]: ").lower()
                    if ans == 'u':
                        new_url = input("    Enter AnimePahe URL: ").strip()
                        match = re.search(r'/anime/([a-f0-9-]+)', new_url)
                        if match:
                            aid = match.group(1)
                            title = None
                            dist_confirmed_url = True
                    elif ans == 'n':
                        aid, title = None, None
                if aid:
                    # Check for season mismatch: folder says "Season 1" but result says "Season 2"
                    folder_season = re.search(r'(?:Season|S)\s*(\d+)', folder_name, re.IGNORECASE)
                    result_season = re.search(r'(?:Season|S)\s*(\d+)', title.replace('_', ' ') if title else '', re.IGNORECASE)
                    if folder_season and result_season and folder_season.group(1) != result_season.group(1):
                        log_debug(f"Season mismatch for {rel_path}: folder=S{folder_season.group(1)}, result=S{result_season.group(1)}. Ignored for now.")
                        # Simply continue without blacklisting the folder forever
                        continue
                    
                    if dist_confirmed_url:
                        ans = 'y'
                    else:
                        tqdm.write(f"\n[New Folder: {rel_path}]", file=sys.stdout)
                        ans = input(f"  Track and download '{title or 'Unknown Series'}'? [y(es)/n(o)/s(kip)/f(skip entire folder)/u(rl)]: ").lower()
                    
                    if ans == 'y':
                        save_tracked(folder_path, aid, title, True)
                        success, final_aid, final_title = process_one_folder(client, folder_path, aid, title, args.quality, args.lang, episodes_filter=target_episodes, parallel=args.parallel)
                        if success and not title and final_title:
                            save_tracked(folder_path, final_aid, final_title, True)
                    elif ans == 'u':
                        new_url = input("    Enter AnimePahe URL: ").strip()
                        match = re.search(r'/anime/([a-f0-9-]+)', new_url)
                        if match:
                            new_aid = match.group(1)
                            success, final_aid, final_title = process_one_folder(client, folder_path, new_aid, None, args.quality, args.lang, episodes_filter=target_episodes, parallel=args.parallel)
                            if success:
                                save_tracked(folder_path, final_aid, final_title, True)
                        else:
                            tqdm.write("    Invalid URL format. Skipped for now.", file=sys.stdout)
                    elif ans == 'f':
                        # If we are in a sub-sub-folder, offer to skip the top-level parent
                        parts = [p for p in rel_path.split(os.sep) if p]
                        if len(parts) > 1:
                            top_level = parts[0]
                            top_path = os.path.abspath(os.path.join(config.BASE_DOWNLOAD_DIR, top_level))
                            ans2 = input(f"    - Skip just this folder ({folder_name}) or the whole tree '{top_level}'? [f/t]: ").lower()
                            if ans2 == 't':
                                tqdm.write(f"    - Skipping ENTIRE TREE (root: {top_level}) forever.", file=sys.stdout)
                                save_tracked(top_path, None, None, False)
                            else:
                                tqdm.write(f"    - Skipping only this folder forever: {folder_name}", file=sys.stdout)
                                save_tracked(folder_path, None, None, False)
                        else:
                            tqdm.write(f"    - Skipping entire tree forever: {folder_name}", file=sys.stdout)
                            save_tracked(folder_path, None, None, False)
                        
                        dirs[:] = [] # Prune current walk
                    elif ans == 's':
                        tqdm.write("    - Folder skipped forever.", file=sys.stdout)
                        save_tracked(folder_path, None, None, False)
                    else:
                        tqdm.write("    - Skipped for now.", file=sys.stdout)
                elif api_ok:
                    # API responded successfully but no anime match - safe to auto-skip
                    save_tracked(folder_path, None, None, False)
                    log_debug(f"Scan: No match for {rel_path}, auto-skipped.")
                else:
                    # API blocked (403/DDoS) - do NOT save, retry next time
                    log_debug(f"Scan: API blocked for {rel_path}, will retry next run.")

    cleanup_db()
    client.close()

if __name__ == '__main__':
    main()
