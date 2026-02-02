import os

from dotenv import load_dotenv

load_dotenv()

_raw = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/greytg",
)
# Use psycopg 3 driver; avoid psycopg2 (no build required)
if _raw.startswith("postgresql://") and not _raw.startswith("postgresql+"):
    _raw = _raw.replace("postgresql://", "postgresql+psycopg://", 1)
DATABASE_URL = _raw

SESSION_ENC_KEY = os.getenv("SESSION_ENC_KEY", "")
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID", "")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
CALLBACK_SIGNING_SECRET = os.getenv("CALLBACK_SIGNING_SECRET", "")
DEV_CALLBACK_RECEIVER = os.getenv("DEV_CALLBACK_RECEIVER", "").lower() in ("1", "true", "yes")
