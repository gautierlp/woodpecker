import logging
from dataclasses import dataclass, replace
from datetime import date, datetime

from anthropic import Anthropic

from . import config, render
from .models import STATUS_PENDING, Task
from .selection import BAND_HIGH, order_backlog, priority_band
from .vault import VaultTask, short_text

logger = logging.getLogger(__name__)


def build_client(api_key: str) -> Anthropic:
    """The Anthropic client, with an explicit short timeout. Calls run on the bot's single
    event loop, so without a tight timeout one hung request would freeze polling and every
    scheduled job for the SDK's ~10-minute default."""
    return Anthropic(api_key=api_key, timeout=config.ANTHROPIC_TIMEOUT_SECONDS)


def _log_usage(label: str, response) -> None:
    """Record token usage, dollar cost and stop reason for one Claude call, so spend and
    truncation issues are visible in the logs (and the cost can be summed over time)."""
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if isinstance(input_tokens, int) and isinstance(output_tokens, int):
        cost = f"${config.call_cost_usd(input_tokens, output_tokens):.5f}"
    else:
        cost = "?"
    logger.info(
        "Claude %s: stop=%s in=%s out=%s cost=%s",
        label,
        getattr(response, "stop_reason", "?"),
        input_tokens if input_tokens is not None else "?",
        output_tokens if output_tokens is not None else "?",
        cost,
    )


def _format_duration(seconds: int | None) -> str | None:
    if not seconds:
        return None
    if seconds < 60:
        return "<1 min"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes / 60
    return f"{int(hours)}h" if hours == int(hours) else f"{hours:.1f}h"


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


# strict=False on purpose. Strict tool use constrains generation to the schema grammar,
# and in practice that made the model stop populating optional fields on a reschedule: it
# would emit {"action": "reschedule", "task_id": N} and silently drop the new deadline into
# "reply" or nowhere, so the deadline change was lost.
# Both Haiku and Sonnet failed the same way under strict and both work with it off, so this
# is the schema, not the model. We do not need the grammar guarantee: parse_intent already
# survives a malformed block and _resolve_positions handles out-of-range numbers.
_TOOL = {
    "name": "record_intent",
    "description": "Record what the user's Telegram message means for their task list.",
    "strict": False,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "complete", "drop", "reschedule", "list", "answer"],
                "description": "add a task, complete/drop an existing one, reschedule an "
                "existing one (move its due date or make it important), list the backlog, "
                "or answer a question / reply to the user.",
            },
            "text": {
                "type": "string",
                "description": "The task's wording. This is the ONLY field a task's text ever goes "
                "in, never reply. For action=add, the new task.",
            },
            "priority": {
                "type": "string",
                "enum": ["normal", "important"],
                "description": "For action=add: always judge this from the wording and stakes, never leave it blank. 'important' for anything with real consequences (a deadline, money, health, admin/legal weight); 'normal' otherwise. For action=reschedule: set 'important' only when the user is raising the task's urgency.",
            },
            "deadline": {
                "type": "string",
                "description": "The task's due date as an ISO YYYY-MM-DD date, for action=add "
                "or action=reschedule. "
                "Fill this whenever the user gives a deadline, including relative "
                "ones (today, tomorrow, Wednesday, next week): resolve them to an ISO date. "
                "Leave unset only when there is no deadline.",
            },
            "task_id": {
                "type": "integer",
                "description": "The backlog number shown for the existing task, for "
                "complete/drop/reschedule.",
            },
            "reply": {
                "type": "string",
                "description": "For action=answer ONLY: the exact message to send back to the "
                "user. Never put a task's wording here; that always goes in text.",
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
        + (" [important]" if priority_band(t) == BAND_HIGH else "")
        + (f" (due {t.deadline.isoformat()})" if t.deadline else "")
        for pos, t in numbered
    )


