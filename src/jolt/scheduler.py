import logging
from datetime import datetime

from . import db, llm
from .render import display_order, render_backlog
from .selection import is_quiet_hours, select_daily_focus, select_slow_resurface

logger = logging.getLogger(__name__)


def send_daily_focus(conn, send, client, now: datetime, chat_id: int) -> None:
    logger.info("Daily focus job firing at %s", now.isoformat())
    tasks = db.list_all(conn)
    focus = select_daily_focus(tasks, now)
    logger.info("Selected focus task_id=%s", focus.focus.id if focus.focus else None)
    prose = llm.write_focus(focus, now, client)
    backlog = render_backlog(tasks, now)
    # Record the exact order shown, so a number the user types after the focus resolves
    # against this list, not a live order that later completions may have renumbered.
    db.save_display(conn, chat_id, [t.id for t in display_order(tasks, now)])
    send(f"{prose}\n\n{backlog}")


def send_nags(conn, send, client, now: datetime) -> None:
    logger.info("Nag job firing at %s", now.isoformat())
    if is_quiet_hours(now):
        logger.info("Skipping nag: quiet hours")
        return
    tasks = db.list_all(conn)
    focus = select_daily_focus(tasks, now)
    focus_id = focus.focus.id if focus.focus else None
    if focus.focus is not None:
        logger.info("Nagging about focus task_id=%s", focus.focus.id)
        send(llm.write_nag(focus.focus, now, client))
        db.mark_nagged(conn, focus.focus.id, now)
    tadpole = select_slow_resurface(tasks, now, exclude_id=focus_id)
    if tadpole is not None:
        logger.info("Slow re-surface of task_id=%s", tadpole.id)
        send(llm.write_nag(tadpole, now, client))
        db.mark_nagged(conn, tadpole.id, now)
    if focus.focus is None and tadpole is None:
        logger.info("Skipping nag: nothing to nag about")
