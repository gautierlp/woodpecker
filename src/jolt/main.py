import os

from anthropic import Anthropic
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from . import bot, config, db, scheduler


def _make_send(application, chat_id):
    async def _send_async(text):
        await application.bot.send_message(chat_id=chat_id, text=text)

    def send(text):
        application.create_task(_send_async(text))

    return send


def main() -> None:
    os.makedirs(os.path.dirname(config.db_path()) or ".", exist_ok=True)
    conn = db.connect(config.db_path())
    db.init_db(conn)
    client = Anthropic(api_key=config.anthropic_api_key())
    chat_id = config.telegram_chat_id()

    application = Application.builder().token(config.telegram_token()).build()
    application.bot_data.update({"conn": conn, "client": client, "chat_id": chat_id})
    application.add_handler(CommandHandler("start", bot.handle_start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))
    application.add_error_handler(bot.handle_error)

    send = _make_send(application, chat_id)
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

    application.run_polling()


if __name__ == "__main__":
    main()
