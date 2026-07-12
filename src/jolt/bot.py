import logging

from . import config, db, llm, orchestrator

logger = logging.getLogger(__name__)

WELCOME = (
    "Hey, I'm Jolt. Tell me what you need to do and I'll hold it for you. "
    "I'll point you at one thing each day and get louder about anything you keep "
    'dodging. Just type tasks in plain language, like "call the vet tomorrow".'
)


async def handle_start(update, context) -> None:
    if update.effective_chat.id != context.bot_data["chat_id"]:
        return
    await update.message.reply_text(WELCOME)


async def handle_message(update, context) -> None:
    chat_id = context.bot_data["chat_id"]
    if update.effective_chat.id != chat_id:
        return
    conn = context.bot_data["conn"]
    client = context.bot_data["client"]
    now = config.now_paris()
    tasks = db.list_all(conn)
    intent = llm.interpret_message(update.message.text, tasks, now, client)
    reply = orchestrator.apply_intent(conn, intent, now)
    await update.message.reply_text(reply)


async def handle_error(update, context) -> None:
    """Last line of defence: log any unhandled failure and, best-effort, tell the
    user so a dropped reply never leaves them staring at silence."""
    logger.error("Unhandled error while processing update", exc_info=context.error)
    chat_id = context.bot_data.get("chat_id")
    if chat_id is None:
        return
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Something glitched on my end and I lost that. Say it again?",
        )
    except Exception:
        logger.exception("Failed to notify user about the earlier error")
