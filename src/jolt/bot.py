from . import config, db, llm, orchestrator


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
