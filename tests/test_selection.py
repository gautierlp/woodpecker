from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from woodpecker import selection
from woodpecker.models import (
    STATUS_DONE,
    STATUS_PENDING,
    Task,
)
from woodpecker.vault import VaultTask

TZ = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 7, 12, 8, tzinfo=TZ)


def make(
    id,
    *,
    priority=0,
    deadline=None,
    created=NOW,
    status=STATUS_PENDING,
    position=0,
    nagged=None,
):
    return Task(
        id=id,
        text=f"t{id}",
        priority=priority,
        deadline=deadline,
        created_at=created,
        status=status,
        last_nagged_at=nagged,
        completed_at=None,
        position=position,
    )


def _t(id, position, created="2026-07-10T08:00:00+00:00"):
    return Task(
        id=id,
        text=f"t{id}",
        priority=0,
        deadline=None,
        created_at=datetime.fromisoformat(created),
        status=STATUS_PENDING,
        last_nagged_at=None,
        completed_at=None,
        position=position,
    )


def test_position_breaks_ties_when_priority_and_deadline_equal():
    tasks = [_t(1, position=5), _t(2, position=1), _t(3, position=3)]
    ordered = selection.order_backlog(tasks)
    assert [t.id for t in ordered] == [2, 3, 1]


def test_priority_band_maps_raw_priority():
    assert selection.priority_band(make(1, priority=5)) == selection.BAND_HIGH
    assert selection.priority_band(make(2, priority=3)) == selection.BAND_HIGH
    assert selection.priority_band(make(3, priority=2)) == selection.BAND_MID
    assert selection.priority_band(make(4, priority=1)) == selection.BAND_MID
    assert selection.priority_band(make(5, priority=0)) == selection.BAND_LOW


def test_high_band_sorts_before_mid_before_low():
    low = make(1, priority=0)
    mid = make(2, priority=2)
    high = make(3, priority=4)
    assert [t.id for t in selection.order_backlog([low, mid, high])] == [3, 2, 1]


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


def test_quiet_hours():
    assert selection.is_quiet_hours(datetime(2026, 7, 12, 5, tzinfo=TZ)) is True
    assert selection.is_quiet_hours(datetime(2026, 7, 12, 6, tzinfo=TZ)) is False
    assert selection.is_quiet_hours(datetime(2026, 7, 12, 22, tzinfo=TZ)) is False
    assert selection.is_quiet_hours(datetime(2026, 7, 12, 23, tzinfo=TZ)) is True


def test_frog_from_priority_zero_undated_tasks():
    task = make(1, priority=0, deadline=None)
    assert selection.select_frog([task], NOW) is task


def test_frog_overdue_beats_bumped():
    overdue = make(1, deadline=NOW.date() - timedelta(days=1))
    bumped = replace(make(2), bump_count=5)
    assert selection.select_frog([bumped, overdue], NOW).id == 1


def test_frog_bumped_beats_old():
    old = make(1, created=NOW - timedelta(days=60))
    bumped = replace(make(2, created=NOW - timedelta(days=1)), bump_count=1)
    assert selection.select_frog([old, bumped], NOW).id == 2


def test_frog_oldest_wins_a_tie():
    newer = make(1, created=NOW - timedelta(days=2))
    older = make(2, created=NOW - timedelta(days=9))
    assert selection.select_frog([newer, older], NOW).id == 2


def test_frog_due_today_is_not_overdue():
    today = make(1, deadline=NOW.date(), created=NOW - timedelta(days=1))
    bumped = replace(make(2, created=NOW - timedelta(days=1)), bump_count=2)
    assert selection.select_frog([today, bumped], NOW).id == 2


def test_frog_empty_backlog_is_none():
    assert selection.select_frog([], NOW) is None
    assert selection.select_frog([make(1, status=STATUS_DONE)], NOW) is None


def test_stale_review_keeps_14_days_and_more_oldest_first():
    at_14 = make(1, created=NOW - timedelta(days=14))
    at_13 = make(2, created=NOW - timedelta(days=13))
    at_40 = make(3, created=NOW - timedelta(days=40))
    got = selection.select_stale_for_review([at_14, at_13, at_40], NOW)
    assert [t.id for t in got] == [3, 1]


def test_stale_review_limit():
    tasks = [make(i, created=NOW - timedelta(days=20 + i)) for i in range(1, 8)]
    got = selection.select_stale_for_review(tasks, NOW, limit=5)
    assert [t.id for t in got] == [7, 6, 5, 4, 3]


def _vt(text, due=None, now=False, note="n"):
    return VaultTask(text=text, note=note, due=due, now=now)


def test_vault_reminders_windows():
    today = date(2026, 10, 8)
    tasks = [
        _vt("overdue", due=today - timedelta(days=2)),
        _vt("today", due=today),
        _vt("day7", due=today + timedelta(days=7)),
        _vt("day8", due=today + timedelta(days=8)),
        _vt("thisweek", now=True),
        _vt("undated"),
    ]
    got = selection.select_vault_reminders(tasks, today)
    assert [t.text for t in got] == ["overdue", "today", "day7", "thisweek"]


def test_vault_due_is_today_and_overdue_only():
    today = date(2026, 10, 8)
    tasks = [
        _vt("tomorrow", due=today + timedelta(days=1)),
        _vt("today", due=today),
        _vt("overdue", due=today - timedelta(days=3)),
        _vt("thisweek", now=True),
    ]
    got = selection.select_vault_due(tasks, today)
    assert [t.text for t in got] == ["overdue", "today"]
