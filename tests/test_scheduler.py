import inspect
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from woodpecker import config, main, render, replies, scheduler, sidecar
from woodpecker.models import STATUS_PENDING, Task
from woodpecker.store import Store

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

    def update_task(self, task_id, deadline=None, priority=None, text=None):
        task = self._open.get(task_id)
        if task is None:
            return None
        changes = {}
        if deadline is not None:
            changes["deadline"] = deadline
        if text is not None:
            changes["text"] = text
        task = replace(task, **changes)
        self._open[task_id] = task
        return task


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


def test_scheduled_jobs_run_on_the_event_loop_not_a_worker_thread():
    """Regression for the silent 6am brief: BackgroundScheduler ran the jobs on a
    worker thread, where the bot's SQLite connection is unusable ("SQLite objects
    created in a thread can only be used in that same thread") and there is no event
    loop for the Telegram send. The scheduler must be an AsyncIOScheduler and every
    job a coroutine, so APScheduler runs them on the bot's own event-loop thread."""
    store = fresh()
    sent, send = collector()
    sched = main.build_scheduler(store, send, FakeClient(), vault_path="/nope")
    assert isinstance(sched, AsyncIOScheduler)
    jobs = sched.get_jobs()
    assert len(jobs) == 2
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
    sched = main.build_scheduler(store, send, FakeClient(), vault_path="/nope")
    for job in sched.get_jobs():
        assert str(job.trigger.timezone) == config.TIMEZONE, (
            f"{job.func} trigger tz is {job.trigger.timezone}, expected {config.TIMEZONE}"
        )


MORNING = datetime(2026, 10, 8, 9, tzinfo=TZ)
CHECKIN = datetime(2026, 10, 8, 14, tzinfo=TZ)


def _vault(tmp_path, body):
    (tmp_path / "Taxes.md").write_text(body, encoding="utf-8")
    return str(tmp_path)


def test_morning_names_a_frog_with_the_legend_then_the_vault(tmp_path):
    store = fresh([_task(1)])
    sent, send = collector()
    vault_path = _vault(tmp_path, "- [ ] file the return 📅 2026-10-10\n")
    scheduler.send_morning(store, send, FakeClient(), MORNING, vault_path)
    [text] = sent
    assert text.startswith("canned prose\n\n" + render.FROG_LEGEND)
    assert text.endswith("• file the return (Oct 10, Taxes)\nTick these in Obsidian.")
    assert store.frog_of_day(MORNING.date()).task_id == 1
    assert store.get_open_prompt().kind == replies.FROG


def test_morning_logs_the_frog(tmp_path, caplog):
    store = fresh([_task(1)])
    sent, send = collector()
    with caplog.at_level("INFO", logger="woodpecker.scheduler"):
        scheduler.send_morning(store, send, FakeClient(), MORNING, str(tmp_path))
    assert "frog 1" in caplog.text


def test_morning_with_an_unreadable_vault_still_sends_the_frog(tmp_path):
    store = fresh([_task(1)])
    sent, send = collector()
    scheduler.send_morning(store, send, FakeClient(), MORNING, str(tmp_path / "missing"))
    [text] = sent
    assert "canned prose" in text
    assert text.endswith("Vault: not readable (not a folder)")


def test_morning_with_a_claude_error_uses_the_plain_line(tmp_path):
    store = fresh([_task(1)])
    sent, send = collector()
    scheduler.send_morning(store, send, RaisingClient(), MORNING, str(tmp_path))
    assert sent[0].startswith('Today: "task 1".')


def test_morning_with_an_empty_backlog(tmp_path):
    store = fresh([])
    sent, send = collector()
    scheduler.send_morning(store, send, FakeClient(), MORNING, str(tmp_path))
    assert sent == ["Backlog empty. Nothing to chase today."]
    assert store.get_open_prompt() is None


def test_the_third_t_turns_the_next_morning_into_the_reframe(tmp_path):
    store = fresh([_task(1)])
    for offset in (3, 2, 1):
        day = MORNING.date() - timedelta(days=offset)
        store.record_frog(day, 1)
        store.mark_frog_answered(day, "t")
    sent, send = collector()
    scheduler.send_morning(store, send, FakeClient(), MORNING, str(tmp_path))
    assert render.REFRAME_LEGEND in sent[0]
    assert store.get_open_prompt().kind == replies.REFRAME


def test_checkin_asks_when_the_frog_has_no_answer(tmp_path):
    store = fresh([_task(1)])
    store.record_frog(CHECKIN.date(), 1)
    sent, send = collector()
    scheduler.send_checkin(store, send, CHECKIN, str(tmp_path))
    assert sent == ['Still on for "task 1" today?\n' + render.FROG_LEGEND]


def test_checkin_after_o_asks_how_it_goes(tmp_path):
    store = fresh([_task(1)])
    store.record_frog(CHECKIN.date(), 1)
    store.mark_frog_started(CHECKIN.date(), MORNING)
    sent, send = collector()
    scheduler.send_checkin(store, send, CHECKIN, str(tmp_path))
    assert sent[0].startswith('How is "task 1" going?')


