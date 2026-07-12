import logging
from datetime import datetime

from . import db, llm
from .render import render_backlog
from .selection import is_quiet_hours, select_daily_focus

logger = logging.getLogger(__name__)


def send_daily_focus(conn, send, client, now: datetime) -> None:
    logger.info("Daily focus job firing at %s", now.isoformat())
    tasks = db.list_all(conn)
    focus = select_daily_focus(tasks, now)
    logger.info("Selected focus task_id=%s", focus.focus.id if focus.focus else None)
    prose = llm.write_focus(focus, now, client)
    backlog = render_backlog(tasks, now)
    send(f"{prose}\n\n{backlog}")


def send_nags(conn, send, client, now: datetime) -> None:
    logger.info("Nag job firing at %s", now.isoformat())
    if is_quiet_hours(now):
        logger.info("Skipping nag: quiet hours")
        return
    tasks = db.list_all(conn)
    focus = select_daily_focus(tasks, now)
    if focus.focus is None:
        logger.info("Skipping nag: nothing to nag about")
        return
    logger.info("Nagging about task_id=%s", focus.focus.id)
    send(llm.write_nag(focus.focus, now, client))
    db.mark_nagged(conn, focus.focus.id, now)
