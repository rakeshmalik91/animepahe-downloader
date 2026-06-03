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

from .utils import log_debug, ensure_working_mirror, ensure_working_kwik_mirror, ensure_working_jikan_mirror

class KwikDecoder:
    """Helper to decode Kwik's obfuscated JavaScript links."""
    BASE_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+/"
    
    @staticmethod
    def _base_to_int(s, iy, ms):
        h = KwikDecoder.BASE_ALPHABET[:iy]
        i = KwikDecoder.BASE_ALPHABET[:ms]
        j = 0
        for idx, ch in enumerate(reversed(s)):
            try:
                pos = h.index(ch)
                j += pos * (iy ** idx)
            except ValueError: continue
        if j == 0: return int(i[0])
        k = ""
        while j > 0:
            k = i[j % ms] + k
            j //= ms
        return int(k) if k else 0

    @staticmethod
    def decode(hb, wg, of, jg):
        gj = ""
        i = 0
        while i < len(hb):
            s = ""
            while i < len(hb) and hb[i] != wg[jg]:
                s += hb[i]
                i += 1
            for j, char in enumerate(wg):
                s = s.replace(char, str(j))
            try:
                code = KwikDecoder._base_to_int(s, jg, 10) - of
                if 0 <= code <= 0x10FFFF:
                    gj += chr(code)
            except: pass
            i += 1
        return gj

