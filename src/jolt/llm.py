import logging
from dataclasses import dataclass
from datetime import date, datetime

from . import config
from .models import DailyFocus, PRIORITY_IMPORTANT, Task

logger = logging.getLogger(__name__)


def _log_usage(label: str, response) -> None:
    """Record model, token usage and stop reason for one Claude call, so cost and
    truncation issues are visible in the logs."""
    usage = getattr(response, "usage", None)
    logger.info(
        "Claude %s: stop=%s in=%s out=%s",
        label,
        getattr(response, "stop_reason", "?"),
        getattr(usage, "input_tokens", "?"),
        getattr(usage, "output_tokens", "?"),
    )


def _importance(task: Task) -> str:
    return "important" if task.priority == PRIORITY_IMPORTANT else "normal"


@dataclass(frozen=True)
class Intent:
    action: str
    text: str | None = None
    priority: str | None = None
    deadline: date | None = None
    task_id: int | None = None
    blocked_by: int | None = None
    reply: str | None = None
    clear_deadline: bool = False


def parse_intent(tool_input: dict) -> Intent:
    deadline = tool_input.get("deadline")
    return Intent(
        action=tool_input["action"],
        text=tool_input.get("text"),
        priority=tool_input.get("priority"),
        deadline=date.fromisoformat(deadline) if deadline else None,
        task_id=tool_input.get("task_id"),
        blocked_by=tool_input.get("blocked_by"),
        reply=tool_input.get("reply"),
        clear_deadline=tool_input.get("clear_deadline", False),
    )


_TOOL = {
    "name": "record_intent",
    "description": "Record what the user's Telegram message means for their task list.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "complete", "drop", "edit", "block", "unblock", "list", "answer"],
                "description": "add a task, complete/drop an existing one, edit an existing task's deadline/priority/text, block a task on another / unblock it, list the backlog, or answer a question / reply to the user.",
            },
            "text": {"type": "string", "description": "Task text, for action=add."},
            "priority": {
                "type": "string",
                "enum": ["normal", "important"],
                "description": "For action=add: always judge this from the wording and stakes, never leave it blank. 'important' for anything with real consequences (a deadline, money, health, admin/legal weight); 'normal' otherwise.",
            },
            "deadline": {
                "type": "string",
                "description": "ISO date YYYY-MM-DD, if the user gave one.",
            },
            "task_id": {
                "type": "integer",
                "description": "The id of the existing task, for complete/drop/edit.",
            },
            "blocked_by": {
                "type": "integer",
                "description": "For action=block: the id of the prerequisite task that must be finished first.",
            },
            "reply": {
                "type": "string",
                "description": "For action=answer: the exact message to send back to the user.",
            },
            "clear_deadline": {
                "type": "boolean",
                "description": "For action=edit only: set true to remove a task's due date entirely. Leave unset to keep the current due date.",
            },
        },
        "required": ["action"],
    },
}


def _task_lines(tasks: list[Task]) -> str:
    pending = [t for t in tasks if t.status == "pending"]
    if not pending:
        return "(backlog is empty)"
    return "\n".join(
        f"- id={t.id}: {t.text}"
        + (" [important]" if t.priority == "important" else "")
        + (f" (due {t.deadline.isoformat()})" if t.deadline else "")
        for t in pending
    )


def interpret_message(message: str, tasks: list[Task], now: datetime, client) -> list[Intent]:
    system = (
        "You are Jolt, a personal accountability bot. Read the user's message and record "
        "what it means by calling record_intent. A single message can contain several things "
        "at once (for example a pasted list of tasks); call record_intent once per distinct "
        "task or action, never fold several tasks into one. When adding a task, judge its "
        "importance from the wording and stakes and set priority (normal / important); do not "
        "leave it blank, since the backlog is ranked by importance. To complete or drop a task, "
        "pick the matching task_id from the current backlog. When the user says one task must "
        "happen before another (for example 'X needs Y first', 'can't do X until Y', 'Y blocks X'), "
        "call record_intent with action=block, task_id = the task that is blocked and blocked_by = "
        "the prerequisite task's id; emit one block call per blocked task. To lift a dependency, use "
        "action=unblock with the task_id. "
        "To change an existing task rather than add a new one, use action=edit with its task_id "
        "and only the fields that change: a new deadline, a new priority (normal/important), or "
        "reworded text. Prefer edit over dropping and re-adding, so the task keeps its age. To "
        "remove a due date entirely, use action=edit with clear_deadline=true. A message like "
        "'change all due dates to today' becomes one edit call per pending task. "
        "For a question or a blocker conversation, use "
        "action=answer and write a short, plain reply (no cheerleading, no em dashes). The user may "
        "write in any language, but always store the task text in English (translate it if needed). "
        f"Today is {now:%A, %Y-%m-%d}. Resolve any relative deadline (today, tomorrow, next "
        "week, in 3 days) against today's date and record it as an ISO YYYY-MM-DD date. "
        "Current backlog:\n" + _task_lines(tasks)
    )
    logger.debug("interpret_message: %d pending task(s) in context", len(tasks))
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=1000,
        system=system,
        tools=[_TOOL],
        # "any" forces at least one record_intent call but, unlike naming the tool,
        # still allows Claude to emit one call per task in a multi-task message.
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": message}],
    )
    _log_usage("interpret_message", response)
    intents = [
        parse_intent(block.input)
        for block in response.content
        if getattr(block, "type", None) == "tool_use"
    ]
    if not intents:
        logger.warning("Claude returned no tool_use block; falling back to clarification reply")
        return [Intent(action="answer", reply="Sorry, I did not catch that. Try again?")]
    return intents


def _text_of(response) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""


def write_focus(focus: DailyFocus, now: datetime, client) -> str:
    if focus.focus is None:
        return "Nothing on the list today. Enjoy it."
    age = (now - focus.focus.created_at).days
    rescues = (
        "; ".join(
            f"{t.text} ({(now - t.created_at).days}d old, {_importance(t)})" for t in focus.rescues
        )
        or "none"
    )
    system = (
        "You are Jolt. Write a short morning message (2 to 4 lines, no em dashes). Lead with the "
        "one focus task as the single thing to hit today, and push harder on important tasks than "
        "low-stakes ones. If there are rescue tasks that have gone stale, mention them and ask what "
        "is blocking them. Be plain and direct, never guilt-tripping."
    )
    user = f"Focus task: {focus.focus.text} ({age}d old, {_importance(focus.focus)}). Rescues: {rescues}."
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    _log_usage("write_focus", response)
    return _text_of(response)


def write_nag(task: Task, now: datetime, client) -> str:
    age = (now - task.created_at).days
    system = (
        "You are Jolt. Write one short nag (1 to 2 lines, no em dashes) about the task below. "
        "Push harder the more important the task is, the older it is, and the later in the day it "
        "is: an important task dodged for days gets blunt and insistent by evening; a normal, "
        "low-stakes task stays gentle and easy to wave off. If it is several days old, first ask "
        "what is actually blocking it before pushing. Never guilt-trip."
    )
    user = f"Task: {task.text}. Importance: {_importance(task)}. Age: {age} days. Current hour: {now.hour}."
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=200,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    _log_usage("write_nag", response)
    return _text_of(response)
