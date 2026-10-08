import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx

from woodpecker import bot, sidecar
from woodpecker.llm import Intent
from woodpecker.memory import ConversationMemory
from woodpecker.models import PRIORITY_IMPORTANT, STATUS_PENDING, Task
from woodpecker.store import Store
from woodpecker.vikunja import VikunjaClient, VikunjaError

TZ = ZoneInfo("Europe/Paris")


class FakeVikunja:
    def __init__(self, open_tasks=None):
        self._open = {t.id: t for t in (open_tasks or [])}
        self._next_id = max(self._open, default=0) + 1

    def list_open(self):
        return list(self._open.values())

    def create_task(self, text, priority, deadline):
        # Mimics VikunjaClient.create_task: the read model stores the raw int priority,
        # not the creation-vocabulary string.
        task = Task(
            id=self._next_id,
            text=text,
            priority=4 if priority == PRIORITY_IMPORTANT else 0,
            deadline=deadline,
            created_at=datetime.now(timezone.utc),
            status=STATUS_PENDING,
            last_nagged_at=None,
            completed_at=None,
            position=0,
        )
        self._open[task.id] = task
        self._next_id += 1
        return task

    def get_task(self, task_id):
        return self._open.get(task_id)

    def mark_done(self, task_id):
        return self._open.pop(task_id, None)

    def delete_task(self, task_id):
        return self._open.pop(task_id, None) is not None


def _task(id, created_at=None):
    return Task(
        id=id,
        text=f"task {id}",
        priority=0,
        deadline=None,
        created_at=created_at or datetime(2026, 7, 1, tzinfo=TZ),
        status=STATUS_PENDING,
        last_nagged_at=None,
        completed_at=None,
        position=0,
    )


def fresh(open_tasks=None):
    conn = sidecar.connect(":memory:")
    sidecar.init_db(conn)
    return Store(FakeVikunja(open_tasks), conn)


def make_update(text, chat_id=42):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=chat_id))


def make_context(store, chat_id=42, memory=None):
    # client is unused because interpret_message is monkeypatched in the test
    return SimpleNamespace(
        bot_data={
            "store": store,
            "client": object(),
            "chat_id": chat_id,
            "memory": memory or ConversationMemory(),
        }
    )


def test_handle_message_adds_task_and_replies(monkeypatch):
    store = fresh()
    monkeypatch.setattr(
        bot.llm,
        "interpret_message",
        lambda msg, tasks, now, client, history=None, recent_outbound=None, display_ids=None: [
            Intent(action="add", text="call vet")
        ],
    )
    update = make_update("remind me to call vet")
    context = make_context(store)
    asyncio.run(bot.handle_message(update, context))
    update.message.reply_text.assert_awaited_once()
    assert "call vet" in update.message.reply_text.call_args.args[0]
    assert len(store.list_pending()) == 1


def test_handle_message_saves_every_task_and_confirms_each(monkeypatch):
    store = fresh()
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
    context = make_context(store)
    asyncio.run(bot.handle_message(update, context))
    assert len(store.list_pending()) == 3
    reply = update.message.reply_text.call_args.args[0]
    assert "cancel gym" in reply
    assert "file taxes" in reply
    assert "file expenses" in reply


def test_handle_message_passes_prior_history_to_llm(monkeypatch):
    # The follow-up fix: the previous turn must reach interpret_message so a bare
    # "Yes" can be resolved.
    store = fresh()
    memory = ConversationMemory()
    memory.add(42, "is the taxes task done?", "Not currently. Want me to mark it?")
    seen = {}

    def capture(msg, tasks, now, client, history=None, recent_outbound=None, display_ids=None):
        seen["history"] = history
        return [Intent(action="answer", reply="ok")]

    monkeypatch.setattr(bot.llm, "interpret_message", capture)
    update = make_update("Yes")
    context = make_context(store, memory=memory)
    asyncio.run(bot.handle_message(update, context))
    assert seen["history"] == [
        {"role": "user", "content": "is the taxes task done?"},
        {"role": "assistant", "content": "Not currently. Want me to mark it?"},
    ]


