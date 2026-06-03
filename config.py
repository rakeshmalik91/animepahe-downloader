import os

# AnimePahe Downloader Configuration

# Base directory where all anime will be downloaded
BASE_DOWNLOAD_DIR = r"D:\Downloads\ANIME"

# Default video quality (360p, 720p, 1080p)
DEFAULT_QUALITY = "720p"
DEFAULT_LANGUAGE = "en"                 # "en" for English dub, "jap" for Japanese sub

# Target sites
ANIMEPAHE_URLS = [
    "https://animepahe.pw",
    "https://animepahe.com",
    "https://animepahe.org",
    "https://animepahe.si",
]
KWIK_URLS = [
    "https://kwik.si",
    "https://kwik.cx",
]
JIKAN_API_URLS = [
    "https://api.jikan.moe/v4",
]

# Browser emulation
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# Extraction settings
REDIRECT_WAIT_TIME = 5                  # Seconds to wait on pahe.win gateway
FORCE_IPV4 = False                       # Fix for Connection Reset issues on some networks
OPEN_BROWSER_ON_FAIL = False            # Open direct link in browser if download fails
AUTO_SKIP_DAYS = 30                     # Stop checking anime with no new episodes for this many days
AUTO_REJECT_LANGUAGE_FALLBACK = True    # If true, auto-select 'no' when asked to download a different language
ENABLE_BROWSER_BYPASS = True            # If kwik returns 403, open a browser for manual solve
BROWSER_VERSION_MAIN = None              # Chrome version (None for dynamic auto-detect; set to e.g. 148 if dynamic detection fails)
MAX_DISTANCE_THRESHOLD = 20             # Max allowed Levenshtein distance before prompting for URL

# Cloudflare bypass cookies for Kwik downloads are now stored automatically in the database.
# To trigger a refresh, simply wait for a 403 or delete the 'sessions' table in tracking.db.

# Retry settings
MAX_DOWNLOAD_RETRIES = 5               # Auto retry this many times before prompting user
DOWNLOAD_RETRY_BASE_DELAY = 2           # Base seconds to wait before retrying
DOWNLOAD_RETRY_MULTIPLIER = 2           # Multiplier for exponential backoff (e.g. 2, 4, 8)

# Segmented download settings
ENABLE_SEGMENTED_DOWNLOAD = True
DOWNLOAD_SEGMENTS = 4
SLOW_DOWNLOAD_THRESHOLD_KBPS = 500      # Fallback to normal download if speed drops below this
DEFAULT_PARALLEL_DOWNLOADS = 2          # Number of concurrent episode downloads

# Accessibility settings
ENABLE_NOTIFICATIONS = True

# Database for tracking
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracking.db")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_log.txt")
