import logging
from datetime import datetime

from . import llm
from .render import display_order, render_backlog
from .selection import is_quiet_hours, select_daily_focus, select_slow_resurface

logger = logging.getLogger(__name__)


def send_daily_focus(store, send, client, now: datetime, chat_id: int) -> None:
    logger.info("Daily focus job firing at %s", now.isoformat())
    try:
        tasks = store.list_pending()
    except Exception:
        logger.exception("Daily focus job aborted: could not load pending tasks")
        return
    focus = select_daily_focus(tasks, now)
    logger.info("Selected focus task_id=%s", focus.focus.id if focus.focus else None)
    backlog = render_backlog(tasks, now)
    # Record the exact order shown, so a number the user types after the focus resolves
    # against this list, not a live order that later completions may have renumbered.
    store.save_display(chat_id, [t.id for t in display_order(tasks, now)])
    try:
        prose = llm.write_focus(focus, now, client)
    except Exception:
        logger.exception("Daily focus prose failed; sending the backlog with a plain lead")
        prose = "Morning. I couldn't write today's lead, but here's where things stand."
    send(f"{prose}\n\n{backlog}")


def send_nags(store, send, client, now: datetime) -> None:
    logger.info("Nag job firing at %s", now.isoformat())
    if is_quiet_hours(now):
        logger.info("Skipping nag: quiet hours")
        return
    try:
        tasks = store.list_pending()
        focus = select_daily_focus(tasks, now)
        focus_id = focus.focus.id if focus.focus else None
        tadpole = select_slow_resurface(tasks, now, exclude_id=focus_id)
    except Exception:
        logger.exception("Nag job aborted: could not load pending tasks")
        send("I tried to nudge you but something on my end broke. I'll try again next time.")
        return
    failed = False

    def _nag(task):
        nonlocal failed
        try:
            send(llm.write_nag(task, now, client))
            store.mark_nagged(task.id, now)
        except Exception:
            logger.exception("Nag failed for task_id=%s", task.id)
            failed = True

    if focus.focus is not None:
        logger.info("Nagging about focus task_id=%s", focus.focus.id)
        _nag(focus.focus)
    if tadpole is not None:
        logger.info("Slow re-surface of task_id=%s", tadpole.id)
        _nag(tadpole)
    if focus.focus is None and tadpole is None:
        logger.info("Skipping nag: nothing to nag about")
    elif failed:
        send("I tried to nudge you but something on my end broke. I'll try again next time.")
