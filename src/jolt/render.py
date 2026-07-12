from datetime import date, datetime, timedelta

from .models import PRIORITY_IMPORTANT, Task
from .selection import is_blocked, is_stale, is_urgent, order_backlog

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


def _dot(task: Task, now: datetime) -> str:
    # Urgency-aware scale: the redder the dot, the more it should bug you.
    if task.priority == PRIORITY_IMPORTANT:
        return "🔴" if is_urgent(task, now) else "🟠"
    return "🟡" if is_stale(task, now) else "⚪"


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


def render_backlog(tasks: list[Task], now: datetime) -> str:
    ordered = order_backlog(tasks)
    if not ordered:
        return "Backlog empty. Nice."
    today = now.date()

    # Partition the ordered backlog into deadline groups, preserving the within-group
    # order (priority, then deadline, then age) that order_backlog already produced.
    grouped: dict[str, list[Task]] = {key: [] for key, _ in _GROUPS}
    for task in ordered:
        grouped[_bucket(task.deadline, today)].append(task)

    count = len(ordered)
    lines = [f"📋 {count} task{'s' if count != 1 else ''}"]
    for key, header in _GROUPS:
        group = grouped[key]
        if not group:
            continue
        lines.append("")
        lines.append(header)
        for task in group:
            # Number each line by the task's stable id, not its sort position: the id is
            # what the LLM and orchestrator key on, so a number the user types back
            # ("complete 7", "3 blocks 7") resolves to the same task they see here.
            if is_blocked(task, tasks):
                dot = "⚪"
                tag = f" (blocked by {task.blocked_by})"
            else:
                dot = _dot(task, now)
                tag = ""
            suffix = _date_suffix(task.deadline, today)
            lines.append(f"{task.id}. {dot} {task.text}{suffix}{tag}")
    return "\n".join(lines)
