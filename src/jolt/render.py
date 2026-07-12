from .models import PRIORITY_IMPORTANT, Task
from .selection import order_backlog


def render_backlog(tasks: list[Task]) -> str:
    ordered = order_backlog(tasks)
    if not ordered:
        return "Backlog empty. Nice."
    lines = ["Backlog:"]
    for i, task in enumerate(ordered, 1):
        mark = "‼️" if task.priority == PRIORITY_IMPORTANT else "•"
        due = f" (due {task.deadline.isoformat()})" if task.deadline else ""
        lines.append(f"{i}. {mark} {task.text}{due}")
    return "\n".join(lines)
