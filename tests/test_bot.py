import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from jolt import bot, db
from jolt.llm import Intent


def fresh():
    conn = db.connect(":memory:")
    db.init_db(conn)
    return conn


def make_update(text, chat_id=42):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=chat_id))


def make_context(conn, intent, chat_id=42):
    # client is unused because interpret_message is monkeypatched in the test
    return SimpleNamespace(bot_data={"conn": conn, "client": object(), "chat_id": chat_id})


def test_handle_message_adds_task_and_replies(monkeypatch):
    conn = fresh()
    monkeypatch.setattr(bot.llm, "interpret_message",
                        lambda msg, tasks, client: Intent(action="add", text="call vet"))
    update = make_update("remind me to call vet")
    context = make_context(conn, None)
    asyncio.run(bot.handle_message(update, context))
    update.message.reply_text.assert_awaited_once()
    assert "call vet" in update.message.reply_text.call_args.args[0]
    assert len(db.list_pending(conn)) == 1


def test_handle_message_ignores_foreign_chat(monkeypatch):
    conn = fresh()
    called = False

    def spy(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(bot.llm, "interpret_message", spy)
    update = make_update("hello", chat_id=999)
    context = make_context(conn, None, chat_id=42)
    asyncio.run(bot.handle_message(update, context))
    assert called is False
    update.message.reply_text.assert_not_awaited()