# Which position-numbered id fields each action actually acts on. Anything not listed is
# noise the model sometimes sprays onto an intent (a stray task_id on an add). We only
# validate and resolve the fields the action uses; validating the rest would reject a
# perfectly good intent over a number it was never going to touch. Actions absent from
# this map (add, list, answer) use no position number.
_ID_FIELDS = {
    "complete": ("task_id",),
    "drop": ("task_id",),
    "reschedule": ("task_id",),
}
_ALL_ID_FIELDS = ("task_id",)


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
        used = _ID_FIELDS.get(intent.action, ())
        # Clear the id fields this action does not use, so a stray number the model tacked
        # on can neither derail resolution nor leak into the orchestrator.
        cleared = {f: None for f in _ALL_ID_FIELDS if f not in used}
        positions = {f: getattr(intent, f) for f in used if getattr(intent, f) is not None}
        missing = [n for n in positions.values() if n not in pos_to_id]
        if missing:
            numbers = " or ".join(dict.fromkeys(str(n) for n in missing))
            logger.warning(
                "Task number(s) %s not on the last list shown; asking to clarify", missing
            )
            resolved.append(
                replace(
                    intent,
                    action="answer",
                    reply=f"I don't have a task numbered {numbers} on the current list. "
                    "Send 'list' to see the current numbers.",
                    task_id=None,
                )
            )
            continue
        changes = {**cleared, **{f: pos_to_id[n] for f, n in positions.items()}}
        resolved.append(replace(intent, **changes))
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
        "You are Woodpecker, a personal accountability bot. Read the user's message and record "
        "what it means by calling record_intent. A single message can contain several things "
        "at once (for example a pasted list of tasks); call record_intent once per distinct "
        "task or action. When adding tasks, never fold several into one add. "
        "When adding a task, judge its "
        "importance from the wording and stakes and set priority (normal / important); do not "
        "leave it blank, since the backlog is ranked by importance. In the backlog below each "
        "task is listed as 'number: text', where the number is what the user sees. To act on a "
        "task (complete, drop, reschedule), pass that exact number as task_id: the user's '2' in "
        "'complete 2' is that number. "
        "When the user gives a new date or a new urgency for a task that is ALREADY in the "
        "backlog, that is action=reschedule, never action=add. Shorthand like '2 -> tomorrow', "
        "'3 to friday', 'push 4 to next week', 'move this to monday' or '5 -> urgent' means "
        "reschedule the task at that number: put the resolved date in deadline, or set "
        "priority=important when the user is raising urgency rather than naming a date. "
        "Only use action=add when the task is not in the backlog yet. Adding a second copy of a "
        "task that is already listed is always wrong. "
        "These numbers are only valid for the backlog shown right "
        "now: the list is renumbered whenever a task is completed or added, so a number that "
        "appeared in an earlier turn of this conversation may now point to a different task. "
        "Never reinterpret or quote a number from an earlier turn against the current backlog; "
        "when you refer back to something discussed earlier, name the task by its text, not by "
        "its number. "
        "When the user describes a task in words rather than typing a number, match it to the "
        "backlog line by its text and use that line's number; only treat a bare number as task_id "
        "when the user actually typed that number. "
        "You may be given earlier turns of this conversation before the latest message. Use them to "
        "resolve short follow-ups: if your previous turn offered to do something and the user replies "
        "'yes', 'do it', 'the first one' and the like, treat it as confirming that action and record "
        "the concrete intent, not a contextless answer. "
        "For a question or any other conversation, use "
        "action=answer and write a short, plain reply, no cheerleading. The user may "
        "write in any language, but always store the task text in English (translate it if needed). "
        "Never use an em dash (the '—' character) in your reply. Use a comma, a colon, or a period "
        "instead. This rule has no exceptions. "
        "Vocabulary: the user's 'frog' is the single most important thing to do today, the one "
        "lead task to hit first; a 'tadpole' is a low-value task that has been sitting and is a "
        "candidate to drop rather than chase. When the user asks what their frog is or what they "
        "should do today, answer with action=answer, naming the task from the backlog that best "
        "fits (lead with the most important, and prefer one that has gone stale). "
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
    if recent_outbound and messages:
        # The pending nag/focus is the freshest thing Woodpecker said, but it lives outside
        # `history`. Without it in the transcript, a reply like "what are you talking
        # about?" resolves against the last handled turn (often a stale backlog) and Woodpecker
        # answers about the wrong thing. Add it as the most recent assistant turn so the
        # transcript reflects what Woodpecker actually last said. The API merges consecutive
        # same-role messages, so this is safe even when `history` already ends with an
        # assistant turn. Skip it when there is no history: the message list must start
        # with a user turn, and with no prior turns there is no stale transcript for the
        # nag to override (the system-prompt copy above already carries it).
        messages.append({"role": "assistant", "content": recent_outbound})
    messages.append({"role": "user", "content": message})
    logger.debug(
        "interpret_message: %d task(s) in store, %d prior turn(s) in context",
        len(tasks),
        len(messages) - 1,
    )
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=config.INTERPRET_MAX_TOKENS,
        system=system,
        tools=[_TOOL],
        # "any" forces at least one record_intent call but, unlike naming the tool,
        # still allows Claude to emit one call per task in a multi-task message.
        tool_choice={"type": "any"},
        messages=messages,
    )
    _log_usage("interpret_message", response)
    intents = []
    for block in response.content:
        if getattr(block, "type", None) != "tool_use":
            continue
        try:
            intents.append(parse_intent(block.input))
        except (ValueError, KeyError, TypeError) as exc:
            # One malformed block (a non-ISO date, a missing field) must not sink the rest of
            # a multi-task message. Degrade just this item to a clarification.
            logger.warning("Could not parse an intent block (%s): %r", exc, block.input)
            intents.append(
                Intent(
                    action="answer",
                    reply="I couldn't make sense of part of that. Try rephrasing it?",
                )
            )
    truncated = getattr(response, "stop_reason", None) == "max_tokens"
    if truncated:
        logger.warning("interpret_message hit max_tokens; response may be truncated")
    if not intents:
        if truncated:
            return [Intent(action="answer", reply=_TRUNCATION_NOTE)]
        logger.warning("Claude returned no tool_use block; falling back to clarification reply")
        return [Intent(action="answer", reply="Sorry, I did not catch that. Try again?")]
    intents = _resolve_positions(intents, display_ids, tasks, now)
    if truncated:
        intents.append(Intent(action="answer", reply=_TRUNCATION_NOTE))
    return intents


