# AnimePahe Auto-Downloader Deep-Dive

This document details the underlying mechanics of the AnimePahe Auto-Downloader, focusing specifically on network handling, Cloudflare circumvention, and the multi-tiered concurrency models used for extraction and downloading.

## 1. Cloudflare Handling & Network Layer (`modules/scraper.py`)

AnimePahe and its primary video host (Kwik) are heavily guarded by Cloudflare and other bot-protection suites. The script relies on a resilient, multi-layered evasion strategy:

### **Tier 1: Intelligent Headless Scraping**
- Uses `cloudscraper` and/or `curl_cffi`, matching modern TLS and JA3 fingerprints.
- Deliberately spoofs standard browser fingerprints (e.g., Firefox on Windows) to look legitimate and bypass standard checks natively.
- Global network calls (managed via `httpx.Client`) are continuously injected with the bypass tokens, including the `__ddg2_` cookie, User-Agents, and referrers derived dynamically from active mirrors.

### **Tier 2: Interactive Browser Fallback (Selenium)**
- If headless scraping hits an impenetrable challenge (`Cloudflare`, `Just a moment`, or `Verify` titles appear in the response payload), the script triggers `get_browser_cookies()`.
- This spins up a temporary, visible Selenium webdriver instance.
- It pauses and allows the user/system to resolve the Javascript challenge or Captcha.
- Once the page unblocks, the script intercepts and extracts the active, pre-authorized cookies (for both base domains and CDNs) along with the User-Agent, caches them, and resumes execution headlessly using this newly validated session.

## 2. Parallel Episode Resolution (`modules/processor.py`)

Configured through the `--parallel N` argument, the script employs `concurrent.futures.ThreadPoolExecutor` to process multiple episodes concurrently, drastically reducing overall queue times.

- **Sequential Pre-Warming:** Firing 4 simultaneous initial API requests often triggers immediate DDoS protection blocks from Cloudflare. To avoid this, the script sequence-locks the initialization. The *first* episode in the batch is deliberately launched and authorized independently. Once its secure connection pattern is established and validated, the lock releases, allowing all subsequent episode threads to flood in utilizing the pre-warmed tunnel context.
- **Non-blocking Flow:** When running with `--parallel > 1`, interactive components dynamically disable themselves. Fallback prompts (like manual language or quality resolution tasks) are auto-rejected to ensure background workers don't lock the process or overlap output states in the console.

## 3. Segmented File Downloading (`modules/downloader.py`)

To circumvent single-stream bandwidth throttling imposed by hosting providers (Kwik limits straight downloads), the core downloader engine executes segmented retrieval:

- **Verification:** An initial `HEAD`/`GET` stream request captures the target `Content-Length` and checks for the `Accept-Ranges: bytes` header flag.
- **Partitioning:** Provided the file is substantial (> 1MB) and the server permits range requests, the filesize is dynamically divided by the target sections (`config.DOWNLOAD_SEGMENTS`, dynamically falling back to 4 parts).
- **Secondary Concurrency:** A brand new, isolated `ThreadPoolExecutor` spawns strictly for the episode. 
- **Partial Fetching:** Target partitions are downloaded concurrently using the HTTP `Range: bytes=START-END` instruction. Temporary blocks are piped to `.part{0}`, `.part{n}` fragments alongside the main file.
- **Sequential Reassembly:** Upon successful termination of all segment workers, the script linearly merges the isolated bitstreams down into the final packaged container (`.mp4`), immediately deleting the temporary remnants.

## 4. Robust Anime Identification & Search Flow (`modules/scraper.py`)

Matching local folders against AnimePahe's database is fraught with naming discrepancies (e.g. "Meitantei Conan" vs. "Case Closed" vs. "Detective Conan") and API limitations (their autocomplete restricts results and does not paginate). The script handles mapping using a robust multi-tiered fallback system:

### **Tier 1: Intelligent String Distance & Type Matching**
- Local folders are stripped of release group noise (`SubsPlease`, `1080p`, dual-audio tags, etc.) before querying.
- Results are scored using the **Levenshtein Distance** against the query.
- **Contextual Type Penalties:** A huge algorithmic penalty is applied if a search for a standard TV series returns a "Movie", "Special", "OVA" or "ONA" (identified via Regex and API tags), effectively blocking sequel movies from taking over a TV series tracking entry.
- **Season Targeting:** If the folder implies a season (e.g., `Season 2`), the script isolates this token and ensures the matched title aligns with that season count, applying bonuses or penalties accordingly.

### **Tier 2: The Jikan API Fallback Engine**
- Because the AnimePahe API search logic heavily prioritizes newly added movies and restricts its `m=search` autocomplete to a small chunk, main TV series are frequently hidden from search results entirely if there are too many franchise movies.
- When the script detects that a search for a TV series returned **only** Movies/OVAs/Specials, the primary search logic suspends itself and triggers a fallback.
- It queries the official `api.jikan.moe` (MyAnimeList's public REST API) for the exact folder query.
- It extracts the official `title_english` and any mapped `title_synonyms`.
- It recursively feeds these alternative titles back into the AnimePahe search API under the hood. For example, a search for `"Meitantei Conan"`, which initially fails, transparently discovers the synonym `"Detective Conan"`, yielding the correct AnimePahe internal `session_id` and guaranteeing tracking continuity without any user interaction.
