from pathlib import Path

# =========================
# REQUIRED
# =========================
BOT_TOKEN = "7736671703:AAEvI8KYlEgBRm7sLO8Q7h-FtMSlZNVhulE"

# =========================
# OPTIONAL
# =========================
ADMIN_IDS = set()  # Example: {123456789}

# Telegram/API behaviour
BOT_NAME = "ABSOLUTE CINEMA"
LANGUAGE = "ru"

# Download limits
MAX_CONCURRENT_JOBS = 2
MAX_QUEUE_PER_USER = 3

# This is the bot's configured safety/size guard.
# Telegram limits can vary by API/deployment, so keep this configurable.
MAX_UPLOAD_BYTES = 1_900_000_000

# yt-dlp / FFmpeg
SOCKET_TIMEOUT = 25
RETRIES = 5
FRAGMENT_RETRIES = 5

# Progress message refresh interval
PROGRESS_INTERVAL = 2.5

# Paths
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DOWNLOAD_DIR = BASE_DIR / "downloads"
TMP_DIR = BASE_DIR / "tmp"

for p in (DATA_DIR, DOWNLOAD_DIR, TMP_DIR):
    p.mkdir(parents=True, exist_ok=True)
