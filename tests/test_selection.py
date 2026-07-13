from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from jolt import selection
from jolt.models import (
    PRIORITY_IMPORTANT,
    PRIORITY_NORMAL,
    STATUS_DONE,
    STATUS_PENDING,
    Task,
)

TZ = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 7, 12, 8, tzinfo=TZ)


def make(
    id,
    *,
    priority=PRIORITY_NORMAL,
    deadline=None,
    created=NOW,
    status=STATUS_PENDING,
    blocked_by=None,
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
        blocked_by=blocked_by,
    )


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


def test_is_blocked_true_when_blocker_pending():
    blocker = make(1)
    dep = make(2, blocked_by=1)
    assert selection.is_blocked(dep, [blocker, dep]) is True


def test_is_blocked_false_when_blocker_done():
    blocker = make(1, status=STATUS_DONE)
    dep = make(2, blocked_by=1)
    assert selection.is_blocked(dep, [blocker, dep]) is False


def test_is_blocked_false_when_not_blocked():
    assert selection.is_blocked(make(1), [make(1)]) is False


def test_blocked_task_sorts_directly_below_its_blocker():
    # An important, near-deadline blocked task would normally sort to the top, but it
    # must appear right after its blocker instead.
    blocker = make(1, priority=PRIORITY_NORMAL, created=NOW)
    dep = make(2, priority=PRIORITY_IMPORTANT, deadline=date(2026, 7, 13), blocked_by=1)
    other = make(3, priority=PRIORITY_NORMAL, created=NOW)
    ordered = [t.id for t in selection.order_backlog([dep, blocker, other])]
    assert ordered.index(2) == ordered.index(1) + 1  # dep is immediately after blocker


def test_blocked_task_is_not_selected_as_focus():
    # The blocked task is important + stale (would normally lead); the blocker is fresh
    # and normal. Focus must still land on the actionable blocker.
    blocker = make(1, priority=PRIORITY_NORMAL, created=NOW)
    dep = make(2, priority=PRIORITY_IMPORTANT, created=NOW - timedelta(days=5), blocked_by=1)
    result = selection.select_daily_focus([blocker, dep], NOW)
    assert result.focus.id == 1


def test_blocked_task_is_not_a_rescue():
    blocker = make(1, priority=PRIORITY_IMPORTANT, created=NOW)
    stale_dep = make(2, created=NOW - timedelta(days=5), blocked_by=1)
    result = selection.select_daily_focus([blocker, stale_dep], NOW)
    assert 2 not in [t.id for t in result.rescues]


def test_nag_stance_start_for_important():
    assert selection.nag_stance(make(1, priority=PRIORITY_IMPORTANT)) == "start"


def test_nag_stance_drop_for_normal():
    assert selection.nag_stance(make(1, priority=PRIORITY_NORMAL)) == "drop"


def test_slow_resurface_picks_normal_stale_task():
    t = make(1, created=NOW - timedelta(days=5))
    assert selection.select_slow_resurface([t], NOW).id == 1


def test_slow_resurface_none_when_nothing_eligible():
    fresh = make(1, created=NOW)  # not stale
    important = make(2, priority=PRIORITY_IMPORTANT, created=NOW - timedelta(days=5))
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


def test_slow_resurface_ignores_blocked_task():
    blocker = make(1, created=NOW - timedelta(days=5))
    dep = make(2, created=NOW - timedelta(days=5), blocked_by=1)
    # exclude the blocker as the focus; the only other candidate (dep) is blocked -> None
    assert selection.select_slow_resurface([blocker, dep], NOW, exclude_id=1) is None


def test_slow_resurface_eligible_at_exactly_the_cadence_boundary():
    from jolt import config, selection

    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    created = now - timedelta(days=10)  # stale
    last_nagged = now - timedelta(days=config.SLOW_RESURFACE_DAYS)  # exactly the cadence
    task = Task(
        id=1, text="sort photos", priority=PRIORITY_NORMAL, deadline=None,
        created_at=created, status=STATUS_PENDING, last_nagged_at=last_nagged, completed_at=None,
    )
    assert selection.select_slow_resurface([task], now) is task
