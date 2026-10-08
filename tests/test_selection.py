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


def test_select_daily_focus_picks_top_and_two_rescues():
    focus = make(1, priority=4, created=NOW)
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
    fresh_imp = make(1, priority=4, deadline=date(2026, 7, 13), created=NOW)
    stale_imp = make(2, priority=4, created=NOW - timedelta(days=5))
    stale_normal = make(3, priority=0, created=NOW - timedelta(days=6))
    result = selection.select_daily_focus([fresh_imp, stale_imp, stale_normal], NOW)
    assert result.focus.id == 2
    assert 2 not in [t.id for t in result.rescues]


def test_lead_falls_back_to_top_priority_when_no_important_is_stale():
    fresh_imp = make(1, priority=4, created=NOW)
    stale_normal = make(2, priority=0, created=NOW - timedelta(days=5))
    result = selection.select_daily_focus([fresh_imp, stale_normal], NOW)
    assert result.focus.id == 1


def test_longer_stale_high_task_leads_the_focus():
    # Two stale high tasks; the longer-estimated one is the bigger avoided thing and leads.
    short = make(1, priority=4, created=NOW - timedelta(days=5))
    long = make(2, priority=4, created=NOW - timedelta(days=5))
    short = replace(short, estimate_seconds=900)
    long = replace(long, estimate_seconds=14400)
    result = selection.select_daily_focus([short, long], NOW)
    assert result.focus.id == 2


def test_estimate_only_breaks_ties_within_stale_high_not_across_bands():
    # A short stale high task still leads a long stale low task: band wins over duration.
    high_short = replace(make(1, priority=4, created=NOW - timedelta(days=5)), estimate_seconds=300)
    low_long = replace(make(2, priority=0, created=NOW - timedelta(days=5)), estimate_seconds=14400)
    result = selection.select_daily_focus([high_short, low_long], NOW)
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


def test_nag_stance_start_for_high_band():
    assert selection.nag_stance(make(1, priority=4)) == "start"


def test_nag_stance_drop_for_low_band():
    assert selection.nag_stance(make(1, priority=0)) == "drop"


def test_nag_stance_poke_for_mid_band():
    assert selection.nag_stance(make(1, priority=2)) == "poke"


def test_slow_resurface_picks_normal_stale_task():
    t = make(1, created=NOW - timedelta(days=5))
    assert selection.select_slow_resurface([t], NOW).id == 1


def test_slow_resurface_none_when_nothing_eligible():
    fresh = make(1, created=NOW)  # not stale
    important = make(2, priority=4, created=NOW - timedelta(days=5))
    assert selection.select_slow_resurface([fresh, important], NOW) is None


def test_slow_resurface_excludes_focus_id():
    focus = make(1, created=NOW - timedelta(days=5))
    other = make(2, created=NOW - timedelta(days=5))
    assert selection.select_slow_resurface([focus, other], NOW, exclude_id=1).id == 2


def test_slow_resurface_gated_within_cadence():
    recent = make(1, created=NOW - timedelta(days=10), nagged=NOW - timedelta(days=2))
    assert selection.select_slow_resurface([recent], NOW) is None


def test_slow_resurface_eligible_after_cadence():
    old = make(1, created=NOW - timedelta(days=30), nagged=NOW - timedelta(days=8))
    assert selection.select_slow_resurface([old], NOW).id == 1


def test_slow_resurface_prefers_never_nagged():
    never = make(1, created=NOW - timedelta(days=5))
    nagged_long_ago = make(2, created=NOW - timedelta(days=20), nagged=NOW - timedelta(days=10))
    assert selection.select_slow_resurface([never, nagged_long_ago], NOW).id == 1


def test_slow_resurface_includes_stale_mid_task():
    mid = make(1, priority=2, created=NOW - timedelta(days=5))
    assert selection.select_slow_resurface([mid], NOW).id == 1


def test_slow_resurface_excludes_stale_high_task():
    high = make(1, priority=4, created=NOW - timedelta(days=5))
    assert selection.select_slow_resurface([high], NOW) is None


def test_slow_resurface_eligible_at_exactly_the_cadence_boundary():
    from woodpecker import config, selection

    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    created = now - timedelta(days=10)  # stale
    last_nagged = now - timedelta(days=config.SLOW_RESURFACE_DAYS)  # exactly the cadence
    task = Task(
        id=1,
        text="sort photos",
        priority=0,
        deadline=None,
        created_at=created,
        status=STATUS_PENDING,
        last_nagged_at=last_nagged,
        completed_at=None,
    )
    assert selection.select_slow_resurface([task], now) is task


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
