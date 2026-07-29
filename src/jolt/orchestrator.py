import logging
from datetime import datetime

from .llm import Intent
from .models import PRIORITY_NORMAL
from .render import render_backlog

logger = logging.getLogger(__name__)


def apply_intent(store, intent: Intent, now: datetime) -> str:
    logger.info("Applying intent action=%s task_id=%s", intent.action, intent.task_id)
    if intent.action == "add":
        task = store.add_task(intent.text, intent.priority or PRIORITY_NORMAL, intent.deadline, now)
        ack = f'Got it. "{task.text}" saved.'
        if task.deadline:
            ack += f" Due {task.deadline.isoformat()}."
        return ack
    if intent.action == "complete":
        task = store.complete_task(intent.task_id, now)
        return "Done, nice." if task else "Couldn't find that one."
    if intent.action == "drop":
        ok = store.drop_task(intent.task_id)
        return "Dropped." if ok else "Couldn't find that one."
    if intent.action == "reschedule":
        task = store.reschedule_task(intent.task_id, intent.deadline, intent.priority)
        if task is None:
            return "Couldn't find that one."
        if intent.deadline:
            return f'Moved. "{task.text}" now due {task.deadline.isoformat()}.'
        return f'Bumped up. "{task.text}" is marked important.'
    if intent.action == "list":
        return render_backlog(store.list_pending(), now)
    return intent.reply or "Not sure what you mean. Try rephrasing?"
