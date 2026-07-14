import os
from datetime import datetime
from zoneinfo import ZoneInfo

STALE_THRESHOLD_DAYS = 3
SLOW_RESURFACE_DAYS = 7  # a waved-off normal, stale task is re-poked at most once this often
QUIET_START_HOUR = 6  # first hour of the day pings are allowed
QUIET_END_HOUR = 23  # pings stop at 23:00 (hour 23 and later is quiet)
NAG_HOURS = (9, 13, 19)
DAILY_FOCUS_HOUR = 6
TIMEZONE = "Europe/Paris"
MODEL = "claude-haiku-4-5-20251001"
# USD per million tokens for MODEL, from Anthropic's pricing. Update these together with
# MODEL. Haiku 4.5 is $1.00 in / $5.00 out per 1M tokens. Jolt sends no cached tokens, so
# input is always billed at the full rate.
MODEL_PRICE_INPUT_USD_PER_MTOK = 1.00
MODEL_PRICE_OUTPUT_USD_PER_MTOK = 5.00
ANTHROPIC_TIMEOUT_SECONDS = 30  # a Claude call that hangs past this fails fast, freeing the loop
INTERPRET_MAX_TOKENS = 4000  # room for many record_intent calls in one pasted list


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


def log_level() -> str:
    return os.environ.get("JOLT_LOG_LEVEL", "INFO").upper()


def log_file() -> str | None:
    # Path for a persistent log file, written in addition to stdout. It lives under a
    # bind-mounted directory in the container (see docker-compose.yml), so the log
    # survives container recreation: Docker discards a recreated container's own stdout
    # on every redeploy, which was wiping the whole history. Set JOLT_LOG_FILE="" to
    # disable file logging (local runs, tests).
    return os.environ.get("JOLT_LOG_FILE", "logs/jolt.log") or None


def call_cost_usd(input_tokens: int, output_tokens: int) -> float:
    """Dollar cost of one Claude call at MODEL's pricing, so each call's spend can be
    logged and later summed from the persistent log."""
    return (
        input_tokens * MODEL_PRICE_INPUT_USD_PER_MTOK
        + output_tokens * MODEL_PRICE_OUTPUT_USD_PER_MTOK
    ) / 1_000_000