def test_handle_message_records_turn_in_memory(monkeypatch):
    # After replying, the exchange must be stored so the *next* message has context.
    store = fresh()
    memory = ConversationMemory()
    monkeypatch.setattr(
        bot.llm,
        "interpret_message",
        lambda msg, tasks, now, client, history=None, recent_outbound=None, display_ids=None: [
            Intent(action="answer", reply="Want me to set that up?")
        ],
    )
    update = make_update("is the taxes task done?")
    context = make_context(store, memory=memory)
    asyncio.run(bot.handle_message(update, context))
    assert memory.get(42) == [
        {"role": "user", "content": "is the taxes task done?"},
        {"role": "assistant", "content": "Want me to set that up?"},
    ]


def test_handle_message_passes_pending_outbound_to_llm(monkeypatch):
    # A reply to a nag must carry the nag into interpret_message as context.
    store = fresh()
    memory = ConversationMemory()
    memory.note_outbound(42, "Still the taxes. Two minutes. Go.")
    seen = {}

    def capture(msg, tasks, now, client, history=None, recent_outbound=None, display_ids=None):
        seen["outbound"] = recent_outbound
        return [Intent(action="answer", reply="ok")]

    monkeypatch.setattr(bot.llm, "interpret_message", capture)
    update = make_update("done")
    context = make_context(store, memory=memory)
    asyncio.run(bot.handle_message(update, context))
    assert seen["outbound"] == "Still the taxes. Two minutes. Go."


def test_handle_message_clears_outbound_after_reply(monkeypatch):
    # Once the user has engaged, the pending nag is spent and must not haunt the
    # next unrelated message.
    store = fresh()
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
    context = make_context(store, memory=memory)
    asyncio.run(bot.handle_message(update, context))
    assert memory.get_outbound(42) is None


def test_handle_message_snapshots_the_shown_list_for_next_message(monkeypatch):
    # After showing the list, the exact order shown is remembered, so the next message's
    # numbers resolve against what the user is looking at, not a re-derived live order.
    t1 = _task(1, datetime(2026, 7, 1, tzinfo=TZ))
    t2 = _task(2, datetime(2026, 7, 2, tzinfo=TZ))
    store = fresh([t1, t2])
    memory = ConversationMemory()
    monkeypatch.setattr(
        bot.llm,
        "interpret_message",
        lambda msg, tasks, now, client, history=None, recent_outbound=None, display_ids=None: [
            Intent(action="list")
        ],
    )
    update = make_update("current tasks")
    context = make_context(store, memory=memory)
    asyncio.run(bot.handle_message(update, context))
    assert store.load_display(42) == [t1.id, t2.id]


class RaisingStore:
    """A store whose list_pending() always fails, standing in for a Vikunja outage."""

    def list_pending(self):
        raise VikunjaError("vikunja unreachable")

    def get_open_prompt(self):
        # The letter parser reads the open prompt from the sidecar first; no prompt
        # is open, so the message falls through to the free-text flow.
        return None


