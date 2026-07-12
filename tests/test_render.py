from datetime import date, datetime
from zoneinfo import ZoneInfo

from jolt import render
from jolt.models import PRIORITY_IMPORTANT, PRIORITY_NORMAL, STATUS_PENDING, Task

TZ = ZoneInfo("Europe/Paris")


def make(id, *, text, priority=PRIORITY_NORMAL, deadline=None):
    return Task(id=id, text=text, priority=priority, deadline=deadline,
                created_at=datetime(2026, 7, 1, tzinfo=TZ), status=STATUS_PENDING,
                last_nagged_at=None, completed_at=None)


def test_empty_backlog_message():
    assert render.render_backlog([]) == "Backlog empty. Nice."


def test_render_orders_and_marks_important_and_deadline():
    tasks = [
        make(1, text="tidy desk"),
        make(2, text="call vet", priority=PRIORITY_IMPORTANT, deadline=date(2026, 7, 15)),
    ]
    out = render.render_backlog(tasks)
    lines = out.splitlines()
    assert lines[0] == "Backlog:"
    # important task sorts first
    assert lines[1] == "1. ‼️ call vet (due 2026-07-15)"
    assert lines[2] == "2. • tidy desk"
