from datetime import date, datetime
from zoneinfo import ZoneInfo

from jolt import config
from jolt.models import Task, DailyFocus, PRIORITY_IMPORTANT, STATUS_PENDING


def test_constants_present():
    assert config.STALE_THRESHOLD_DAYS == 3
    assert config.QUIET_START_HOUR == 6
    assert config.QUIET_END_HOUR == 23
    assert config.NAG_HOURS == (9, 13, 19)


def test_now_paris_is_timezone_aware():
    assert config.now_paris().tzinfo is not None


def test_task_and_focus_construct():
    t = Task(
        id=1, text="call vet", priority=PRIORITY_IMPORTANT, deadline=date(2026, 7, 15),
        created_at=datetime(2026, 7, 12, tzinfo=ZoneInfo("Europe/Paris")),
        status=STATUS_PENDING, last_nagged_at=None, completed_at=None,
    )
    focus = DailyFocus(focus=t, rescues=[])
    assert focus.focus.text == "call vet"
    assert focus.rescues == []
