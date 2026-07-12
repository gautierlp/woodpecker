from datetime import date, datetime
from zoneinfo import ZoneInfo

from jolt import render
from jolt.models import PRIORITY_IMPORTANT, PRIORITY_NORMAL, STATUS_PENDING, Task

TZ = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 7, 4, 12, tzinfo=TZ)
FRESH = datetime(2026, 7, 3, tzinfo=TZ)  # 1 day old at NOW -> not stale
OLD = datetime(2026, 6, 20, tzinfo=TZ)  # ~2 weeks old at NOW -> stale


def make(id, *, text, priority=PRIORITY_NORMAL, deadline=None, created_at=FRESH):
    return Task(
        id=id,
        text=text,
        priority=priority,
        deadline=deadline,
        created_at=created_at,
        status=STATUS_PENDING,
        last_nagged_at=None,
        completed_at=None,
    )


def dot_of(task):
    line = render.render_backlog([task], NOW).splitlines()[1]
    return line.split(" ", 2)[1]


def test_empty_backlog_message():
    assert render.render_backlog([], NOW) == "Backlog empty. Nice."


def test_important_overdue_is_red():
    assert dot_of(make(1, text="x", priority=PRIORITY_IMPORTANT, deadline=date(2026, 7, 1))) == "🔴"


def test_important_due_soon_is_red():
    assert dot_of(make(1, text="x", priority=PRIORITY_IMPORTANT, deadline=date(2026, 7, 6))) == "🔴"


def test_important_stale_no_deadline_is_red():
    assert dot_of(make(1, text="x", priority=PRIORITY_IMPORTANT, created_at=OLD)) == "🔴"


def test_important_not_urgent_is_orange():
    assert (
        dot_of(make(1, text="x", priority=PRIORITY_IMPORTANT, deadline=date(2026, 12, 1))) == "🟠"
    )


def test_normal_stale_is_yellow():
    assert dot_of(make(1, text="x", created_at=OLD)) == "🟡"


def test_normal_fresh_is_white():
    assert dot_of(make(1, text="x")) == "⚪"


def test_render_orders_and_formats_line():
    tasks = [
        make(1, text="tidy desk"),
        make(2, text="call vet", priority=PRIORITY_IMPORTANT, deadline=date(2026, 12, 1)),
    ]
    out = render.render_backlog(tasks, NOW)
    lines = out.splitlines()
    assert lines[0] == "Backlog:"
    # important task sorts first
    assert lines[1] == "1. 🟠 call vet (due 2026-12-01)"
    assert lines[2] == "2. ⚪ tidy desk"
