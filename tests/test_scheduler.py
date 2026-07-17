import inspect
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from jolt import config, main, scheduler, sidecar
from jolt.models import STATUS_PENDING, Task
from jolt.store import Store

TZ = ZoneInfo("Europe/Paris")


class FakeVikunja:
    def __init__(self, open_tasks=None):
        self._open = {t.id: t for t in (open_tasks or [])}
        self._next_id = max(self._open, default=0) + 1

    def list_open(self):
        return list(self._open.values())

    def create_task(self, text, priority, deadline):
        task = Task(
            id=self._next_id,
            text=text,
            priority=priority,
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


def _task(id, priority=0, deadline=None, created_at=None):
    return Task(
        id=id,
        text=f"task {id}",
        priority=priority,
        deadline=deadline,
        created_at=created_at or datetime(2026, 7, 10, tzinfo=TZ),
        status=STATUS_PENDING,
        last_nagged_at=None,
        completed_at=None,
        position=0,
    )


def fresh(open_tasks=None):
    conn = sidecar.connect(":memory:")
    sidecar.init_db(conn)
    return Store(FakeVikunja(open_tasks), conn)


class FakeClient:
    """Records prose calls; returns canned text so no network is hit."""

    def __init__(self):
        self.messages = self
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        from types import SimpleNamespace

        return SimpleNamespace(content=[SimpleNamespace(type="text", text="canned prose")])


class RaisingClient:
    """A client whose create() always raises, to exercise the failure path."""

    def __init__(self):
        self.messages = self

    def create(self, **kwargs):
        raise RuntimeError("boom")


def collector():
    sent = []
    return sent, lambda msg: sent.append(msg)


def test_daily_focus_sends_prose_then_backlog():
    now = datetime(2026, 7, 12, 6, tzinfo=TZ)
    store = fresh([_task(1, 4, created_at=now)])
    sent, send = collector()
    scheduler.send_daily_focus(store, send, FakeClient(), now, chat_id=42)
    assert len(sent) == 1
    assert "canned prose" in sent[0]
    assert "📋 " in sent[0]
    assert "task 1" in sent[0]


def test_daily_focus_snapshots_the_order_it_shows():
    # The 06:00 focus prints a numbered backlog but used to never record that order, so a
    # number typed after it resolved against a re-derived live list (the "28 done" bug).
    # It must snapshot the exact order shown, keyed by chat, so the next number lines up
    # with what the user is looking at. Here 'a' is important, 'b' normal, so a ranks first.
    now = datetime(2026, 7, 12, 6, tzinfo=TZ)
    a = _task(1, 4, created_at=now)
    b = _task(2, 0, created_at=now)
    store = fresh([a, b])
    sent, send = collector()
    scheduler.send_daily_focus(store, send, FakeClient(), now, chat_id=42)
    assert store.load_display(42) == [a.id, b.id]


def test_nags_skipped_during_quiet_hours():
    now = datetime(2026, 7, 12, 5, tzinfo=TZ)  # before 06:00
    store = fresh([_task(1, created_at=now)])
    sent, send = collector()
    scheduler.send_nags(store, send, FakeClient(), now)
    assert sent == []


def test_nags_skipped_when_no_pending_task():
    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    store = fresh()
    sent, send = collector()
    scheduler.send_nags(store, send, FakeClient(), now)
    assert sent == []


def test_scheduled_jobs_run_on_the_event_loop_not_a_worker_thread():
    """Regression for the silent 6am brief: BackgroundScheduler ran the jobs on a
    worker thread, where the bot's SQLite connection is unusable ("SQLite objects
    created in a thread can only be used in that same thread") and there is no event
    loop for the Telegram send. The scheduler must be an AsyncIOScheduler and every
    job a coroutine, so APScheduler runs them on the bot's own event-loop thread."""
    store = fresh()
    sent, send = collector()
    sched = main.build_scheduler(store, send, FakeClient(), chat_id=42)
    assert isinstance(sched, AsyncIOScheduler)
    jobs = sched.get_jobs()
    assert len(jobs) == 1 + len(config.NAG_HOURS)
    for job in jobs:
        assert inspect.iscoroutinefunction(job.func), f"{job.func} must be a coroutine"


def test_scheduled_jobs_fire_in_the_configured_timezone(monkeypatch):
    """Regression: APScheduler's CronTrigger defaults to the machine's local zone, and
    add_job does not stamp the scheduler's timezone onto a trigger that already carries
    one. In the container (local zone UTC) every job fired 2 hours late: the 06:00 focus
    arrived at 08:00 Paris, the 19:00 nag at 21:00. We force the local zone to UTC here so
    the test reproduces the container regardless of the developer's machine zone; each
    trigger must still carry the configured Europe/Paris zone."""
    import apscheduler.triggers.cron as cron_module

    monkeypatch.setattr(cron_module, "get_localzone", lambda: ZoneInfo("Etc/UTC"))
    store = fresh()
    sent, send = collector()
    sched = main.build_scheduler(store, send, FakeClient(), chat_id=42)
    for job in sched.get_jobs():
        assert str(job.trigger.timezone) == config.TIMEZONE, (
            f"{job.func} trigger tz is {job.trigger.timezone}, expected {config.TIMEZONE}"
        )


def test_nag_sends_and_marks_nagged():
    created = datetime(2026, 7, 8, tzinfo=TZ)
    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    store = fresh([_task(1, created_at=created)])
    sent, send = collector()
    scheduler.send_nags(store, send, FakeClient(), now)
    assert len(sent) == 1
    assert store.get_task(1).last_nagged_at == now


def test_slow_resurface_nags_a_normal_stale_nonfocus_task():
    # An important stale task is the focus (frog). A separate normal stale task should
    # also get a slow-resurface poke, so two messages go out and the tadpole is marked.
    created = datetime(2026, 7, 5, tzinfo=TZ)  # 7 days before `now`
    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    store = fresh(
        [
            _task(1, 4, created_at=created),  # becomes focus
            _task(2, 0, created_at=created),  # tadpole
        ]
    )
    sent, send = collector()
    scheduler.send_nags(store, send, FakeClient(), now)
    assert len(sent) == 2
    assert store.get_task(2).last_nagged_at == now


def test_slow_resurface_gated_by_cadence():
    # The tadpole was poked yesterday, well within SLOW_RESURFACE_DAYS, so only the
    # frog nag goes out this run.
    created = datetime(2026, 7, 1, tzinfo=TZ)
    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    store = fresh(
        [
            _task(1, 4, created_at=created),
            _task(2, 0, created_at=created),
        ]
    )
    store.mark_nagged(2, datetime(2026, 7, 11, 13, tzinfo=TZ))  # poked yesterday
    sent, send = collector()
    scheduler.send_nags(store, send, FakeClient(), now)
    assert len(sent) == 1


def test_daily_focus_sends_a_fallback_when_the_llm_fails():
    now = datetime(2026, 7, 12, 6, tzinfo=TZ)
    store = fresh([_task(1, 4, created_at=now)])
    sent, send = collector()
    scheduler.send_daily_focus(store, send, RaisingClient(), now, chat_id=42)
    assert len(sent) == 1  # the user hears about it rather than silence
    assert sent[0]  # non-empty fallback text


class RaisingStore:
    """A store whose list_pending() always raises, to simulate a Vikunja outage."""

    def list_pending(self):
        raise RuntimeError("vikunja is down")


def test_daily_focus_does_not_propagate_when_vikunja_is_down():
    now = datetime(2026, 7, 12, 6, tzinfo=TZ)
    sent, send = collector()
    # Must not raise: an infra hiccup at 06:00 should be logged, not crash the job.
    scheduler.send_daily_focus(RaisingStore(), send, FakeClient(), now, chat_id=42)
    assert sent == []  # no spam for an infra hiccup; logging + healthchecks covers it


def test_nags_sends_fallback_and_does_not_propagate_when_vikunja_is_down():
    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    sent, send = collector()
    # Must not raise: the outage is caught before any selection logic runs.
    scheduler.send_nags(RaisingStore(), send, FakeClient(), now)
    assert len(sent) == 1
    assert (
        sent[0] == "I tried to nudge you but something on my end broke. I'll try again next time."
    )


def test_nags_attempt_the_tadpole_even_if_the_frog_nag_fails():
    # An important stale frog and a normal stale tadpole. The frog nag raises; the tadpole
    # nag must still be attempted, and the user gets exactly one failure note.
    created = datetime(2026, 7, 5, tzinfo=TZ)  # 7 days before now
    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    store = fresh(
        [
            _task(1, 4, created_at=created),
            _task(2, 0, created_at=created),
        ]
    )
    sent, send = collector()
    scheduler.send_nags(store, send, RaisingClient(), now)
    # Both nags raise, so no real nags go out, but the user is told once, not zero times.
    assert len(sent) == 1
