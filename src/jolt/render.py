from datetime import date, datetime, timedelta

from .models import Task
from .selection import order_backlog

_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Groups render top-to-bottom in this order; empty groups are omitted.
_GROUPS = [
    ("overdue", "🔴 OVERDUE"),
    ("today", "🔥 TODAY"),
    ("tomorrow", "📅 TOMORROW"),
    ("week", "🗓 THIS WEEK"),
    ("later", "📆 LATER"),
    ("none", "📥 NO DEADLINE"),
]


def _bucket(deadline: date | None, today: date) -> str:
    if deadline is None:
        return "none"
    if deadline < today:
        return "overdue"
    if deadline == today:
        return "today"
    if deadline == today + timedelta(days=1):
        return "tomorrow"
    if deadline <= today + timedelta(days=7):
        return "week"
    return "later"


def _date_suffix(deadline: date | None, today: date) -> str:
    # The group header already carries today/tomorrow/none, so those lines stay bare.
    # Overdue and later lines need their own date since a group can span several days.
    bucket = _bucket(deadline, today)
    if bucket == "overdue":
        return f" ({(today - deadline).days}d overdue)"
    if bucket == "week":
        return f" ({_WEEKDAYS[deadline.weekday()]})"
    if bucket == "later":
        return f" ({_MONTHS[deadline.month - 1]} {deadline.day:02d})"
    return ""


def _chain(task: Task, by_id: dict[int, Task]) -> tuple[int, Task]:
    # Climb the blocked-by links (only while the blocker is still pending) to find the
    # task's nesting depth and the root it hangs from. A task whose blocker is done or
    # absent is its own root at depth 0, so it renders as a normal top-level line.
    depth = 0
    current = task
    seen: set[int] = set()
    while current.blocked_by in by_id and current.id not in seen:
        seen.add(current.id)
        current = by_id[current.blocked_by]
        depth += 1
    return depth, current


def _grouped(tasks: list[Task], today: date) -> tuple[dict[str, list[Task]], dict[int, Task]]:
    # Partition the ordered backlog into deadline groups, preserving the within-group
    # order (priority, then deadline, then age) that order_backlog already produced.
    # A blocked task follows its root blocker's deadline, so a family stays together
    # under one header even when the child's own deadline falls in a different bucket.
    ordered = order_backlog(tasks)
    by_id = {t.id: t for t in ordered}  # pending tasks only
    grouped: dict[str, list[Task]] = {key: [] for key, _ in _GROUPS}
    for task in ordered:
        _, root = _chain(task, by_id)
        grouped[_bucket(root.deadline, today)].append(task)
    return grouped, by_id


def display_order(tasks: list[Task], now: datetime) -> list[Task]:
    # The exact top-to-bottom order tasks are rendered in: groups in _GROUPS order, each
    # group in its within-group order. The user's visible number for a task is its 1-based
    # index here, so llm.py numbers the same list identically to resolve replies like
    # "edit 2" back to the task the user is looking at.
    grouped, _ = _grouped(tasks, now.date())
    return [task for key, _ in _GROUPS for task in grouped[key]]


def render_backlog(tasks: list[Task], now: datetime) -> str:
    today = now.date()
    grouped, by_id = _grouped(tasks, today)
    ordered = [task for key, _ in _GROUPS for task in grouped[key]]
    if not ordered:
        return "Backlog empty. Nice."

    # Number lines by display position, not the task's stable db id. The id is an
    # internal handle (see llm.py); the user only ever sees and types the position, so
    # the list reads 1, 2, 3... down the page and never leaks gappy, ever-growing ids.
    position = {task.id: i for i, task in enumerate(ordered, 1)}

    count = len(ordered)
    lines = [f"📋 {count} task{'s' if count != 1 else ''}"]
    for key, header in _GROUPS:
        group = grouped[key]
        if not group:
            continue
        lines.append("")
        lines.append(header)
        for task in group:
            depth, _ = _chain(task, by_id)
            num = position[task.id]
            if depth:
                # Blocked child: indent under its blocker, no date (it is gated by the
                # parent, so its own deadline is not actionable yet).
                indent = "   " * depth
                lines.append(f"{indent}↳ {num}. {task.text}")
            else:
                suffix = _date_suffix(task.deadline, today)
                lines.append(f"{num}. {task.text}{suffix}")
    return "\n".join(lines)
