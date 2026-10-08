import logging

from . import config, llm, orchestrator, render, replies
from .vikunja import VikunjaError

logger = logging.getLogger(__name__)

WELCOME = (
    "Hey, I'm Woodpecker. Tell me what you need to do and I'll hold it for you. "
    "Each morning I name one thing and a first step; answer in your own words, "
    "or with one letter as a shortcut. "
    'Just type tasks in plain language, like "call the vet tomorrow".'
)


def make_recording_send(send, memory, chat_id):
    """Wrap the raw send so every message Woodpecker initiates (morning, check-in) is
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
    store = context.bot_data["store"]
    client = context.bot_data["client"]
    memory = context.bot_data["memory"]
    now = config.now_paris()
    text = update.message.text or ""
    logger.info("Received message (%d chars)", len(text))
    logger.debug("Inbound message body: %s", text)
    try:
        # A one-letter reply is answered here, with no Claude call. Anything else goes to
        # the free-text flow below.
        reply = replies.answer(store, text, now)
        if reply is None:
            reply = _free_text(store, client, memory, chat_id, text, now)
    except VikunjaError:
        logger.exception("Vikunja unreachable while handling message")
        await update.message.reply_text(
            "My task list is unreachable right now, try again in a moment."
        )
        return
    memory.add(chat_id, text, reply)
    # The pending nag has now been answered (or superseded by real conversation), so
    # it must not colour the next, unrelated message.
    memory.clear_outbound(chat_id)
    await update.message.reply_text(reply)


def _free_text(store, client, memory, chat_id, text, now) -> str:
    tasks = store.list_pending()
    history = memory.get(chat_id)
    outbound = memory.get_outbound(chat_id)
    display_ids = store.load_display(chat_id)
    intents = llm.interpret_message(
        text,
        tasks,
        now,
        client,
        history=history,
        recent_outbound=outbound,
        display_ids=display_ids,
    )
    logger.info("Interpreted into %d intent(s): %s", len(intents), [i.action for i in intents])
    # A "list" intent needs a snapshot of the backlog to both render the reply and
    # save as the display order for the next message's numbered references. Fetch it
    # once, at the point the intent is processed (so any earlier intents in this same
    # batch have already mutated the backlog), and reuse it for both instead of
    # letting each step fetch its own copy.
    replies_out = []
    shown = None
    for intent in intents:
        if intent.action == "list":
            shown = store.list_pending()
            replies_out.append(render.render_backlog(shown, now))
        else:
            reply, changed = orchestrator.apply(store, intent, now)
            if changed:
                _answer_frog(store, intent)
            replies_out.append(reply)
    reply = "\n".join(replies_out)
    logger.debug("Reply body: %s", reply)
    # If we just printed the backlog, remember the exact order shown, so the numbers in
    # the next message resolve against this list rather than a later, shifted order.
    if shown is not None:
        store.save_display(chat_id, [t.id for t in render.display_order(shown, now)])
    return reply


# A plain-language answer about the open frog counts like its letter: "done" is d, "drop
# it" is x, "push it to Friday" is t. The letters stay a shortcut, not the only way.
_FROG_LETTER = {"complete": "d", "drop": "x", "reschedule": "t"}


def _answer_frog(store, intent) -> None:
    """Mark the frog answered when an applied intent is about the task of the open frog or
    reframe prompt. Call it only after the store made the change."""
    letter = _FROG_LETTER.get(intent.action)
    if letter is None or (letter == "t" and intent.deadline is None):
        return  # a priority bump moves no date, so it is not a "tomorrow"
    prompt = store.get_open_prompt()
    if prompt is None or prompt.kind not in (replies.FROG, replies.REFRAME):
        return
    if intent.task_id != prompt.task_ids[0]:
        return
    store.mark_frog_answered(prompt.sent_at.date(), letter)
    if letter != "t":  # like the letter t, a new date keeps the prompt open
        store.clear_open_prompt()
    logger.info("reply %s %s-text", intent.task_id, letter)


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
