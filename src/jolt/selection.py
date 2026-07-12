from datetime import date, datetime, timedelta

from . import config
from .models import DailyFocus, PRIORITY_IMPORTANT, STATUS_PENDING, Task


def priority_sort_key(task: Task) -> tuple:
    prio_rank = 0 if task.priority == PRIORITY_IMPORTANT else 1
    deadline_rank = task.deadline or date.max
    return (prio_rank, deadline_rank, task.created_at)


def is_blocked(task: Task, tasks: list[Task]) -> bool:
    if task.blocked_by is None:
        return False
    blocker = next((t for t in tasks if t.id == task.blocked_by), None)
    return blocker is not None and blocker.status == STATUS_PENDING


def order_backlog(tasks: list[Task]) -> list[Task]:
    pending = [t for t in tasks if t.status == STATUS_PENDING]
    dependents: dict[int, list[Task]] = {}
    for t in pending:
        if is_blocked(t, pending):
            dependents.setdefault(t.blocked_by, []).append(t)
    roots = sorted([t for t in pending if not is_blocked(t, pending)], key=priority_sort_key)
    ordered: list[Task] = []

    def emit(task: Task) -> None:
        ordered.append(task)
        for dep in sorted(dependents.get(task.id, []), key=priority_sort_key):
            emit(dep)

    for root in roots:
        emit(root)
    return ordered


def is_stale(task: Task, now: datetime, threshold_days: int = config.STALE_THRESHOLD_DAYS) -> bool:
    if task.status != STATUS_PENDING:
        return False
    return now - task.created_at >= timedelta(days=threshold_days)


def is_urgent(task: Task, now: datetime, due_soon_days: int = config.DUE_SOON_DAYS) -> bool:
    # Urgent = the deadline is close/overdue, or the task has been sitting long enough
    # to count as avoided. Either way it should press harder.
    if task.deadline is not None and task.deadline <= (now.date() + timedelta(days=due_soon_days)):
        return True
    return is_stale(task, now)


def select_daily_focus(tasks: list[Task], now: datetime) -> DailyFocus:
    ordered = order_backlog(tasks)
    actionable = [t for t in ordered if not is_blocked(t, tasks)]
    if not actionable:
        return DailyFocus(focus=None, rescues=[])
    # Avoidance wins the lead spot: an important task that has gone stale is the
    # deferral signal, so it leads even over a fresher, nearer-deadline one. Only when
    # nothing important is being dodged does the lead fall back to the top of the order.
    stale_important = [
        t for t in actionable if t.priority == PRIORITY_IMPORTANT and is_stale(t, now)
    ]
    focus = stale_important[0] if stale_important else actionable[0]
    rescues = [t for t in actionable if t.id != focus.id and is_stale(t, now)][:2]
    return DailyFocus(focus=focus, rescues=rescues)


def is_quiet_hours(
    now: datetime,
    start_hour: int = config.QUIET_START_HOUR,
    end_hour: int = config.QUIET_END_HOUR,
) -> bool:
    return now.hour < start_hour or now.hour >= end_hour
