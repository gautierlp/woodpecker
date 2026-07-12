from datetime import datetime

from . import db
from .llm import Intent
from .models import PRIORITY_NORMAL
from .render import render_backlog


def apply_intent(conn, intent: Intent, now: datetime) -> str:
    if intent.action == "add":
        task = db.add_task(conn, intent.text, intent.priority or PRIORITY_NORMAL, intent.deadline, now)
        ack = f'Got it. "{task.text}" saved.'
        if task.deadline:
            ack += f" Due {task.deadline.isoformat()}."
        return ack
    if intent.action == "complete":
        task = db.complete_task(conn, intent.task_id, now)
        return "Done, nice." if task else "Couldn't find that one."
    if intent.action == "drop":
        task = db.drop_task(conn, intent.task_id)
        return "Dropped." if task else "Couldn't find that one."
    if intent.action == "list":
        return render_backlog(db.list_all(conn), now)
    return intent.reply or "Not sure what you mean. Try rephrasing?"
