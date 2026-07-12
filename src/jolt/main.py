import logging
import os

from anthropic import Anthropic
from apscheduler.schedulers.background import BackgroundScheduler
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

    application = Application.builder().token(config.telegram_token()).build()
    memory = ConversationMemory()
    application.bot_data.update(
        {"conn": conn, "client": client, "chat_id": chat_id, "memory": memory}
    )
    application.add_handler(CommandHandler("start", bot.handle_start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
    application.add_error_handler(bot.handle_error)

    send = bot.make_recording_send(_make_send(application, chat_id), memory, chat_id)
    sched = BackgroundScheduler(timezone=config.TIMEZONE)
    sched.add_job(
        lambda: scheduler.send_daily_focus(conn, send, client, config.now_paris()),
        CronTrigger(hour=config.DAILY_FOCUS_HOUR, minute=0),
    )
    for nag_hour in config.NAG_HOURS:
        sched.add_job(
            lambda: scheduler.send_nags(conn, send, client, config.now_paris()),
            CronTrigger(hour=nag_hour, minute=0),
        )
    sched.start()
    logger.info(
        "Scheduler started: daily focus at %02d:00, nags at %s",
        config.DAILY_FOCUS_HOUR,
        ", ".join(f"{h:02d}:00" for h in config.NAG_HOURS),
    )

    logger.info("Starting Telegram polling")
    application.run_polling()


if __name__ == "__main__":
    main()
