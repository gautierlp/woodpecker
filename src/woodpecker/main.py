import logging
import os
from logging.handlers import RotatingFileHandler

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from . import bot, config, llm, scheduler, sidecar
from .memory import ConversationMemory
from .store import Store
from .vikunja import VikunjaClient

logger = logging.getLogger(__name__)


def _build_log_handlers() -> list[logging.Handler]:
    """The handlers root logging is configured with: always a stdout stream (so
    `docker logs` works for the live container) and, when config.log_file() is set, also
    a rotating file. The file lives on a bind-mounted path, so it survives container
    recreation: Docker discards a recreated container's own stdout on every redeploy,
    which otherwise wiped the entire history. Kept side-effect-light (only creates the
    log directory) so it is unit-testable without touching the global root logger."""
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_file = config.log_file()
    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        # Rotate so a DEBUG-level log on a long-running bot cannot fill the disk:
        # at most 5 x 2 MB = 10 MB kept.
        handlers.append(
            RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        )
    for handler in handlers:
        handler.setFormatter(formatter)
    return handlers


def setup_logging() -> None:
    """Configure root logging once, at process start. Level is driven by WOODPECKER_LOG_LEVEL
    (default INFO; set DEBUG to trace everything). Even at DEBUG we keep the flood in
    check: our own woodpecker.* loggers run at the root level, third-party libraries are capped
    at INFO, and the byte-level HTTP loggers are pinned to WARNING. So DEBUG still shows
    Woodpecker's own lines and full Claude I/O without drowning them in poll-loop chatter.

    force=True so our handlers win even if an import configured root logging first."""
    logging.basicConfig(level=config.log_level(), handlers=_build_log_handlers(), force=True)
    for lib in ("telegram", "anthropic", "apscheduler"):
        logging.getLogger(lib).setLevel(logging.INFO)
    # httpx logs each request URL at INFO, and the Telegram token lives in that URL
    # path, so keep it at WARNING to avoid writing the bot token to the logs. Our own
    # woodpecker.* lines and _log_usage already cover what those requests were doing.
    for wire in ("httpx", "httpcore", "hpack"):
        logging.getLogger(wire).setLevel(logging.WARNING)


def _make_send(application, chat_id):
    async def _send_async(text):
        await application.bot.send_message(chat_id=chat_id, text=text)

    def send(text):
        logger.info("Sending message to chat %s (%d chars)", chat_id, len(text))
        logger.debug("Outbound message body: %s", text)
        application.create_task(_send_async(text))

    return send


def build_scheduler(store, send, client, vault_path) -> AsyncIOScheduler:
    """Build the cron scheduler: the morning message, the check-in, the weekly review.

    The jobs are coroutines on purpose: AsyncIOScheduler runs coroutine jobs on the
    bot's own asyncio event loop, the same thread that owns the sidecar connection and
    the Telegram send. A BackgroundScheduler with plain sync jobs ran them on a worker
    thread instead, where the SQLite connection is unusable and there is no running loop
    for the send, so every scheduled message crashed silently."""
    sched = AsyncIOScheduler(timezone=config.TIMEZONE)

    async def _morning():
        scheduler.send_morning(store, send, client, config.now_paris(), vault_path)

    async def _checkin():
        scheduler.send_checkin(store, send, config.now_paris(), vault_path)

    async def _weekly():
        scheduler.send_weekly_review(store, send, config.now_paris())

    # Pass the timezone to every CronTrigger explicitly. APScheduler does NOT stamp the
    # scheduler's timezone onto a trigger; a trigger built without one defaults to the
    # machine's local zone (UTC in the container), so every job fired 2 hours off Paris.
    tz = config.TIMEZONE
    sched.add_job(_morning, CronTrigger(hour=config.FOCUS_HOUR, minute=0, timezone=tz))
    sched.add_job(_checkin, CronTrigger(hour=config.CHECKIN_HOUR, minute=0, timezone=tz))
    sched.add_job(
        _weekly,
        CronTrigger(
            day_of_week=config.WEEKLY_REVIEW_DAY,
            hour=config.WEEKLY_REVIEW_HOUR,
            minute=0,
            timezone=tz,
        ),
    )
    return sched


def main() -> None:
    setup_logging()
    logger.info("Starting Woodpecker")
    os.makedirs(os.path.dirname(config.sidecar_path()) or ".", exist_ok=True)
    logger.info("Opening sidecar database at %s", config.sidecar_path())
    conn = sidecar.connect(config.sidecar_path())
    sidecar.init_db(conn)
    vk = VikunjaClient(config.vikunja_url(), config.vikunja_token(), config.vikunja_project_id())
    store = Store(vk, conn)
    client = llm.build_client(config.anthropic_api_key())
    chat_id = config.telegram_chat_id()
    logger.info(
        "Configured for chat_id=%s, model=%s, timezone=%s", chat_id, config.MODEL, config.TIMEZONE
    )

    memory = ConversationMemory()

    async def _post_init(application) -> None:
        # Runs once the event loop is up. Build the send and start the scheduler here so
        # AsyncIOScheduler binds to the bot's running loop: the jobs then execute on the
        # same thread that owns the sidecar connection and the Telegram send.
        send = bot.make_recording_send(_make_send(application, chat_id), memory, chat_id)
        sched = build_scheduler(store, send, client, config.vault_path())
        sched.start()
        application.bot_data["scheduler"] = sched
        logger.info(
            "Scheduler started: morning at %02d:00, check-in at %02d:00, review %s %02d:00, "
            "vault at %s",
            config.FOCUS_HOUR,
            config.CHECKIN_HOUR,
            config.WEEKLY_REVIEW_DAY,
            config.WEEKLY_REVIEW_HOUR,
            config.vault_path(),
        )

    application = Application.builder().token(config.telegram_token()).post_init(_post_init).build()
    application.bot_data.update(
        {"store": store, "client": client, "chat_id": chat_id, "memory": memory}
    )
    application.add_handler(CommandHandler("start", bot.handle_start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
    application.add_error_handler(bot.handle_error)

    logger.info("Starting Telegram polling")
    application.run_polling()


if __name__ == "__main__":
    main()
