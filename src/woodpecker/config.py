import os
from datetime import datetime
from zoneinfo import ZoneInfo

STALE_THRESHOLD_DAYS = 3
QUIET_START_HOUR = 6  # first hour of the day pings are allowed
QUIET_END_HOUR = 23  # pings stop at 23:00 (hour 23 and later is quiet)
# Priority bands derived from the raw Vikunja priority (0-5). High pushes hard, Mid gets a
# gentle poke, Low/unset gets nudged toward dropping. See selection.priority_band.
PRIORITY_HIGH_MIN = 3  # priority >= 3 -> high band
PRIORITY_MID_MIN = 1  # priority 1-2 -> mid band; 0 -> low band
# The nudge rhythm (spec 2026-10-08): one morning message and one check-in, every day.
FOCUS_HOUR = 9
CHECKIN_HOUR = 14
REFRAME_AFTER_BUMPS = 3  # the 3rd "t" on a frog turns the next morning into the reframe question
DROP_CONFIRM_MINUTES = 10  # a second "x" within this window drops the task
VAULT_SOON_DAYS = 7  # the morning lists vault tasks due within this many days
VAULT_MORNING_MAX = 3  # vault lines in the morning; the rest is counted in the footer
STEP_ANSWER_MINUTES = 30  # after "s", the next message within this window becomes the new title
TIMEZONE = "Europe/Paris"
MODEL = "claude-haiku-4-5-20251001"
# USD per million tokens for MODEL, from Anthropic's pricing. Update these together with
# MODEL. Haiku 4.5 is $1.00 in / $5.00 out per 1M tokens. Woodpecker sends no cached tokens, so
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


def vikunja_url() -> str:
    return os.environ["VIKUNJA_URL"]


def vikunja_token() -> str:
    return os.environ["VIKUNJA_TOKEN"]


def vikunja_project_id() -> int:
    return int(os.environ["VIKUNJA_PROJECT_ID"])


def _setting(name: str, default: str) -> str:
    # WOODPECKER_<name>, else the JOLT_<name> it was called before the rename (the .env on
    # jarvis may still use it), else the default.
    for key in (f"WOODPECKER_{name}", f"JOLT_{name}"):
        if key in os.environ:
            return os.environ[key]
    return default


def sidecar_path() -> str:
    # data/sidecar.db, not data/jolt.db: the latter is the pre-Vikunja database, still kept
    # on the host as a rollback artifact, and its `tasks` table is not a sidecar schema.
    # See docs/CUTOVER.md.
    return _setting("DB_PATH", "data/sidecar.db")


def log_level() -> str:
    return _setting("LOG_LEVEL", "INFO").upper()


def log_file() -> str | None:
    # Path for a persistent log file, written in addition to stdout. It lives under a
    # bind-mounted directory in the container (see docker-compose.yml), so the log
    # survives container recreation: Docker discards a recreated container's own stdout
    # on every redeploy, which was wiping the whole history. Set WOODPECKER_LOG_FILE="" to
    # disable file logging (local runs, tests).
    return _setting("LOG_FILE", "logs/woodpecker.log") or None


def vault_path() -> str:
    # The Obsidian vault, bind-mounted read-only (see docker-compose.yml).
    return _setting("VAULT_PATH", "/vault")


def call_cost_usd(input_tokens: int, output_tokens: int) -> float:
    """Dollar cost of one Claude call at MODEL's pricing, so each call's spend can be
    logged and later summed from the persistent log."""
    return (
        input_tokens * MODEL_PRICE_INPUT_USD_PER_MTOK
        + output_tokens * MODEL_PRICE_OUTPUT_USD_PER_MTOK
    ) / 1_000_000
