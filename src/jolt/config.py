import os
from datetime import datetime
from zoneinfo import ZoneInfo

STALE_THRESHOLD_DAYS = 3
QUIET_START_HOUR = 6   # first hour of the day pings are allowed
QUIET_END_HOUR = 23    # pings stop at 23:00 (hour 23 and later is quiet)
NAG_HOURS = (9, 13, 19)
DAILY_FOCUS_HOUR = 6
TIMEZONE = "Europe/Paris"
MODEL = "claude-haiku-4-5-20251001"


def now_paris() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def telegram_token() -> str:
    return os.environ["TELEGRAM_BOT_TOKEN"]


def telegram_chat_id() -> int:
    return int(os.environ["TELEGRAM_CHAT_ID"])


def anthropic_api_key() -> str:
    return os.environ["ANTHROPIC_API_KEY"]


def db_path() -> str:
    return os.environ.get("JOLT_DB_PATH", "data/jolt.db")
