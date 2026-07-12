from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from jolt import selection
from jolt.models import (
    PRIORITY_IMPORTANT, PRIORITY_NORMAL, STATUS_DONE, STATUS_PENDING, Task,
)

TZ = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 7, 12, 8, tzinfo=TZ)


def make(id, *, priority=PRIORITY_NORMAL, deadline=None, created=NOW, status=STATUS_PENDING):
    return Task(id=id, text=f"t{id}", priority=priority, deadline=deadline,
                created_at=created, status=status, last_nagged_at=None, completed_at=None)


def test_important_sorts_before_normal():
    normal = make(1, priority=PRIORITY_NORMAL)
    important = make(2, priority=PRIORITY_IMPORTANT)
    assert [t.id for t in selection.order_backlog([normal, important])] == [2, 1]


def test_earlier_deadline_wins_within_same_priority():
    late = make(1, deadline=date(2026, 8, 1))
    soon = make(2, deadline=date(2026, 7, 14))
    assert [t.id for t in selection.order_backlog([late, soon])] == [2, 1]


def test_older_wins_when_no_deadline():
    newer = make(1, created=datetime(2026, 7, 12, tzinfo=TZ))
    older = make(2, created=datetime(2026, 7, 1, tzinfo=TZ))
    assert [t.id for t in selection.order_backlog([newer, older])] == [2, 1]


def test_order_backlog_excludes_non_pending():
    a = make(1)
    b = make(2, status=STATUS_DONE)
    assert [t.id for t in selection.order_backlog([a, b])] == [1]


def test_is_stale_boundary_exactly_three_days():
    created = NOW - timedelta(days=3)
    assert selection.is_stale(make(1, created=created), NOW) is True
    almost = NOW - timedelta(days=3) + timedelta(minutes=1)
    assert selection.is_stale(make(2, created=almost), NOW) is False


def test_is_stale_false_for_done():
    created = NOW - timedelta(days=10)
    assert selection.is_stale(make(1, created=created, status=STATUS_DONE), NOW) is False


def test_select_daily_focus_picks_top_and_two_rescues():
    focus = make(1, priority=PRIORITY_IMPORTANT, created=NOW)
    old_a = make(2, created=NOW - timedelta(days=5))
    old_b = make(3, created=NOW - timedelta(days=4))
    old_c = make(4, created=NOW - timedelta(days=6))
    fresh = make(5, created=NOW)
    result = selection.select_daily_focus([focus, old_a, old_b, old_c, fresh], NOW)
    assert result.focus.id == 1
    assert len(result.rescues) == 2
    assert 1 not in [t.id for t in result.rescues]  # focus never doubled as a rescue


def test_stale_important_leads_and_is_not_also_a_rescue():
    # A fresh important task with the nearest deadline would win the ordinary priority
    # order, but an avoided (stale) important task must take the lead instead.
    fresh_imp = make(1, priority=PRIORITY_IMPORTANT, deadline=date(2026, 7, 13), created=NOW)
    stale_imp = make(2, priority=PRIORITY_IMPORTANT, created=NOW - timedelta(days=5))
    stale_normal = make(3, priority=PRIORITY_NORMAL, created=NOW - timedelta(days=6))
    result = selection.select_daily_focus([fresh_imp, stale_imp, stale_normal], NOW)
    assert result.focus.id == 2
    assert 2 not in [t.id for t in result.rescues]


def test_lead_falls_back_to_top_priority_when_no_important_is_stale():
    fresh_imp = make(1, priority=PRIORITY_IMPORTANT, created=NOW)
    stale_normal = make(2, priority=PRIORITY_NORMAL, created=NOW - timedelta(days=5))
    result = selection.select_daily_focus([fresh_imp, stale_normal], NOW)
    assert result.focus.id == 1


def test_select_daily_focus_empty():
    result = selection.select_daily_focus([], NOW)
    assert result.focus is None
    assert result.rescues == []


def test_quiet_hours():
    assert selection.is_quiet_hours(datetime(2026, 7, 12, 5, tzinfo=TZ)) is True
    assert selection.is_quiet_hours(datetime(2026, 7, 12, 6, tzinfo=TZ)) is False
    assert selection.is_quiet_hours(datetime(2026, 7, 12, 22, tzinfo=TZ)) is False
    assert selection.is_quiet_hours(datetime(2026, 7, 12, 23, tzinfo=TZ)) is True