def get_browser_cookies(url, extra_url=None):
    """Helper to open a browser, solve Cloudflare, and extract the direct link.
    Returns (cookies, ua, resolved_url)
    """
    try:
        import undetected_chromedriver as uc
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        
        # Patch __del__ and quit to prevent OSError during GC on Windows
        if hasattr(uc.Chrome, '__del__'):
            original_del = uc.Chrome.__del__
            def patched_del(self):
                try:
                    if original_del: original_del(self)
                except (OSError, AttributeError):
                    pass
            uc.Chrome.__del__ = patched_del
            
        if hasattr(uc.Chrome, 'quit'):
            original_quit = uc.Chrome.quit
            def patched_quit(self, *args, **kwargs):
                try:
                    if original_quit: original_quit(self, *args, **kwargs)
                except (OSError, AttributeError):
                    pass
            uc.Chrome.quit = patched_quit
            
    except ImportError:
        log_debug("Requirements for browser bypass missing.")
        return None, None, None

    log_debug(f"Opening browser for full resolution: {url}")
    tqdm.write(f"\n[Browser Resolve] Opening browser to extract direct link...", file=sys.stdout)
    
    options = uc.ChromeOptions()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    
    import tempfile
    import shutil
    temp_dl_dir = tempfile.mkdtemp(prefix="animepahe_temp_")
    prefs = {
        "download.default_directory": temp_dl_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": False
    }
    options.add_experimental_option("prefs", prefs)

    # Dynamic Chrome version detection
    version = getattr(config, 'BROWSER_VERSION_MAIN', None)
    if not version and sys.platform == 'win32':
        try:
            import winreg
            for key_path in [r'Software\Google\Chrome\BLBeacon', r'SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Google Chrome']:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                        val, _ = winreg.QueryValueEx(key, 'version')
                        if val:
                            version = int(val.split('.')[0])
                            break
                except Exception:
                    pass
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                        val, _ = winreg.QueryValueEx(key, 'version')
                        if val:
                            version = int(val.split('.')[0])
                            break
                except Exception:
                    pass
        except Exception as reg_err:
            log_debug(f"Failed to query registry for Chrome version: {reg_err}")

    try:
        log_debug(f"Initializing uc.Chrome with version_main={version}")
        driver = uc.Chrome(options=options, version_main=version)
    except Exception as e:
        log_debug(f"uc.Chrome initialization failed: {e}")
        # Fallback 1: Parse version mismatch from the exception message
        match = re.search(r"Current browser version is (\d+)", str(e))
        if match:
            parsed_version = int(match.group(1))
            log_debug(f"Parsed Chrome version {parsed_version} from error message. Retrying...")
            try:
                driver = uc.Chrome(options=options, version_main=parsed_version)
            except Exception as e_inner:
                log_debug(f"Retry with parsed version {parsed_version} failed: {e_inner}")
                raise e_inner
        else:
            # Fallback 2: Try running with version_main=None if we haven't already
            if version is not None:
                log_debug("Retrying with version_main=None")
                try:
                    driver = uc.Chrome(options=options, version_main=None)
                except Exception as e_inner:
                    raise e_inner
            else:
                raise e
    
    resolved_url = None
    try:
        driver.get(url)
        # 1. Wait for challenge (give user plenty of time if needed)
        start_time = time.time()
        while time.time() - start_time < 300:
            if "Cloudflare" not in driver.title and "Just a moment" not in driver.title and "Verify" not in driver.title:
                log_debug("Page seems clear of Cloudflare.")
                break
            time.sleep(2)
        
        # 2. Click the download button & wait for redirect (only for Kwik)
        if "kwik" in url:
            try:
                log_debug("Looking for download button...")
                wait = WebDriverWait(driver, 30)
                
                # Try multiple selectors for Kwik's button
                button = None
                for selector in ["button", "input[type='submit']", ".button"]:
                    try:
                        button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, selector)))
                        if button: break
                    except: continue
                
                if button:
                    driver.execute_script("arguments[0].click();", button)
                    log_debug("Download button clicked.")
                else:
                    log_debug("Could not find download button automatically. User might need to click it.")
                
                # 3. Wait for redirect to CDN (check all tabs and logs)
                start_time_cdn = time.time()
                log_debug("Waiting for direct link capture via Logs...")
                
                while time.time() - start_time_cdn < 60:
                    # A. Scan performance logs (Very thorough)
                    try:
                        logs = driver.get_log("performance")
                        for entry in logs:
                            entry_text = entry.get("message", "")
                            if "owocdn" in entry_text or "vault" in entry_text:
                                # Search for any string that looks like an owocdn/vault URL
                                matches = re.findall(r'(https?://[^"\\\s]+\.(?:mp4|mkv)[^"\\\s]*)', entry_text.replace("\\/", "/"))
                                if not matches:
                                    matches = re.findall(r'(https?://[^"\\\s]*(?:owocdn|vault)[^"\\\s]*)', entry_text.replace("\\/", "/"))
                                
                                for f_url in matches:
                                    if "token=" in f_url or ".mp4" in f_url:
                                        resolved_url = f_url
                                        break
                            if resolved_url: break
                        if resolved_url: break
                    except: pass

                    # B. Check Current URL (sometimes it updates)
                    try:
                        curr = driver.current_url
                        if "owocdn.top" in curr or "vault" in curr:
                            resolved_url = curr
                            break
                    except: pass
                    
                    time.sleep(1)
                
                if resolved_url:
                    log_debug(f"Captured resolved URL: {resolved_url}")
            except Exception as e:
                log_debug(f"Browser resolve error: {e}")

        all_cookies = driver.get_cookies()
        
        # Optional: if browser didn't redirect but we have cookies, try visiting extra_url
        if not resolved_url and extra_url:
            try:
                driver.get(extra_url)
                time.sleep(5)
                if "owocdn.top" in driver.current_url:
                    resolved_url = driver.current_url
                # Merge
                cdn_cookies = driver.get_cookies()
                seen = {(c['domain'], c['name']): c for c in all_cookies}
                for c in cdn_cookies: seen[(c['domain'], c['name'])] = c
                all_cookies = list(seen.values())
            except: pass

        ua = driver.execute_script("return navigator.userAgent")
        return all_cookies, ua, resolved_url
    except Exception as e:
        log_debug(f"Browser bypass error: {e}")
        return None, None, None
    finally:
        try: 
            if 'driver' in locals() and driver:
                driver.quit()
        except: pass
        try:
            if 'driver' in locals() and driver:
                del driver
            import gc
            gc.collect()
        except: pass
        try:
            import shutil
            shutil.rmtree(temp_dl_dir, ignore_errors=True)
        except: pass

