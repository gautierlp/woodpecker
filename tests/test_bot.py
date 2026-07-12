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
    monkeypatch.setattr(
        bot.llm,
        "interpret_message",
        lambda msg, tasks, now, client: [Intent(action="add", text="call vet")],
    )
    update = make_update("remind me to call vet")
    context = make_context(conn, None)
    asyncio.run(bot.handle_message(update, context))
    update.message.reply_text.assert_awaited_once()
    assert "call vet" in update.message.reply_text.call_args.args[0]
    assert len(db.list_pending(conn)) == 1


def test_handle_message_saves_every_task_and_confirms_each(monkeypatch):
    conn = fresh()
    monkeypatch.setattr(
        bot.llm,
        "interpret_message",
        lambda msg, tasks, now, client: [
            Intent(action="add", text="cancel gym"),
            Intent(action="add", text="file taxes"),
            Intent(action="add", text="file expenses"),
        ],
    )
    update = make_update("(three tasks at once)")
    context = make_context(conn, None)
    asyncio.run(bot.handle_message(update, context))
    assert len(db.list_pending(conn)) == 3
    reply = update.message.reply_text.call_args.args[0]
    assert "cancel gym" in reply
    assert "file taxes" in reply
    assert "file expenses" in reply


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


def test_handle_start_replies_with_welcome():
    update = make_update("/start")
    context = make_context(fresh(), None)
    asyncio.run(bot.handle_start(update, context))
    update.message.reply_text.assert_awaited_once()
    assert "Jolt" in update.message.reply_text.call_args.args[0]


def test_handle_start_ignores_foreign_chat():
    update = make_update("/start", chat_id=999)
    context = make_context(fresh(), None, chat_id=42)
    asyncio.run(bot.handle_start(update, context))
    update.message.reply_text.assert_not_awaited()


def make_error_context(error, chat_id=42):
    return SimpleNamespace(
        error=error,
        bot=SimpleNamespace(send_message=AsyncMock()),
        bot_data={"chat_id": chat_id},
    )


def test_handle_error_notifies_user():
    context = make_error_context(RuntimeError("boom"))
    asyncio.run(bot.handle_error(make_update("hi"), context))
    context.bot.send_message.assert_awaited_once()
    assert context.bot.send_message.call_args.kwargs["chat_id"] == 42


def test_handle_error_swallows_notify_failure():
    # If even the notification can't be sent (e.g. Telegram unreachable), the
    # error handler must not raise, or the failure cascades.
    context = make_error_context(RuntimeError("boom"))
    context.bot.send_message.side_effect = RuntimeError("telegram down")
    asyncio.run(bot.handle_error(make_update("hi"), context))  # must not raise
