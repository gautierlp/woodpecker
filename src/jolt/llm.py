from dataclasses import dataclass
from datetime import date, datetime

from . import config
from .models import DailyFocus, Task


@dataclass(frozen=True)
class Intent:
    action: str
    text: str | None = None
    priority: str | None = None
    deadline: date | None = None
    task_id: int | None = None
    reply: str | None = None


def parse_intent(tool_input: dict) -> Intent:
    deadline = tool_input.get("deadline")
    return Intent(
        action=tool_input["action"],
        text=tool_input.get("text"),
        priority=tool_input.get("priority"),
        deadline=date.fromisoformat(deadline) if deadline else None,
        task_id=tool_input.get("task_id"),
        reply=tool_input.get("reply"),
    )


_TOOL = {
    "name": "record_intent",
    "description": "Record what the user's Telegram message means for their task list.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "complete", "drop", "list", "answer"],
                "description": "add a task, complete/drop an existing one, list the backlog, or answer a question / reply to the user.",
            },
            "text": {"type": "string", "description": "Task text, for action=add."},
            "priority": {"type": "string", "enum": ["normal", "important"]},
            "deadline": {"type": "string", "description": "ISO date YYYY-MM-DD, if the user gave one."},
            "task_id": {"type": "integer", "description": "The id of the existing task, for complete/drop."},
            "reply": {"type": "string", "description": "For action=answer: the exact message to send back to the user."},
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
        + (f" [important]" if t.priority == "important" else "")
        + (f" (due {t.deadline.isoformat()})" if t.deadline else "")
        for t in pending
    )


def interpret_message(message: str, tasks: list[Task], client) -> Intent:
    system = (
        "You are Jolt, a personal accountability bot. Read the user's message and record "
        "what it means by calling record_intent exactly once. To complete or drop a task, "
        "pick the matching task_id from the current backlog. For a question or a blocker "
        "conversation, use action=answer and write a short, plain reply (no cheerleading, "
        "no em dashes). Current backlog:\n" + _task_lines(tasks)
    )
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=400,
        system=system,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "record_intent"},
        messages=[{"role": "user", "content": message}],
    )
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return parse_intent(block.input)
    return Intent(action="answer", reply="Sorry, I did not catch that. Try again?")


def _text_of(response) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""


def write_focus(focus: DailyFocus, now: datetime, client) -> str:
    if focus.focus is None:
        return "Nothing on the list today. Enjoy it."
    age = (now - focus.focus.created_at).days
    rescues = "; ".join(f"{t.text} ({(now - t.created_at).days}d old)" for t in focus.rescues) or "none"
    system = (
        "You are Jolt. Write a short morning message (2 to 4 lines, no em dashes). Name the "
        "one focus task as the single thing to do today. If there are rescue tasks that have "
        "gone stale, mention them and ask what is blocking them. Be plain and direct, never "
        "guilt-tripping."
    )
    user = f"Focus task: {focus.focus.text} ({age}d old). Rescues: {rescues}."
    return _text_of(client.messages.create(
        model=config.MODEL, max_tokens=300, system=system,
        messages=[{"role": "user", "content": user}],
    ))


def write_nag(task: Task, now: datetime, client) -> str:
    age = (now - task.created_at).days
    system = (
        "You are Jolt. Write one short nag (1 to 2 lines, no em dashes) about the task below. "
        "The older it is and the later in the day, the more direct and blunt you get: gentle in "
        "the morning, pointed by evening. If it is several days old, first ask what is actually "
        "blocking it before pushing. Never guilt-trip."
    )
    user = f"Task: {task.text}. Age: {age} days. Current hour: {now.hour}."
    return _text_of(client.messages.create(
        model=config.MODEL, max_tokens=200, system=system,
        messages=[{"role": "user", "content": user}],
    ))