def test_checkin_is_skipped_after_an_answer(tmp_path):
    for letter in ("d", "t", "x", "n"):
        store = fresh([_task(1)])
        store.record_frog(CHECKIN.date(), 1)
        store.mark_frog_answered(CHECKIN.date(), letter)
        sent, send = collector()
        scheduler.send_checkin(store, send, CHECKIN, str(tmp_path))
        assert sent == [], letter


def test_checkin_carries_vault_tasks_due_today(tmp_path):
    store = fresh([_task(1)])
    store.record_frog(CHECKIN.date(), 1)
    store.mark_frog_answered(CHECKIN.date(), "d")
    sent, send = collector()
    vault_path = _vault(tmp_path, "- [ ] pay rent 📅 2026-10-08\n- [ ] later 📅 2026-10-09\n")
    scheduler.send_checkin(store, send, CHECKIN, vault_path)
    assert sent == ["Due in the vault today:\n• pay rent (Oct 08, Taxes)\nTick these in Obsidian."]


def test_quiet_hours_send_nothing(tmp_path):
    late = datetime(2026, 10, 8, 23, 30, tzinfo=TZ)
    store = fresh([_task(1, created_at=late - timedelta(days=30))])
    store.record_frog(late.date(), 1)
    sent, send = collector()
    scheduler.send_morning(store, send, FakeClient(), late, str(tmp_path))
    scheduler.send_checkin(store, send, late, str(tmp_path))
    assert sent == []


def test_cron_times():
    store = fresh()
    sent, send = collector()
    sched = main.build_scheduler(store, send, FakeClient(), vault_path="/nope")
    fields = sorted(
        (str(job.trigger.fields[4]), str(job.trigger.fields[5])) for job in sched.get_jobs()
    )  # (day_of_week, hour)
    assert fields == [("*", "14"), ("*", "9")]


def test_checkin_with_an_unreadable_vault_sends_nothing(tmp_path, caplog):
    store = fresh([_task(1)])
    store.record_frog(CHECKIN.date(), 1)
    store.mark_frog_answered(CHECKIN.date(), "d")
    sent, send = collector()
    with caplog.at_level("WARNING", logger="woodpecker.scheduler"):
        scheduler.send_checkin(store, send, CHECKIN, str(tmp_path / "missing"))
    assert sent == []
    assert "Vault unreadable" in caplog.text


def _bumped_three_times(store, task_id):
    for offset in (3, 2, 1):
        day = MORNING.date() - timedelta(days=offset)
        store.record_frog(day, task_id)
        store.mark_frog_answered(day, "t")


def test_the_reframe_task_beats_overdue_tasks(tmp_path):
    overdue = MORNING.date() - timedelta(days=2)
    store = fresh(
        [
            _task(1, deadline=overdue),
            _task(2, deadline=overdue),
            _task(3, deadline=MORNING.date(), created_at=MORNING - timedelta(days=5)),
        ]
    )
    _bumped_three_times(store, 3)
    sent, send = collector()
    scheduler.send_morning(store, send, FakeClient(), MORNING, str(tmp_path))
    assert store.frog_of_day(MORNING.date()).task_id == 3
    assert store.get_open_prompt().kind == replies.REFRAME


def test_the_oldest_reframe_task_comes_first(tmp_path):
    store = fresh(
        [
            _task(1, created_at=MORNING - timedelta(days=5)),
            _task(2, created_at=MORNING - timedelta(days=9)),
        ]
    )
    _bumped_three_times(store, 1)
    for offset in (6, 5, 4):
        day = MORNING.date() - timedelta(days=offset)
        store.record_frog(day, 2)
        store.mark_frog_answered(day, "t")
    sent, send = collector()
    scheduler.send_morning(store, send, FakeClient(), MORNING, str(tmp_path))
    assert store.frog_of_day(MORNING.date()).task_id == 2


def test_an_empty_backlog_logs_no_frog(tmp_path, caplog):
    store = fresh([])
    sent, send = collector()
    with caplog.at_level("INFO", logger="woodpecker.scheduler"):
        scheduler.send_morning(store, send, FakeClient(), MORNING, str(tmp_path))
    assert "frog" not in caplog.text


class DownVikunja(FakeVikunja):
    def list_open(self):
        raise RuntimeError("vikunja down")


def test_morning_with_vikunja_down_closes_yesterdays_prompt(tmp_path):
    conn = sidecar.connect(":memory:")
    sidecar.init_db(conn)
    store = Store(DownVikunja([_task(1)]), conn)
    store.set_open_prompt(replies.FROG, [1], MORNING - timedelta(days=1))
    sent, send = collector()
    scheduler.send_morning(store, send, FakeClient(), MORNING, str(tmp_path))
    assert sent[0].startswith("My task list is unreachable this morning.")
    assert store.get_open_prompt() is None
