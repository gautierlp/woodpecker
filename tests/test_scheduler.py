import inspect
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from jolt import config, db, main, scheduler

TZ = ZoneInfo("Europe/Paris")


def fresh():
    conn = db.connect(":memory:")
    db.init_db(conn)
    return conn


class FakeClient:
    """Records prose calls; returns canned text so no network is hit."""

    def __init__(self):
        self.messages = self
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        from types import SimpleNamespace

        return SimpleNamespace(content=[SimpleNamespace(type="text", text="canned prose")])


def collector():
    sent = []
    return sent, lambda msg: sent.append(msg)


def test_daily_focus_sends_prose_then_backlog():
    conn = fresh()
    now = datetime(2026, 7, 12, 6, tzinfo=TZ)
    db.add_task(conn, "call vet", "important", None, now)
    sent, send = collector()
    scheduler.send_daily_focus(conn, send, FakeClient(), now, chat_id=42)
    assert len(sent) == 1
    assert "canned prose" in sent[0]
    assert "📋 " in sent[0]
    assert "call vet" in sent[0]


def test_daily_focus_snapshots_the_order_it_shows():
    # The 06:00 focus prints a numbered backlog but used to never record that order, so a
    # number typed after it resolved against a re-derived live list (the "28 done" bug).
    # It must snapshot the exact order shown, keyed by chat, so the next number lines up
    # with what the user is looking at. Here 'a' is important, 'b' normal, so a ranks first.
    conn = fresh()
    now = datetime(2026, 7, 12, 6, tzinfo=TZ)
    a = db.add_task(conn, "a", "important", None, now)
    b = db.add_task(conn, "b", "normal", None, now)
    sent, send = collector()
    scheduler.send_daily_focus(conn, send, FakeClient(), now, chat_id=42)
    assert db.load_display(conn, 42) == [a.id, b.id]


def test_nags_skipped_during_quiet_hours():
    conn = fresh()
    now = datetime(2026, 7, 12, 5, tzinfo=TZ)  # before 06:00
    db.add_task(conn, "taxes", "normal", None, now)
    sent, send = collector()
    scheduler.send_nags(conn, send, FakeClient(), now)
    assert sent == []


def test_nags_skipped_when_no_pending_task():
    conn = fresh()
    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    sent, send = collector()
    scheduler.send_nags(conn, send, FakeClient(), now)
    assert sent == []


def test_scheduled_jobs_run_on_the_event_loop_not_a_worker_thread():
    """Regression for the silent 6am brief: BackgroundScheduler ran the jobs on a
    worker thread, where the bot's SQLite connection is unusable ("SQLite objects
    created in a thread can only be used in that same thread") and there is no event
    loop for the Telegram send. The scheduler must be an AsyncIOScheduler and every
    job a coroutine, so APScheduler runs them on the bot's own event-loop thread."""
    conn = fresh()
    sent, send = collector()
    sched = main.build_scheduler(conn, send, FakeClient(), chat_id=42)
    assert isinstance(sched, AsyncIOScheduler)
    jobs = sched.get_jobs()
    assert len(jobs) == 1 + len(config.NAG_HOURS)
    for job in jobs:
        assert inspect.iscoroutinefunction(job.func), f"{job.func} must be a coroutine"


def test_nag_sends_and_marks_nagged():
    conn = fresh()
    created = datetime(2026, 7, 8, tzinfo=TZ)
    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    t = db.add_task(conn, "taxes", "normal", None, created)
    sent, send = collector()
    scheduler.send_nags(conn, send, FakeClient(), now)
    assert len(sent) == 1
    assert db.get_task(conn, t.id).last_nagged_at == now
