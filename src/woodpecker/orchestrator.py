import logging
from datetime import datetime

from .llm import Intent
from .models import PRIORITY_NORMAL
from .render import render_backlog

logger = logging.getLogger(__name__)


NOT_FOUND = "Couldn't find that one."


def apply_intent(store, intent: Intent, now: datetime) -> str:
    return apply(store, intent, now)[0]


def apply(store, intent: Intent, now: datetime) -> tuple[str, bool]:
    """Apply one intent. Returns the reply and whether the store made the change, so the
    bot can tell a real "done" on the frog from a task it could not find."""
    logger.info("Applying intent action=%s task_id=%s", intent.action, intent.task_id)
    if intent.action == "add":
        task = store.add_task(intent.text, intent.priority or PRIORITY_NORMAL, intent.deadline, now)
        ack = f'Got it. "{task.text}" saved.'
        if task.deadline:
            ack += f" Due {task.deadline.isoformat()}."
        return ack, True
    if intent.action == "complete":
        task = store.complete_task(intent.task_id, now)
        return ("Done, nice.", True) if task else (NOT_FOUND, False)
    if intent.action == "drop":
        ok = store.drop_task(intent.task_id)
        return ("Dropped.", True) if ok else (NOT_FOUND, False)
    if intent.action == "reschedule":
        task = store.reschedule_task(intent.task_id, intent.deadline, intent.priority)
        if task is None:
            return NOT_FOUND, False
        if intent.deadline:
            return f'Moved. "{task.text}" now due {task.deadline.isoformat()}.', True
        return f'Bumped up. "{task.text}" is marked important.', True
    if intent.action == "list":
        return render_backlog(store.list_pending(), now), True
    return intent.reply or "Not sure what you mean. Try rephrasing?", False
