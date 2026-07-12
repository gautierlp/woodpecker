from datetime import datetime

from .models import PRIORITY_IMPORTANT, Task
from .selection import is_blocked, is_stale, is_urgent, order_backlog


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
    # Number each line by the task's stable id, not its sort position: the id is what
    # the LLM and orchestrator key on, so a number the user types back ("complete 7",
    # "3 blocks 7") resolves to the same task they see here.
    for task in ordered:
        due = f" (due {task.deadline.isoformat()})" if task.deadline else ""
        if is_blocked(task, tasks):
            dot = "⚪"
            tag = f" (blocked by {task.blocked_by})"
        else:
            dot = _dot(task, now)
            tag = ""
        lines.append(f"{task.id}. {dot} {task.text}{due}{tag}")
    return "\n".join(lines)
