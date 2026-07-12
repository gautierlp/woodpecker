from datetime import datetime

from . import db, llm
from .render import render_backlog
from .selection import is_quiet_hours, select_daily_focus


def send_daily_focus(conn, send, client, now: datetime) -> None:
    tasks = db.list_all(conn)
    focus = select_daily_focus(tasks, now)
    prose = llm.write_focus(focus, now, client)
    backlog = render_backlog(tasks, now)
    send(f"{prose}\n\n{backlog}")


def send_nags(conn, send, client, now: datetime) -> None:
    if is_quiet_hours(now):
        return
    tasks = db.list_all(conn)
    focus = select_daily_focus(tasks, now)
    if focus.focus is None:
        return
    send(llm.write_nag(focus.focus, now, client))
    db.mark_nagged(conn, focus.focus.id, now)
