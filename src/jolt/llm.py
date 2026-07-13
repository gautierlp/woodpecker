import logging
from dataclasses import dataclass, replace
from datetime import date, datetime

from . import config, render
from .models import DailyFocus, PRIORITY_IMPORTANT, STATUS_PENDING, Task
from .selection import nag_stance

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
                "description": "The backlog number shown for the existing task, for "
                "complete/drop/edit/block.",
            },
            "blocked_by": {
                "type": "integer",
                "description": "For action=block: the backlog number of the prerequisite task "
                "that must be finished first.",
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


def _numbered(display_ids: list[int] | None, tasks: list[Task], now: datetime) -> list[tuple]:
    # The (position, task) pairs the user is looking at. When we have a snapshot of the
    # last list shown (display_ids), number by that exact order so a reference resolves to
    # what the user saw, not a live order that may have shifted since. A snapshot task
    # that is no longer pending drops out, but the survivors keep their original numbers
    # (each line carries its number explicitly). With no snapshot, fall back to the live
    # display order.
    if display_ids is None:
        return list(enumerate(render.display_order(tasks, now), 1))
    by_id = {t.id: t for t in tasks}
    return [
        (pos, by_id[tid])
        for pos, tid in enumerate(display_ids, 1)
        if tid in by_id and by_id[tid].status == STATUS_PENDING
    ]


def _task_lines(display_ids: list[int] | None, tasks: list[Task], now: datetime) -> str:
    # Show Claude the exact position numbers the user sees and nothing else. The db id is
    # deliberately hidden: exposing both let Claude sometimes emit the position where an
    # id was expected. Claude echoes a position; code below translates it to the id.
    numbered = _numbered(display_ids, tasks, now)
    if not numbered:
        return "(backlog is empty)"
    return "\n".join(
        f"- {pos}: {t.text}"
        + (" [important]" if t.priority == "important" else "")
        + (f" (due {t.deadline.isoformat()})" if t.deadline else "")
        for pos, t in numbered
    )


def _resolve_positions(
    intents: list[Intent], display_ids: list[int] | None, tasks: list[Task], now: datetime
) -> list[Intent]:
    # Claude refers to tasks by the position numbers it was shown; the orchestrator and db
    # act on stable ids. Translate every position back to its id here, in deterministic
    # code, so a wrong translation can't happen (the earlier bug: Claude emitting a
    # position as if it were an id). Positions are read against the same snapshot the
    # prompt used, so numbers resolve to the list the user saw. A number with no matching
    # position maps to None, which the orchestrator reports as "couldn't find" rather than
    # hitting an unrelated task.
    if display_ids is None:
        pos_to_id = {pos: t.id for pos, t in enumerate(render.display_order(tasks, now), 1)}
    else:
        pos_to_id = {pos: tid for pos, tid in enumerate(display_ids, 1)}
    resolved = []
    for intent in intents:
        changes = {}
        if intent.task_id is not None:
            changes["task_id"] = pos_to_id.get(intent.task_id)
        if intent.blocked_by is not None:
            changes["blocked_by"] = pos_to_id.get(intent.blocked_by)
        resolved.append(replace(intent, **changes) if changes else intent)
    return resolved


def interpret_message(
    message: str,
    tasks: list[Task],
    now: datetime,
    client,
    history: list[dict] | None = None,
    recent_outbound: str | None = None,
    display_ids: list[int] | None = None,
) -> list[Intent]:
    system = (
        "You are Jolt, a personal accountability bot. Read the user's message and record "
        "what it means by calling record_intent. A single message can contain several things "
        "at once (for example a pasted list of tasks); call record_intent once per distinct "
        "task or action, never fold several tasks into one. When adding a task, judge its "
        "importance from the wording and stakes and set priority (normal / important); do not "
        "leave it blank, since the backlog is ranked by importance. In the backlog below each "
        "task is listed as 'number: text', where the number is what the user sees. To act on a "
        "task (complete, drop, edit, block), pass that exact number as task_id (and as "
        "blocked_by for a prerequisite): the user's '2' in 'complete 2' or '34 blocks 35' is "
        "that number. When the user names a task by its text instead of a number, find the "
        "matching line and use its number. When the user says one task must "
        "happen before another (for example 'X needs Y first', 'can't do X until Y', 'Y blocks X'), "
        "call record_intent with action=block, task_id = the task that is blocked and blocked_by = "
        "the prerequisite task's number; emit one block call per blocked task. To lift a dependency, use "
        "action=unblock with the task_id. "
        "To change an existing task rather than add a new one, use action=edit with its task_id "
        "and only the fields that change: a new deadline, a new priority (normal/important), or "
        "reworded text. Prefer edit over dropping and re-adding, so the task keeps its age. To "
        "remove a due date entirely, use action=edit with clear_deadline=true. A message like "
        "'change all due dates to today' becomes one edit call per pending task. "
        "You may be given earlier turns of this conversation before the latest message. Use them to "
        "resolve short follow-ups: if your previous turn offered to do something and the user replies "
        "'yes', 'do it', 'the first one' and the like, treat it as confirming that action and record "
        "the concrete intent (for example the block you proposed), not a contextless answer. "
        "For a question or a blocker conversation, use "
        "action=answer and write a short, plain reply (no cheerleading, no em dashes). The user may "
        "write in any language, but always store the task text in English (translate it if needed). "
        f"Today is {now:%A, %Y-%m-%d}. Resolve any relative deadline (today, tomorrow, next "
        "week, in 3 days) against today's date and record it as an ISO YYYY-MM-DD date. "
    )
    if recent_outbound:
        system += (
            "You recently sent the user this message on your own, which they have not "
            "answered until now, so their message is most likely a reply to it; resolve it "
            f"against this and act on the task it refers to:\n{recent_outbound}\n"
        )
    system += "Current backlog:\n" + _task_lines(display_ids, tasks, now)
    messages = list(history or [])
    messages.append({"role": "user", "content": message})
    logger.debug(
        "interpret_message: %d task(s) in store, %d prior turn(s) in context",
        len(tasks),
        len(messages) - 1,
    )
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=1000,
        system=system,
        tools=[_TOOL],
        # "any" forces at least one record_intent call but, unlike naming the tool,
        # still allows Claude to emit one call per task in a multi-task message.
        tool_choice={"type": "any"},
        messages=messages,
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
    intents = _resolve_positions(intents, display_ids, tasks, now)
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
        "low-stakes ones. If there are rescue tasks that have gone stale, mention them: for an "
        "important rescue ask what is blocking it, but for a low-stakes (normal) rescue lean the "
        "other way and ask whether it is still worth keeping or should just be dropped. Be plain "
        "and direct, never guilt-tripping."
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
    stance = nag_stance(task)
    if stance == "start":
        guidance = (
            "This task matters. Push toward starting it: first ask what is actually blocking "
            "it and offer to break it down into a small first step. The older it is and the "
            "later in the day, the blunter and more insistent you get, up to a flat 'do it or "
            "delete it' by evening."
        )
    else:
        guidance = (
            "This task is low-stakes and has been sitting untouched. Do not chase it to get "
            "done. Instead nudge toward dropping it with a zero-based question: if it were not "
            "already on the list, would they add it today? Still want it, or drop it? Stay "
            "light and easy to wave off."
        )
    system = (
        "You are Jolt. Write one short nag (1 to 2 lines, no em dashes) about the task below. "
        + guidance
        + " Never guilt-trip."
    )
    user = (
        f"Task: {task.text}. Importance: {_importance(task)}. Age: {age} days. "
        f"Current hour: {now.hour}."
    )
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=200,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    _log_usage("write_nag", response)
    return _text_of(response)