def resolve_kwik_direct(kwik_url, referer, retry_with_browser=True):
    """Resolve a Kwik download link.
    If blocked by Cloudflare, it optionally launches a visible browser for the user to solve the challenge.
    """
    try:
        from curl_cffi import requests as curl_requests
        import json
    except ImportError:
        log_debug("curl_cffi not installed")
        return None
        
    try:
        log_debug(f"Resolving Kwik: {kwik_url}")
        
        # 1. Try with curl_cffi using cached cookies if we have them
        session = curl_requests.Session(impersonate="chrome124")
        
        from .db import get_kwik_session, save_kwik_session
        kwik_cookies, kwik_ua = get_kwik_session()
        
        encode_pattern = r'\(\s*"([^",]*)"\s*,\s*\d+\s*,\s*"([^",]*)"\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*\d+[a-zA-Z]?\s*\)'
        
        headers = {
            "Referer": referer,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        # Override user-agent if provided
        if kwik_ua:
            headers["User-Agent"] = kwik_ua
        
        domain_main = re.search(r'https?://([^/]+)', kwik_url).group(1) if re.search(r'https?://([^/]+)', kwik_url) else "kwik.cx"
        if kwik_cookies:
            # Handle both old dict format and new list-of-dicts format
            if isinstance(kwik_cookies, dict):
                for name, value in kwik_cookies.items():
                    session.cookies.set(name, value, domain=domain_main)
            elif isinstance(kwik_cookies, list):
                for c in kwik_cookies:
                    if isinstance(c, dict):
                        session.cookies.set(c['name'], c['value'], domain=c.get('domain'))
        
        res = session.get(kwik_url, headers=headers, timeout=20)
        
        # Log failure details for diagnosis
        if res.status_code != 200 or "eval(function" not in res.text:
            title_match = re.search(r'<title>(.*?)</title>', res.text, re.IGNORECASE)
            title = title_match.group(1).strip() if title_match else "No Title"
            log_debug(f"Kwik initial resolution failed (Status: {res.status_code}, Title: '{title}', ScriptFound: {'eval' in res.text})")

        # New: If this is a forced bypass, we might want to pre-extract a potential direct link
        # to visit it in the browser too.
        force_bypass = (retry_with_browser == "force")
        pre_extracted_direct = None
        if force_bypass:
            try:
                # Try a quick extraction with current (maybe stale) session
                match_pe = re.search(encode_pattern, re.sub(r"\s+", " ", res.text))
                if match_pe:
                    # Very simple extraction just for the purpose of getting a domain
                    # This won't work for the final link but might give us the CDN host
                    decoded_pe = KwikDecoder.decode(match_pe.group(1), match_pe.group(2), int(match_pe.group(3)), int(match_pe.group(4)))
                    # Usually Kwik's decoded script has the action URL
                    post_url_pe = re.search(r'action="(\S*)"', decoded_pe)
                    if post_url_pe:
                        pre_extracted_direct = post_url_pe.group(1).replace("/f/", "/d/")
            except: pass

        if (res.status_code != 200 or "eval(function" not in res.text or force_bypass) and retry_with_browser and getattr(config, "ENABLE_BROWSER_BYPASS", False):
            if force_bypass:
                log_debug(f"Browser bypass forced by caller. Pre-extracted: {pre_extracted_direct}")
            else:
                log_debug(f"Kwik blocked (Status: {res.status_code}). Attempting browser bypass...")
            
            new_cookies, new_ua, new_resolved = get_browser_cookies(kwik_url, extra_url=pre_extracted_direct)
            
            if new_cookies is not None and new_ua is not None:
                # Persist to DB instead of config.py
                save_kwik_session(new_cookies, new_ua)
                
                # If the browser already resolved it, USE IT!
                if new_resolved:
                    log_debug(f"Kwik resolved via browser: {new_resolved}")
                    return new_resolved
                
                # Retry the request with new session
                session = curl_requests.Session(impersonate="chrome124")
                headers["User-Agent"] = new_ua
                if new_cookies:
                    if isinstance(new_cookies, dict):
                       for name, value in new_cookies.items():
                           session.cookies.set(name, value, domain=domain_main)
                    else:
                        for c in new_cookies:
                            session.cookies.set(c['name'], c['value'], domain=c.get('domain'))
                
                res = session.get(kwik_url, headers=headers, timeout=20)
            else:
                log_debug("Browser bypass failed or was cancelled.")

        page_content = res.text
        current_url = kwik_url
        
        if res.status_code != 200 or "eval(function" not in page_content:
            log_debug(f"Kwik still blocked after bypass attempt (Status: {res.status_code}).")
            return None
                
        # 3. We have the page content, decode the link
        clean_text = re.sub(r"\s+", " ", page_content)
        match = re.search(encode_pattern, clean_text)
        if not match:
            log_debug(f"Kwik: encoded script not found in response from {current_url}. Body snippet: {page_content[:200]}")
            return None
        
        decoded = KwikDecoder.decode(match.group(1), match.group(2), int(match.group(3)), int(match.group(4)))
        token_match = re.search(r'name="_token"[^"]*"(\S*)"', decoded)
        post_url_match = re.search(r'action="(\S*)"', decoded)
        if not token_match:
            log_debug("Kwik: _token not found in decoded script")
            return None
        
        token = token_match.group(1)
        post_url = post_url_match.group(1) if post_url_match else current_url
        if "/f/" in post_url:
            post_url = post_url.replace("/f/", "/d/")
        
        # 4. Do the POST request via curl_cffi
        time.sleep(1.5)
        post_headers = headers.copy()
        post_headers.update({
            "Referer": current_url,
            "Origin": re.match(r'https?://[^/]+', current_url).group(0),
            "Upgrade-Insecure-Requests": "1"
        })
        post_res = session.post(
            post_url,
            data={"_token": token},
            headers=post_headers,
            allow_redirects=False,
            timeout=20
        )
        
        if post_res.status_code == 302:
            return post_res.headers.get("Location")
        else:
            log_debug(f"Kwik POST failed with {post_res.status_code}")
    except Exception as e:
        log_debug(f"Kwik resolution error: {e}")
    return None

def get_direct_link(client, anime_id, session, target_quality="720p", target_lang="jap", retry_with_browser=True):
    """
    Combined Extraction: 
    Phase 1 (AnimePahe): Use HTTPX (HTTP/2) to bypass DDoS-Guard
    Phase 2 & 3 (Redirector/Kwik): Use Cloudscraper
    
    target_lang: 'en' for English dub, 'jap' for Japanese (sub)
    """
    # Use Firefox fingerprint - often bypasses Kwik/Cloudflare blocks better than Chrome
    scraper = cloudscraper.create_scraper(browser={'browser': 'firefox', 'platform': 'windows', 'mobile': False})
    play_url = f"{config.ANIMEPAHE_URL}/play/{anime_id}/{session}"
    
    try:
        log_debug(f"Phase 1: Fetching play page {play_url}")
        res = client.get(play_url, headers={"Referer": f"{config.ANIMEPAHE_URL}/anime/{anime_id}"})
        log_debug(f"Phase 1 status: {res.status_code}")
        
        if res.status_code != 200:
            log_debug(f"Phase 1 failed with {res.status_code}. Attempting mirror rotation...")
            if ensure_working_mirror(client):
                # Update URL and retry
                play_url = f"{config.ANIMEPAHE_URL}/play/{anime_id}/{session}"
                log_debug(f"Retrying Phase 1 with new mirror: {play_url}")
                res = client.get(play_url, headers={"Referer": f"{config.ANIMEPAHE_URL}/anime/{anime_id}"})
                log_debug(f"Phase 1 retry status: {res.status_code}")
            
        if res.status_code != 200: return None

        # Link extraction - capture full inner HTML to detect language badges
        # Pattern captures: (url, inner_html_of_link)
        pattern = r'href="(https?://(?:pahe\.win|kwik\.[^/]+)/[^"]+)"[^>]*>(.*?)</a>'
        raw_matches = re.findall(pattern, res.text, re.DOTALL)
        
        if not raw_matches:
            # Fallback: Mirrors inside script/JSON tags
            log_debug("Checking scripts for mirrors...")
            script_pattern = r'["\']((https?://(?:pahe\.win|kwik\.[^/]+)/[^"\']+))["\']'
            script_matches = re.findall(script_pattern, res.text)
            if script_matches:
                for url in script_matches:
                    raw_matches.append((url, "720p" if "720" in url else "unknown"))

        if not raw_matches:
            log_debug(f"No mirrors found. Snippet: {res.text[:300]}")
            return None
        
        # Parse each mirror: extract quality, group, and language
        mirrors = []
        for url, inner_html in raw_matches:
            text = re.sub(r'<[^>]+>', '', inner_html).strip()  # Strip HTML tags for text
            has_eng_badge = 'badge' in inner_html and '>eng<' in inner_html.lower()
            lang = 'en' if has_eng_badge else 'jap'
            mirrors.append((url, text, lang))
        
        log_debug(f"Mirrors found: {[(t, l) for _, t, l in mirrors]}")
        
        # Filter by language preference, then by quality
        available_langs = set(m[2] for m in mirrors)
        lang_matches = [m for m in mirrors if m[2] == target_lang]
        if not lang_matches:
            log_debug(f"No mirrors for lang={target_lang}, available: {available_langs}")
            return None, target_lang, available_langs
        
        selected_url = None
        actual_lang = lang_matches[0][2]
        target_q = target_quality.lower()
        
        # Try exact match first
        for url, text, lang in lang_matches:
            if target_q in text.lower():
                selected_url = url
                actual_lang = lang
                break
                
        # If no exact match, try normalized resolution match
        if not selected_url:
            for url, text, lang in lang_matches:
                m = re.search(r'(\d+)p', text.lower())
                if m:
                    resolution = int(m.group(1))
                    if resolution <= 360:
                        norm_q = "360p"
                    elif resolution <= 720:
                        norm_q = "720p"
                    else:
                        norm_q = "1080p"
                        
                    if norm_q == target_q:
                        selected_url = url
                        actual_lang = lang
                        break
                        
        if not selected_url: selected_url = lang_matches[0][0]
        
        log_debug(f"Phase 2: Resolving redirector {selected_url}")
        res = scraper.get(selected_url, headers={"Referer": play_url}, timeout=20)
        time.sleep(config.REDIRECT_WAIT_TIME) 
        
        kwik_match = re.search(r'https?://kwik\.[^/]+/[^"\'\s>]+', res.text)
        kwik_result = None
        if kwik_match:
            kwik_result = resolve_kwik_direct(kwik_match.group(0), selected_url, retry_with_browser=retry_with_browser)
        elif "kwik" in res.url:
            kwik_result = resolve_kwik_direct(res.url, selected_url, retry_with_browser=retry_with_browser)
        
        if kwik_result:
            return kwik_result, actual_lang, available_langs
        else:
            # Extraction failed (e.g. 403), but language WAS found - return empty avail_langs
            # so processor shows "Extraction failed" not "language not available"
            return None, actual_lang, set()
    except Exception as e:
        log_debug(f"Extraction error: {e}")
    return None, None, set()

def search_anime(client, query, return_all=False):
    """Returns (anime_id, title, api_ok) or if return_all=True returns (list_of_tuples, api_ok). api_ok=True means API responded (even if no match)."""
    # Normalize full-width colon to standard colon for search
    import Levenshtein
    
    query = query.replace('：', ':').replace(':', ' ')
    
    all_data = []
    api_ok = False
    
    search_url = f"{config.ANIMEPAHE_URL}/api?m=search&q={query}"
    try:
        res = client.get(search_url, headers={
            "X-Requested-With": "XMLHttpRequest",
        })
        if res.status_code != 200:
            log_debug(f"Search API error (Status {res.status_code}). Attempting mirror rotation...")
            if ensure_working_mirror(client):
                 search_url = f"{config.ANIMEPAHE_URL}/api?m=search&q={query}"
                 log_debug(f"Retrying search with new mirror: {search_url}")
                 res = client.get(search_url, headers={
                    "X-Requested-With": "XMLHttpRequest",
                })
        
        if res.status_code == 200:
            try:
                data = res.json()
                api_ok = True
                if data and data.get('data'):
                    all_data.extend(data['data'])
            except:
                log_debug("Search API returned non-JSON (DDoS-Guard?).")
        else:
            log_debug(f"Search API error (Status {res.status_code})")
    except Exception as e:
        log_debug(f"Search API exception: {e}")
    
    # Deduplicate by session ID
    seen_ids = set()
    unique_data = []
    for item in all_data:
        sid = item.get('session')
        if sid and sid not in seen_ids:
            seen_ids.add(sid)
            unique_data.append(item)
    all_data = unique_data
    
    # Fallback: if all results are movies/specials/OVAs and query doesn't look like 
    # a movie/special query, the main TV series might be listed under a different name.
    # Try scraping the anime page of any result to discover alternative titles and re-search.
    if all_data and not return_all:
        q_lower = query.lower()
        q_is_movie = any(x in q_lower for x in ['movie', 'movie:', 'the movie'])
        q_is_sp = bool(re.search(r'\b(?:special|ova|ona|specials)\b', q_lower))
        
        if not q_is_movie and not q_is_sp:
            has_tv = any(item.get('type', '').upper() == 'TV' for item in all_data)
            if not has_tv:
                log_debug(f"Search for '{query}' returned only movies/specials. Trying Jikan API for alternative titles...")
                
                # Ensure we have a working Jikan mirror
                if ensure_working_jikan_mirror(client):
                    try:
                        jikan_url = f"{config.JIKAN_API_URL}/anime?q={query}&type=tv&limit=1"
                        jikan_res = client.get(jikan_url, timeout=10)
                        
                        # If Jikan mirror fails (e.g. 503 or 429), try rotating once
                        if jikan_res.status_code >= 400:
                            log_debug(f"Jikan API error ({jikan_res.status_code}). Attempting mirror rotation...")
                            if ensure_working_jikan_mirror(client):
                                jikan_url = f"{config.JIKAN_API_URL}/anime?q={query}&type=tv&limit=1"
                                jikan_res = client.get(jikan_url, timeout=10)

                        if jikan_res.status_code == 200:
                            try:
                                jdata = jikan_res.json()
                            except:
                                log_debug("Jikan API returned invalid JSON.")
                                jdata = None
                                
                            if jdata and jdata.get('data'):
                                anime_info = jdata['data'][0]
                                alt_titles = set()
                                if anime_info.get('title_english'):
                                    alt_titles.add(anime_info['title_english'])
                                if anime_info.get('title_synonyms'):
                                    for syn in anime_info['title_synonyms']:
                                        alt_titles.add(syn)
                                
                                log_debug(f"Jikan API found alternative titles: {list(alt_titles)}")
                                for alt in alt_titles:
                                    alt_clean = alt.replace('：', ' ').replace(':', ' ')
                                    if alt_clean.lower() == query.lower():
                                        continue
                                    log_debug(f"Trying alternative title search: '{alt_clean}'")
                                    try:
                                        alt_url = f"{config.ANIMEPAHE_URL}/api?m=search&q={alt_clean}"
                                        alt_res = client.get(alt_url, headers={"X-Requested-With": "XMLHttpRequest"})
                                        if alt_res.status_code == 200:
                                            alt_data = alt_res.json()
                                            if alt_data and alt_data.get('data'):
                                                for aitem in alt_data['data']:
                                                    asid = aitem.get('session')
                                                    if asid and asid not in seen_ids:
                                                        seen_ids.add(asid)
                                                        all_data.append(aitem)
                                    except Exception as e:
                                        pass
                    except Exception as e:
                        log_debug(f"Jikan API fallback error: {e}")
            
    if not api_ok:
        return ([], False) if return_all else (None, None, False, float('inf'))
        
    if not all_data:
        return ([], True) if return_all else (None, None, True, float('inf'))
    
    if return_all:
        results = []
        for item in all_data:
            clean_t = re.sub(r'[\\/*?:"<>|：]', ' ', item.get('title'))
            clean_t = re.sub(r'\s+', ' ', clean_t).strip()
            results.append((item.get('session'), clean_t, item.get('title'), item))
        return results, True
        
    # Find closest match using seasonal-aware and type-aware Levenshtein distance
    best_match = None
    best_dist = float('inf')
    
    # Extract target season from query
    q_s_match = re.search(r'(?:Season|S)\s*(\d+)', query, re.IGNORECASE)
    q_s = q_s_match.group(1) if q_s_match else None
    
    q_lower = query.lower()
    # Clean query for distance: remove symbols and normalize spaces
    q_clean = re.sub(r'[^a-z0-9 ]', ' ', q_lower).strip()
    q_clean = re.sub(r'\s+', ' ', q_clean)

    for item in all_data:
        item_title_raw = item.get('title', '')
        item_title = item_title_raw.lower()
        
        # Clean item title for distance calculation
        t_clean = re.sub(r'[^a-z0-9 ]', ' ', item_title).strip()
        t_clean = re.sub(r'\s+', ' ', t_clean)
        
        dist = Levenshtein.distance(q_clean, t_clean)
        
        # Detect type in result
        is_movie = any(x in item_title for x in ['movie', 'movie:', 'the movie'])
        is_sp = bool(re.search(r'\b(?:special|ova|ona|specials|episode one)\b', item_title))
        
        q_is_movie = any(x in q_lower for x in ['movie', 'movie:', 'the movie'])
        q_is_sp = bool(re.search(r'\b(?:special|ova|ona|specials|episode one)\b', q_lower))

        # Type penalties/bonuses
        if is_movie and not q_is_movie:
            dist += 60 # Very heavy penalty to avoid movies for series folders
        if is_sp and not q_is_sp:
            dist += 40
        
        # Substring/Exact word match bonus
        if q_clean == t_clean:
            dist -= 20
        elif q_clean in t_clean:
            dist -= 10
        
        # Detect season in result
        res_s_match = re.search(r'(?:Season|S)\s*(\d+)', item_title, re.IGNORECASE)
        # If item title has no season number, we assume it's Season 1 / Base Title
        res_s = res_s_match.group(1) if res_s_match else "1"
        
        # Match Logic:
        # 1. If query specified a season (e.g. "Season 1"), and result is different number (e.g. "Season 2"), penalize.
        # 2. If query specified "Season 1", and result HAS NO season number, it's a very good match for base title.
        if q_s:
            if q_s != res_s:
                dist += 100 # Heavy penalty for different season numbers
            elif q_s == res_s and not res_s_match and q_s == "1":
                dist -= 5 # Bonus for matching Season 1 to a base title
        else:
            # If query has no season, penalize RESULTS that have Season numbers > 1
            if res_s != "1":
                dist += 30
        
        if dist < best_dist:
            best_dist = dist
            best_match = item
    
    if not best_match: best_match = all_data[0]
    
    anime_id = best_match.get('session')
    title = best_match.get('title')
    # Replace both colons for filenames/folders
    clean_title = re.sub(r'[\\/*?:"<>|：]', ' ', title)
    clean_title = re.sub(r'\s+', ' ', clean_title).strip()
    return anime_id, clean_title, True, best_dist  # API OK, matched

