import logging
from datetime import datetime

from . import db
from .llm import Intent
from .models import PRIORITY_NORMAL, STATUS_PENDING
from .render import render_backlog

logger = logging.getLogger(__name__)


def _would_cycle(conn, task_id: int, blocked_by: int) -> bool:
    """True if making task_id wait on blocked_by would form a loop. Walks the
    blocked_by chain up from the proposed blocker; a loop exists if it leads back
    to task_id."""
    seen: set[int] = set()
    cursor = blocked_by
    while cursor is not None and cursor not in seen:
        if cursor == task_id:
            return True
        seen.add(cursor)
        task = db.get_task(conn, cursor)
        cursor = task.blocked_by if task else None
    return False


def apply_intent(conn, intent: Intent, now: datetime) -> str:
    logger.info("Applying intent action=%s task_id=%s", intent.action, intent.task_id)
    if intent.action == "add":
        task = db.add_task(
            conn, intent.text, intent.priority or PRIORITY_NORMAL, intent.deadline, now
        )
        logger.info(
            "Added task id=%s priority=%s deadline=%s", task.id, task.priority, task.deadline
        )
        ack = f'Got it. "{task.text}" saved.'
        if task.deadline:
            ack += f" Due {task.deadline.isoformat()}."
        return ack
    if intent.action == "complete":
        task = db.complete_task(conn, intent.task_id, now)
        logger.info("Complete task_id=%s: %s", intent.task_id, "ok" if task else "not found")
        return "Done, nice." if task else "Couldn't find that one."
    if intent.action == "drop":
        task = db.drop_task(conn, intent.task_id)
        logger.info("Drop task_id=%s: %s", intent.task_id, "ok" if task else "not found")
        return "Dropped." if task else "Couldn't find that one."
    if intent.action == "edit":
        if (
            intent.text is None
            and intent.priority is None
            and intent.deadline is None
            and not intent.clear_deadline
        ):
            # The model emits a change-less edit mainly when the user reports part of a
            # compound task done ("shower is done" on "Groom Rex: shower, wash ears,
            # brush teeth"). We can't tick off part of a task, so be honest and offer the
            # two things we can do, rather than the old dead-end "Nothing to change."
            return (
                "I can only change a task as a whole, not tick off part of one. Tell me the new "
                "wording if you want it trimmed down, or say it's fully done."
            )
        if intent.deadline is not None:
            task = db.update_task(
                conn,
                intent.task_id,
                text=intent.text,
                priority=intent.priority,
                deadline=intent.deadline,
            )
        elif intent.clear_deadline:
            task = db.update_task(
                conn, intent.task_id, text=intent.text, priority=intent.priority, deadline=None
            )
        else:
            task = db.update_task(conn, intent.task_id, text=intent.text, priority=intent.priority)
        logger.info("Edit task_id=%s: %s", intent.task_id, "ok" if task else "not found")
        if task is None:
            return "Couldn't find that one."
        ack = "Updated."
        if intent.deadline is not None:
            ack += f" Due {task.deadline.isoformat()}."
        elif intent.clear_deadline:
            ack += " Due date removed."
        return ack
    if intent.action == "block":
        if intent.task_id == intent.blocked_by:
            return "A task can't block itself."
        blocked = db.get_task(conn, intent.task_id)
        blocker = db.get_task(conn, intent.blocked_by)
        if (
            blocked is None
            or blocked.status != STATUS_PENDING
            or blocker is None
            or blocker.status != STATUS_PENDING
        ):
            logger.info("Block rejected: could not find both pending tasks")
            return "Couldn't find those tasks."
        if _would_cycle(conn, intent.task_id, intent.blocked_by):
            logger.info("Block rejected: would create a cycle")
            return "That would create a loop, so I left it alone."
        db.block_task(conn, intent.task_id, intent.blocked_by)
        logger.info("Blocked task_id=%s on %s", intent.task_id, intent.blocked_by)
        return f'Noted: "{blocked.text}" waits on "{blocker.text}" first.'
    if intent.action == "unblock":
        task = db.unblock_task(conn, intent.task_id)
        logger.info("Unblock task_id=%s: %s", intent.task_id, "ok" if task else "not found")
        return "Unblocked." if task else "Couldn't find that one."
    if intent.action == "merge":
        if intent.task_id == intent.merge_from:
            return "Can't merge a task with itself."
        survivor = db.get_task(conn, intent.task_id)
        other = db.get_task(conn, intent.merge_from)
        if (
            survivor is None
            or survivor.status != STATUS_PENDING
            or other is None
            or other.status != STATUS_PENDING
        ):
            logger.info("Merge rejected: could not find both pending tasks")
            return "Couldn't find those tasks."
        text = intent.text or f"{survivor.text} and {other.text}"
        merged = db.merge_tasks(conn, intent.task_id, intent.merge_from, text)
        logger.info("Merged task_id=%s from %s", intent.task_id, intent.merge_from)
        return f'Merged into "{merged.text}".'
    if intent.action == "list":
        return render_backlog(db.list_all(conn), now)
    return intent.reply or "Not sure what you mean. Try rephrasing?"
