from datetime import date, datetime, timedelta

from . import config
from .models import DailyFocus, STATUS_PENDING, Task

BAND_HIGH = "high"
BAND_MID = "mid"
BAND_LOW = "low"


def priority_band(task: Task) -> str:
    if task.priority >= config.PRIORITY_HIGH_MIN:
        return BAND_HIGH
    if task.priority >= config.PRIORITY_MID_MIN:
        return BAND_MID
    return BAND_LOW


_BAND_RANK = {BAND_HIGH: 0, BAND_MID: 1, BAND_LOW: 2}


def priority_sort_key(task: Task) -> tuple:
    deadline_rank = task.deadline or date.max
    return (_BAND_RANK[priority_band(task)], deadline_rank, task.position, task.created_at)


def avoidance_sort_key(task: Task, now: datetime) -> tuple:
    """Ranking for what to drag into the light: high band first, then stale-before-fresh,
    then the longer estimate (the bigger avoided thing leads), then the ordinary order."""
    stale_rank = 0 if is_stale(task, now) else 1
    return (
        _BAND_RANK[priority_band(task)],
        stale_rank,
        -(task.estimate_seconds or 0),
        task.deadline or date.max,
        task.position,
        task.created_at,
    )


def nag_stance(task: Task) -> str:
    """How the nag leans by priority band: 'start' pushes a high task toward action,
    'poke' gently checks in on a mid task without drop pressure, 'drop' nudges a low-value
    task toward the exit."""
    band = priority_band(task)
    if band == BAND_HIGH:
        return "start"
    if band == BAND_MID:
        return "poke"
    return "drop"


def order_backlog(tasks: list[Task]) -> list[Task]:
    pending = [t for t in tasks if t.status == STATUS_PENDING]
    return sorted(pending, key=priority_sort_key)


def is_stale(task: Task, now: datetime, threshold_days: int = config.STALE_THRESHOLD_DAYS) -> bool:
    if task.status != STATUS_PENDING:
        return False
    return now - task.created_at >= timedelta(days=threshold_days)


def select_daily_focus(tasks: list[Task], now: datetime) -> DailyFocus:
    pending = [t for t in tasks if t.status == STATUS_PENDING]
    if not pending:
        return DailyFocus(focus=None, rescues=[])
    by_avoidance = sorted(pending, key=lambda t: avoidance_sort_key(t, now))
    focus = by_avoidance[0]
    rescues = [t for t in by_avoidance if t.id != focus.id and is_stale(t, now)][:2]
    return DailyFocus(focus=focus, rescues=rescues)


def select_frog(tasks: list[Task], now: datetime) -> Task | None:
    """The single most-avoided important task: the lead of the day. Eligible = pending,
    priority >= MATTERS_MIN_PRIORITY, has a due date. Picks the highest bump_count, ties
    broken by higher priority then oldest. If nothing has been bumped yet (day one), falls
    back to the highest-priority eligible task due today or overdue, then oldest."""
    today = now.date()
    eligible = [
        t
        for t in tasks
        if t.status == STATUS_PENDING
        and t.priority >= config.MATTERS_MIN_PRIORITY
        and t.deadline is not None
    ]
    if not eligible:
        return None
    bumped = [t for t in eligible if t.bump_count > 0]
    if bumped:
        return max(bumped, key=lambda t: (t.bump_count, t.priority, -t.created_at.timestamp()))
    due_now = [t for t in eligible if t.deadline <= today]
    if not due_now:
        return None
    return max(due_now, key=lambda t: (t.priority, -t.created_at.timestamp()))


def is_quiet_hours(
    now: datetime,
    start_hour: int = config.QUIET_START_HOUR,
    end_hour: int = config.QUIET_END_HOUR,
) -> bool:
    return now.hour < start_hour or now.hour >= end_hour


def select_slow_resurface(
    tasks: list[Task],
    now: datetime,
    exclude_id: int | None = None,
    cadence_days: int = config.SLOW_RESURFACE_DAYS,
) -> Task | None:
    """The one non-high-priority, avoided task to poke on the slow cadence, or None.

    A waved-off task should not vanish, but it must not be chased like a frog.
    Eligible: pending, non-high band (mid or low), stale, not the excluded focus, and
    either never nagged or last nagged at least cadence_days ago. The nag stance leans
    gentle-poke for mid and drop-nudge for low. Among the eligible, prefer the one that
    has waited longest for a poke: never-nagged first, then the oldest last_nagged_at,
    then the oldest task."""
    eligible = [
        t
        for t in tasks
        if t.status == STATUS_PENDING
        and priority_band(t) != BAND_HIGH
        and t.id != exclude_id
        and is_stale(t, now)
        and (t.last_nagged_at is None or now - t.last_nagged_at >= timedelta(days=cadence_days))
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda t: (
            t.last_nagged_at is not None,  # False (never nagged) sorts first
            t.last_nagged_at or t.created_at,  # then oldest last poke
            t.created_at,  # then oldest task
        ),
    )
