from . import config, db, llm, orchestrator

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
    intent = llm.interpret_message(update.message.text, tasks, client)
    reply = orchestrator.apply_intent(conn, intent, now)
    await update.message.reply_text(reply)
