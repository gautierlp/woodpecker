from datetime import date, datetime, timedelta

from . import config
from .models import STATUS_PENDING, Task
from .vault import VaultTask

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


def order_backlog(tasks: list[Task]) -> list[Task]:
    pending = [t for t in tasks if t.status == STATUS_PENDING]
    return sorted(pending, key=priority_sort_key)


def is_stale(task: Task, now: datetime, threshold_days: int = config.STALE_THRESHOLD_DAYS) -> bool:
    if task.status != STATUS_PENDING:
        return False
    return now - task.created_at >= timedelta(days=threshold_days)


def select_frog(tasks: list[Task], now: datetime) -> Task | None:
    """The one thing of the day, from all pending tasks at any priority: overdue first,
    then the highest bump_count (the avoidance signal), then the oldest. None only when
    nothing is pending."""
    today = now.date()
    pending = [t for t in tasks if t.status == STATUS_PENDING]
    if not pending:
        return None

    def key(task: Task) -> tuple:
        overdue = task.deadline is not None and task.deadline < today
        return (0 if overdue else 1, -task.bump_count, task.created_at)

    return min(pending, key=key)


def select_stale_for_review(tasks: list[Task], now: datetime, limit: int = 5) -> list[Task]:
    """The tasks for the weekly review: pending STALE_REVIEW_DAYS or more, oldest first."""
    stale = [t for t in tasks if is_stale(t, now, config.STALE_REVIEW_DAYS)]
    return sorted(stale, key=lambda t: t.created_at)[:limit]


def select_vault_reminders(tasks: list[VaultTask], today: date) -> list[VaultTask]:
    """The vault lines of the morning: overdue, due within VAULT_SOON_DAYS, or marked ⏫.
    Sorted by date, so overdue comes first and an undated ⏫ comes last."""
    soon = today + timedelta(days=config.VAULT_SOON_DAYS)
    picked = [t for t in tasks if (t.due is not None and t.due <= soon) or t.now]
    return sorted(picked, key=lambda t: (t.due or date.max, t.note, t.text))


def select_vault_due(tasks: list[VaultTask], today: date) -> list[VaultTask]:
    """The vault lines of the check-in: due today or overdue."""
    due = [t for t in tasks if t.due is not None and t.due <= today]
    return sorted(due, key=lambda t: (t.due, t.note, t.text))


def is_quiet_hours(
    now: datetime,
    start_hour: int = config.QUIET_START_HOUR,
    end_hour: int = config.QUIET_END_HOUR,
) -> bool:
    return now.hour < start_hour or now.hour >= end_hour
