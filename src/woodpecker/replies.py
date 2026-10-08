"""Deterministic answers to the one-letter replies (d, o, t, x, s, n). No Claude call: the
letter is parsed here and applied through the Store, against the last prompt the bot sent
(the sidecar's open_prompt). answer() returns None for any text that is not such a reply,
so the caller sends it to the free-text flow."""

import logging
from datetime import datetime, timedelta

from . import config, render

logger = logging.getLogger(__name__)

# The kinds of open prompt a letter can answer.
FROG = "frog"
REFRAME = "reframe"
STEP = "step"  # after "s": the next message is the smaller step

NOTHING_OPEN = "Nothing to answer right now."
NOT_FOUND = "Couldn't find that one."

_ALLOWED = {FROG: {"d", "o", "t", "x"}, REFRAME: {"s", "n", "x"}}
_LEGEND = {FROG: render.FROG_LEGEND, REFRAME: render.REFRAME_LEGEND}
_LETTERS = {"d", "o", "t", "x", "s", "n"}


def answer(store, text: str, now: datetime) -> str | None:
    prompt = store.get_open_prompt()
    reply = text.strip().lower()
    if prompt is not None and prompt.kind == STEP:
        if reply not in _LETTERS:
            step = _smaller_step(store, prompt, text.strip(), now)
            if step is not None:
                return step
        # A letter is not a step: close the window, read the text as usual.
        store.clear_open_prompt()
        prompt = None
    if reply not in _LETTERS:
        return None
    if prompt is None:
        return NOTHING_OPEN
    if reply not in _ALLOWED[prompt.kind]:
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
    if now - prompt.sent_at > timedelta(minutes=config.STEP_ANSWER_MINUTES):
        return None
    task_id = prompt.task_ids[0]
    task = store.rename_task(task_id, text)
    # Close only after the rename: if Vikunja fails, the retry is still the step.
    store.clear_open_prompt()
    store.clear_tomorrows(task_id)
    logger.info("reply %s rewrite", task_id)
    return f'Now the task is "{task.text}".' if task else NOT_FOUND
