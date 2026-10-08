from datetime import date, datetime, timedelta

from .models import Task
from .selection import order_backlog
from .vault import VaultTask

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


def _grouped(tasks: list[Task], today: date) -> dict[str, list[Task]]:
    ordered = order_backlog(tasks)
    grouped: dict[str, list[Task]] = {key: [] for key, _ in _GROUPS}
    for task in ordered:
        grouped[_bucket(task.deadline, today)].append(task)
    return grouped


def display_order(tasks: list[Task], now: datetime) -> list[Task]:
    grouped = _grouped(tasks, now.date())
    return [task for key, _ in _GROUPS for task in grouped[key]]


def render_backlog(tasks: list[Task], now: datetime) -> str:
    today = now.date()
    grouped = _grouped(tasks, today)
    ordered = [task for key, _ in _GROUPS for task in grouped[key]]
    if not ordered:
        return "Backlog empty. Nice."
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
            suffix = _date_suffix(task.deadline, today)
            lines.append(f"{position[task.id]}. {task.text}{suffix}")
    return "\n".join(lines)


# One-letter replies: Beeper shows no Telegram inline buttons, so one letter is the
# nearest thing to one tap. replies.py parses them.
FROG_LEGEND = "d done · o on it · t tomorrow · x drop"
REFRAME_LEGEND = "s smaller step · n not mine to do · x drop"
_VAULT_FOOTER = "Tick these in Obsidian."


def _vault_line(task: VaultTask, today: date) -> str:
    if task.due is None:
        when = ""
    elif task.due < today:
        when = "overdue, "
    else:
        when = f"📅 {task.due.isoformat()}, "
    return f"• {task.text} ({when}{task.note})"


def _vault_lines(header: str, tasks: list[VaultTask], today: date) -> str:
    if not tasks:
        return ""
    lines = [header] + [_vault_line(t, today) for t in tasks] + [_VAULT_FOOTER]
    return "\n".join(lines)


def render_vault_block(tasks: list[VaultTask], today: date) -> str:
    """The morning's vault block. Plain lines with no number: Woodpecker cannot tick a
    vault box, so nothing here is answerable in the chat."""
    return _vault_lines("From the vault:", tasks, today)


def render_vault_due(tasks: list[VaultTask], today: date) -> str:
    return _vault_lines("Due in the vault today:", tasks, today)


def render_vault_unreadable(reason: str) -> str:
    return f"Vault: not readable ({reason})"


def render_checkin(task: Task, started: bool, legend: str) -> str:
    line = f'How is "{task.text}" going?' if started else f'Still on for "{task.text}" today?'
    return f"{line}\n{legend}"
