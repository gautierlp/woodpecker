from datetime import datetime

from .models import PRIORITY_IMPORTANT, Task
from .selection import is_stale, is_urgent, order_backlog


def _dot(task: Task, now: datetime) -> str:
    # Urgency-aware scale: the redder the dot, the more it should bug you.
    if task.priority == PRIORITY_IMPORTANT:
        return "🔴" if is_urgent(task, now) else "🟠"
    return "🟡" if is_stale(task, now) else "⚪"


def render_backlog(tasks: list[Task], now: datetime) -> str:
    ordered = order_backlog(tasks)
    if not ordered:
        return "Backlog empty. Nice."
    lines = ["Backlog:"]
    for i, task in enumerate(ordered, 1):
        due = f" (due {task.deadline.isoformat()})" if task.deadline else ""
        lines.append(f"{i}. {_dot(task, now)} {task.text}{due}")
    return "\n".join(lines)
