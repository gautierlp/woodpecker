import logging

from . import config, db, llm, orchestrator

logger = logging.getLogger(__name__)

WELCOME = (
    "Hey, I'm Jolt. Tell me what you need to do and I'll hold it for you. "
    "I'll point you at one thing each day and get louder about anything you keep "
    'dodging. Just type tasks in plain language, like "call the vet tomorrow".'
)


def make_recording_send(send, memory, chat_id):
    """Wrap the raw send so every message Jolt initiates (nags, daily focus) is
    remembered as the chat's pending outbound before going out. That lets a later
    reply like "done" be resolved against the nag it answers."""

    def recording_send(text):
        memory.note_outbound(chat_id, text)
        send(text)

    return recording_send


async def handle_start(update, context) -> None:
    if update.effective_chat.id != context.bot_data["chat_id"]:
        logger.warning("Ignoring /start from unauthorized chat %s", update.effective_chat.id)
        return
    logger.info("Handling /start")
    await update.message.reply_text(WELCOME)


async def handle_message(update, context) -> None:
    chat_id = context.bot_data["chat_id"]
    if update.effective_chat.id != chat_id:
        logger.warning("Ignoring message from unauthorized chat %s", update.effective_chat.id)
        return
    conn = context.bot_data["conn"]
    client = context.bot_data["client"]
    memory = context.bot_data["memory"]
    now = config.now_paris()
    text = update.message.text or ""
    logger.info("Received message (%d chars)", len(text))
    logger.debug("Inbound message body: %s", text)
    tasks = db.list_all(conn)
    history = memory.get(chat_id)
    outbound = memory.get_outbound(chat_id)
    intents = llm.interpret_message(
        text, tasks, now, client, history=history, recent_outbound=outbound
    )
    logger.info("Interpreted into %d intent(s): %s", len(intents), [i.action for i in intents])
    reply = "\n".join(orchestrator.apply_intent(conn, intent, now) for intent in intents)
    logger.debug("Reply body: %s", reply)
    memory.add(chat_id, text, reply)
    # The pending nag has now been answered (or superseded by real conversation), so
    # it must not colour the next, unrelated message.
    memory.clear_outbound(chat_id)
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