def test_handle_message_replies_friendly_message_when_vikunja_unreachable(monkeypatch):
    # During an outage every command must fail soft: log it, tell the user, and
    # return, instead of propagating to the generic "something glitched" handler
    # and losing the message.
    called = False

    def spy(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(bot.llm, "interpret_message", spy)
    update = make_update("remind me to call vet")
    context = make_context(RaisingStore())
    asyncio.run(bot.handle_message(update, context))  # must not raise
    assert called is False
    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args.args[0]
    assert "unreachable" in reply.lower()


def test_handle_message_replies_friendly_message_on_real_connection_outage(monkeypatch):
    # The finding this guards against: a genuinely unreachable Vikunja (container down,
    # connection refused) raises an httpx transport error, not an HTTP-status error.
    # Wire the real VikunjaClient (through a MockTransport that raises on every call)
    # into a real Store, so this test proves the friendly "unreachable" reply is
    # reached via the actual client path, not just via a hand-rolled VikunjaError.
    def handler(request):
        raise httpx.ConnectError("Connection refused")

    client = VikunjaClient("http://vk", "tok", project_id=3)
    client._http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://vk")
    conn = sidecar.connect(":memory:")
    sidecar.init_db(conn)
    store = Store(client, conn)

    called = False

    def spy(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(bot.llm, "interpret_message", spy)
    update = make_update("remind me to call vet")
    context = make_context(store)
    asyncio.run(bot.handle_message(update, context))  # must not raise
    assert called is False
    update.message.reply_text.assert_awaited_once()
    reply = update.message.reply_text.call_args.args[0]
    assert "unreachable" in reply.lower()


def test_handle_message_snapshot_reflects_prior_mutation_in_same_batch(monkeypatch):
    # A single message that both adds a task and asks for the list must save a
    # snapshot that includes the just-added task, proving the list used for the
    # snapshot is fetched after the mutation rather than reusing a stale,
    # pre-interpretation copy.
    store = fresh()
    memory = ConversationMemory()
    monkeypatch.setattr(
        bot.llm,
        "interpret_message",
        lambda msg, tasks, now, client, history=None, recent_outbound=None, display_ids=None: [
            Intent(action="add", text="call vet"),
            Intent(action="list"),
        ],
    )
    update = make_update("add call vet and show me the list")
    context = make_context(store, memory=memory)
    asyncio.run(bot.handle_message(update, context))
    saved = store.load_display(42)
    pending = store.list_pending()
    assert saved == [t.id for t in pending]
    assert len(saved) == 1


def test_handle_message_passes_display_snapshot_to_llm(monkeypatch):
    # The snapshot from the last list must reach interpret_message, so a number resolves
    # against the list the user saw rather than the current order.
    store = fresh()
    memory = ConversationMemory()
    store.save_display(42, [7, 3, 9])
    seen = {}

    def capture(msg, tasks, now, client, history=None, recent_outbound=None, display_ids=None):
        seen["display_ids"] = display_ids
        return [Intent(action="answer", reply="ok")]

    monkeypatch.setattr(bot.llm, "interpret_message", capture)
    update = make_update("done 2")
    context = make_context(store, memory=memory)
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
    store = fresh()
    called = False

    def spy(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(bot.llm, "interpret_message", spy)
    update = make_update("hello", chat_id=999)
    context = make_context(store, chat_id=42)
    asyncio.run(bot.handle_message(update, context))
    assert called is False
    update.message.reply_text.assert_not_awaited()


def test_handle_start_replies_with_welcome():
    update = make_update("/start")
    context = make_context(fresh())
    asyncio.run(bot.handle_start(update, context))
    update.message.reply_text.assert_awaited_once()
    assert "Woodpecker" in update.message.reply_text.call_args.args[0]


def test_welcome_describes_the_morning_rhythm():
    assert "louder" not in bot.WELCOME
    assert "Each morning I name one thing and a first step" in bot.WELCOME
    assert "answer with one letter" in bot.WELCOME
    assert "\u2014" not in bot.WELCOME


def test_handle_start_ignores_foreign_chat():
    update = make_update("/start", chat_id=999)
    context = make_context(fresh(), chat_id=42)
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


def test_a_letter_reply_skips_claude(monkeypatch):
    store = fresh([_task(1)])
    store.record_frog(datetime(2026, 10, 8, tzinfo=TZ).date(), 1)
    store.set_open_prompt("frog", [1], datetime(2026, 10, 8, 9, tzinfo=TZ))

    def boom(*args, **kwargs):
        raise AssertionError("Claude must not be called for a letter reply")

    monkeypatch.setattr(bot.llm, "interpret_message", boom)
    monkeypatch.setattr(bot.config, "now_paris", lambda: datetime(2026, 10, 8, 9, 5, tzinfo=TZ))
    update = make_update("d")
    asyncio.run(bot.handle_message(update, make_context(store)))
    assert update.message.reply_text.call_args.args[0] == "Done, nice."


def test_a_letter_with_no_prompt_is_refused(monkeypatch):
    store = fresh([_task(1)])
    monkeypatch.setattr(bot.llm, "interpret_message", lambda *a, **k: [])
    update = make_update("x")
    asyncio.run(bot.handle_message(update, make_context(store)))
    assert update.message.reply_text.call_args.args[0] == "Nothing to answer right now."
