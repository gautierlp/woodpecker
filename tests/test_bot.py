import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from jolt import bot, db
from jolt.llm import Intent
from jolt.memory import ConversationMemory

TZ = ZoneInfo("Europe/Paris")


def fresh():
    conn = db.connect(":memory:")
    db.init_db(conn)
    return conn


def make_update(text, chat_id=42):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=chat_id))


def make_context(conn, intent, chat_id=42, memory=None):
    # client is unused because interpret_message is monkeypatched in the test
    return SimpleNamespace(
        bot_data={
            "conn": conn,
            "client": object(),
            "chat_id": chat_id,
            "memory": memory or ConversationMemory(),
        }
    )


def test_handle_message_adds_task_and_replies(monkeypatch):
    conn = fresh()
    monkeypatch.setattr(
        bot.llm,
        "interpret_message",
        lambda msg, tasks, now, client, history=None, recent_outbound=None, display_ids=None: [
            Intent(action="add", text="call vet")
        ],
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
        lambda msg, tasks, now, client, history=None, recent_outbound=None, display_ids=None: [
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


def test_handle_message_passes_prior_history_to_llm(monkeypatch):
    # The follow-up fix: the previous turn must reach interpret_message so a bare
    # "Yes" can be resolved.
    conn = fresh()
    memory = ConversationMemory()
    memory.add(42, "is 32 blocked by 31?", "Not currently. Want me to set that up?")
    seen = {}

    def capture(msg, tasks, now, client, history=None, recent_outbound=None, display_ids=None):
        seen["history"] = history
        return [Intent(action="answer", reply="ok")]

    monkeypatch.setattr(bot.llm, "interpret_message", capture)
    update = make_update("Yes")
    context = make_context(conn, None, memory=memory)
    asyncio.run(bot.handle_message(update, context))
    assert seen["history"] == [
        {"role": "user", "content": "is 32 blocked by 31?"},
        {"role": "assistant", "content": "Not currently. Want me to set that up?"},
    ]


def test_handle_message_records_turn_in_memory(monkeypatch):
    # After replying, the exchange must be stored so the *next* message has context.
    conn = fresh()
    memory = ConversationMemory()
    monkeypatch.setattr(
        bot.llm,
        "interpret_message",
        lambda msg, tasks, now, client, history=None, recent_outbound=None, display_ids=None: [
            Intent(action="answer", reply="Want me to set that up?")
        ],
    )
    update = make_update("is 32 blocked by 31?")
    context = make_context(conn, None, memory=memory)
    asyncio.run(bot.handle_message(update, context))
    assert memory.get(42) == [
        {"role": "user", "content": "is 32 blocked by 31?"},
        {"role": "assistant", "content": "Want me to set that up?"},
    ]


def test_handle_message_passes_pending_outbound_to_llm(monkeypatch):
    # A reply to a nag must carry the nag into interpret_message as context.
    conn = fresh()
    memory = ConversationMemory()
    memory.note_outbound(42, "Still the taxes. Two minutes. Go.")
    seen = {}

    def capture(msg, tasks, now, client, history=None, recent_outbound=None, display_ids=None):
        seen["outbound"] = recent_outbound
        return [Intent(action="answer", reply="ok")]

    monkeypatch.setattr(bot.llm, "interpret_message", capture)
    update = make_update("done")
    context = make_context(conn, None, memory=memory)
    asyncio.run(bot.handle_message(update, context))
    assert seen["outbound"] == "Still the taxes. Two minutes. Go."


def test_handle_message_clears_outbound_after_reply(monkeypatch):
    # Once the user has engaged, the pending nag is spent and must not haunt the
    # next unrelated message.
    conn = fresh()
    memory = ConversationMemory()
    memory.note_outbound(42, "Still the taxes. Two minutes. Go.")
    monkeypatch.setattr(
        bot.llm,
        "interpret_message",
        lambda msg, tasks, now, client, history=None, recent_outbound=None, display_ids=None: [
            Intent(action="answer", reply="ok")
        ],
    )
    update = make_update("done")
    context = make_context(conn, None, memory=memory)
    asyncio.run(bot.handle_message(update, context))
    assert memory.get_outbound(42) is None


def test_handle_message_snapshots_the_shown_list_for_next_message(monkeypatch):
    # After showing the list, the exact order shown is remembered, so the next message's
    # numbers resolve against what the user is looking at, not a re-derived live order.
    conn = fresh()
    memory = ConversationMemory()
    t1 = db.add_task(conn, "first", "normal", None, datetime(2026, 7, 1, tzinfo=TZ))
    t2 = db.add_task(conn, "second", "normal", None, datetime(2026, 7, 2, tzinfo=TZ))
    monkeypatch.setattr(
        bot.llm,
        "interpret_message",
        lambda msg, tasks, now, client, history=None, recent_outbound=None, display_ids=None: [
            Intent(action="list")
        ],
    )
    update = make_update("current tasks")
    context = make_context(conn, None, memory=memory)
    asyncio.run(bot.handle_message(update, context))
    assert db.load_display(conn, 42) == [t1.id, t2.id]


def test_handle_message_passes_display_snapshot_to_llm(monkeypatch):
    # The snapshot from the last list must reach interpret_message, so a number resolves
    # against the list the user saw rather than the current order.
    conn = fresh()
    memory = ConversationMemory()
    db.save_display(conn, 42, [7, 3, 9])
    seen = {}

    def capture(msg, tasks, now, client, history=None, recent_outbound=None, display_ids=None):
        seen["display_ids"] = display_ids
        return [Intent(action="answer", reply="ok")]

    monkeypatch.setattr(bot.llm, "interpret_message", capture)
    update = make_update("done 2")
    context = make_context(conn, None, memory=memory)
    asyncio.run(bot.handle_message(update, context))
    assert seen["display_ids"] == [7, 3, 9]


def test_recording_send_notes_outbound_then_forwards():
    # Wraps the raw send so every scheduler message (nag, focus) is remembered as
    # pending outbound and still goes out unchanged.
    memory = ConversationMemory()
    sent = []
    send = bot.make_recording_send(sent.append, memory, chat_id=42)
    send("Still the taxes. Two minutes. Go.")
    assert sent == ["Still the taxes. Two minutes. Go."]
    assert memory.get_outbound(42) == "Still the taxes. Two minutes. Go."


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
