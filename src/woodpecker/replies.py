"""Deterministic answers to the one-letter replies (d, o, t, x, s, n) and the weekly review
forms (x 1 3, w 2). No Claude call: the letter is parsed here and applied through the
Store, against the last prompt the bot sent (the sidecar's open_prompt). answer() returns
None for any text that is not such a reply, so the caller sends it to the free-text flow."""

import logging
import re
from datetime import datetime, timedelta

from . import config, render

logger = logging.getLogger(__name__)

# The kinds of open prompt a letter can answer.
FROG = "frog"
REFRAME = "reframe"
WEEKLY = "weekly"
STEP = "step"  # after "s": the next message is the smaller step

NOTHING_OPEN = "Nothing to answer right now."
NOT_FOUND = "Couldn't find that one."
WEEKLY_HINT = "For the review, reply like x 1 3 or w 2."

_ALLOWED = {FROG: {"d", "o", "t", "x"}, REFRAME: {"s", "n", "x"}}
_LEGEND = {FROG: render.FROG_LEGEND, REFRAME: render.REFRAME_LEGEND}
_LETTERS = {"d", "o", "t", "x", "s", "n"}
_WEEKLY_FORM = re.compile(r"^(?:[xw](?:\s+\d+)+\s*)+$")
_WEEKLY_GROUP = re.compile(r"([xw])((?:\s+\d+)+)")


def answer(store, text: str, now: datetime) -> str | None:
    prompt = store.get_open_prompt()
    if prompt is not None and prompt.kind == STEP:
        reply = _smaller_step(store, prompt, text.strip(), now)
        if reply is not None:
            return reply
        prompt = None  # the step window closed; read the text as usual
    reply = text.strip().lower()
    weekly = bool(_WEEKLY_FORM.match(reply))
    if reply not in _LETTERS and not weekly:
        return None
    if prompt is None:
        return NOTHING_OPEN
    if prompt.kind == WEEKLY:
        return _weekly(store, prompt.task_ids, reply, now) if weekly else WEEKLY_HINT
    if weekly or reply not in _ALLOWED[prompt.kind]:
        return f"Reply with one letter: {_LEGEND[prompt.kind]}"
    return _letter(store, prompt, reply, now)


def _letter(store, prompt, letter: str, now: datetime) -> str:
    task_id = prompt.task_ids[0]
    day = prompt.sent_at.date()
    if letter == "d":
        task = store.complete_task(task_id, now)
        _close(store, task_id, day, "d")
        return "Done, nice." if task else NOT_FOUND
    if letter == "o":
        store.mark_frog_started(day, now)
        logger.info("reply %s o", task_id)
        return "Good. One step, then see how it goes."
    if letter == "t":
        task = store.reschedule_task(task_id, now.date() + timedelta(days=1), None)
        store.mark_frog_answered(day, "t")
        logger.info("reply %s t", task_id)
        return "Moved to tomorrow." if task else NOT_FOUND
    if letter == "s":
        store.set_open_prompt(STEP, [task_id], now)
        logger.info("reply %s s", task_id)
        return "What is the smaller step? Send it and it becomes the task."
    return _drop(store, prompt, letter, now)  # "x" or "n"


def _drop(store, prompt, letter: str, now: datetime) -> str:
    task_id = prompt.task_ids[0]
    window = timedelta(minutes=config.DROP_CONFIRM_MINUTES)
    if prompt.pending_drop_at is not None and now - prompt.pending_drop_at <= window:
        ok = store.drop_task(task_id)
        _close(store, task_id, prompt.sent_at.date(), letter)
        return "Dropped." if ok else NOT_FOUND
    task = store.get_task(task_id)
    if task is None:
        store.clear_open_prompt()
        return NOT_FOUND
    store.set_pending_drop(now)
    if letter == "n":
        return f'Not yours? Send n again to drop "{task.text}".'
    return f'Drop "{task.text}"? Send x again.'


def _close(store, task_id: int, day, letter: str) -> None:
    store.mark_frog_answered(day, letter)
    store.clear_open_prompt()
    logger.info("reply %s %s", task_id, letter)


def _smaller_step(store, prompt, text: str, now: datetime) -> str | None:
    store.clear_open_prompt()
    if now - prompt.sent_at > timedelta(minutes=config.STEP_ANSWER_MINUTES):
        return None
    task_id = prompt.task_ids[0]
    task = store.rename_task(task_id, text)
    store.clear_tomorrows(task_id)
    logger.info("reply %s rewrite", task_id)
    return f'Now the task is "{task.text}".' if task else NOT_FOUND


def _weekly(store, task_ids: list[int], reply: str, now: datetime) -> str:
    dropped, moved, missing = [], [], []
    for letter, numbers in _WEEKLY_GROUP.findall(reply):
        for n in (int(part) for part in numbers.split()):
            if not 1 <= n <= len(task_ids):
                missing.append(n)
                continue
            task_id = task_ids[n - 1]
            if letter == "x":
                store.drop_task(task_id)
                dropped.append(n)
            else:
                store.reschedule_task(task_id, now.date() + timedelta(days=7), None)
                moved.append(n)
            logger.info("reply %s weekly-%s", task_id, letter)
    if dropped or moved:
        store.clear_open_prompt()
    parts = []
    if dropped:
        parts.append(f"Dropped {', '.join(map(str, dropped))}.")
    if moved:
        parts.append(f"Moved {', '.join(map(str, moved))} to next week.")
    if missing:
        parts.append(f"No {', '.join(map(str, missing))} in the list.")
    return " ".join(parts)
