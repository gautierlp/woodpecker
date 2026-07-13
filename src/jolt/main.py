import logging
import os

from anthropic import Anthropic
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from . import bot, config, db, scheduler
from .memory import ConversationMemory

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """Configure root logging once, at process start. Level is driven by JOLT_LOG_LEVEL
    (default INFO; set DEBUG to trace everything). Even at DEBUG we keep the flood in
    check: our own jolt.* loggers run at the root level, third-party libraries are capped
    at INFO, and the byte-level HTTP loggers are pinned to WARNING. So DEBUG still shows
    Jolt's own lines and full Claude I/O without drowning them in poll-loop chatter."""
    logging.basicConfig(
        level=config.log_level(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for lib in ("telegram", "anthropic", "apscheduler"):
        logging.getLogger(lib).setLevel(logging.INFO)
    # httpx logs each request URL at INFO, and the Telegram token lives in that URL
    # path, so keep it at WARNING to avoid writing the bot token to the logs. Our own
    # jolt.* lines and _log_usage already cover what those requests were doing.
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


def build_scheduler(conn, send, client) -> AsyncIOScheduler:
    """Build the cron scheduler for the daily focus and nags.

    The jobs are coroutines on purpose: AsyncIOScheduler runs coroutine jobs on the
    bot's own asyncio event loop, the same thread that owns `conn` and the Telegram
    send. A BackgroundScheduler with plain sync jobs ran them on a worker thread
    instead, where the SQLite connection is unusable and there is no running loop for
    the send, so every scheduled message crashed silently."""
    sched = AsyncIOScheduler(timezone=config.TIMEZONE)

    async def _daily_focus():
        scheduler.send_daily_focus(conn, send, client, config.now_paris())

    async def _nags():
        scheduler.send_nags(conn, send, client, config.now_paris())

    sched.add_job(_daily_focus, CronTrigger(hour=config.DAILY_FOCUS_HOUR, minute=0))
    for nag_hour in config.NAG_HOURS:
        sched.add_job(_nags, CronTrigger(hour=nag_hour, minute=0))
    return sched


def main() -> None:
    setup_logging()
    logger.info("Starting Jolt")
    os.makedirs(os.path.dirname(config.db_path()) or ".", exist_ok=True)
    logger.info("Opening database at %s", config.db_path())
    conn = db.connect(config.db_path())
    db.init_db(conn)
    client = Anthropic(api_key=config.anthropic_api_key())
    chat_id = config.telegram_chat_id()
    logger.info(
        "Configured for chat_id=%s, model=%s, timezone=%s", chat_id, config.MODEL, config.TIMEZONE
    )

    memory = ConversationMemory()

    async def _post_init(application) -> None:
        # Runs once the event loop is up. Build the send and start the scheduler here so
        # AsyncIOScheduler binds to the bot's running loop: the jobs then execute on the
        # same thread that owns `conn` and the Telegram send.
        send = bot.make_recording_send(_make_send(application, chat_id), memory, chat_id)
        sched = build_scheduler(conn, send, client)
        sched.start()
        application.bot_data["scheduler"] = sched
        logger.info(
            "Scheduler started: daily focus at %02d:00, nags at %s",
            config.DAILY_FOCUS_HOUR,
            ", ".join(f"{h:02d}:00" for h in config.NAG_HOURS),
        )

    application = (
        Application.builder().token(config.telegram_token()).post_init(_post_init).build()
    )
    application.bot_data.update(
        {"conn": conn, "client": client, "chat_id": chat_id, "memory": memory}
    )
    application.add_handler(CommandHandler("start", bot.handle_start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
    application.add_error_handler(bot.handle_error)

    logger.info("Starting Telegram polling")
    application.run_polling()


if __name__ == "__main__":
    main()