_TRUNCATION_NOTE = "That message was too long for me to capture all at once. Please re-send anything that didn't land."
_EMPTY_REPLY_FALLBACK = "I hit a snag putting that into words. Try me again in a moment."


def _text_of(response) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    logger.warning("Claude returned no text block; using fallback text")
    return _EMPTY_REPLY_FALLBACK


_NO_EM_DASH = (
    "Never use em dashes; use a comma, a colon, or a period instead. This rule has no exceptions."
)


_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _focus_context(frog: Task, now: datetime, others, vault_lines) -> str:
    """What Claude needs for a specific first step: the day, the other open tasks and the
    vault notes of the morning. A title alone gave a generic step on 2026-10-09."""
    lines = [f"Today: {_DAYS[now.weekday()]} {now.date().isoformat()}."]
    titles = [t.text for t in order_backlog(list(others)) if t.id != frog.id]
    if titles:
        lines.append("Other open tasks:")
        lines += [f"- {text}" for text in titles[: config.FOCUS_MAX_OTHER_TASKS]]
    if vault_lines:
        lines.append("Vault notes:")
        lines += [f"- {short_text(v.text)} ({v.note})" for v in vault_lines]
    return "\n".join(lines)


def write_focus(
    frog: Task,
    now: datetime,
    client,
    others: list[Task] = (),
    vault_lines: list[VaultTask] = (),
) -> str:
    """The morning lead: the frog in plain words plus one first step that takes under 10
    minutes. No guilt and no count of days avoided: guilt made Gautier close the message."""
    system = (
        "You are Woodpecker. Write exactly two short lines about the ONE task below. "
        "Line 1: name the task plainly as today's one thing. "
        "Line 2: start with 'First step:' and give one concrete action that takes under "
        "10 minutes, for example 'open the Doctolib page', not 'book the doctor'. "
        "Build the step from the context: the other open tasks and the vault notes often "
        "hold the real next step. Do not invent a tool, site or place that the context does "
        "not name. If the context gives nothing specific, the step is to write down what "
        "the task needs. "
        "No guilt, no count of days, no mention of how often it was put off. " + _NO_EM_DASH
    )
    bits = [f"Task: {frog.text}"]
    duration = _format_duration(frog.estimate_seconds)
    if duration:
        bits.append(f"est {duration}")
    if frog.project_name:
        bits.append(f"in {frog.project_name}")
    details_line = f" Notes: {frog.details}." if frog.details else ""
    user = ", ".join(bits) + "." + details_line
    user += "\n\n" + _focus_context(frog, now, others, vault_lines)
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=200,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    _log_usage("write_focus", response)
    return _text_of(response)


def write_reframe(frog: Task, client) -> str:
    """The question for a frog moved to tomorrow REFRAME_AFTER_BUMPS times: the task is
    probably wrong as written, so ask about its size or its owner instead of nagging."""
    system = (
        "You are Woodpecker. The task below was moved to tomorrow three times. Write one or "
        "two short lines, without blame, that ask whether it is too big as written or not "
        "really the user's to do. Do not list options: the bot adds them after your text. "
        + _NO_EM_DASH
    )
    response = client.messages.create(
        model=config.MODEL,
        max_tokens=150,
        system=system,
        messages=[{"role": "user", "content": f"Task: {frog.text}."}],
    )
    _log_usage("write_reframe", response)
    return _text_of(response)
