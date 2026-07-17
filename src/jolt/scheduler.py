import logging
from datetime import datetime

from . import llm
from .render import morning_shown, render_matters
from .selection import (
    is_quiet_hours,
    select_daily_focus,
    select_frog,
    select_slow_resurface,
)

logger = logging.getLogger(__name__)


def send_daily_focus(store, send, client, now: datetime, chat_id: int) -> None:
    logger.info("Daily focus job firing at %s", now.isoformat())
    try:
        tasks = store.list_pending()
    except Exception:
        logger.exception("Daily focus job aborted: could not load pending tasks")
        return
    frog = select_frog(tasks, now)
    logger.info("Selected frog task_id=%s", frog.id if frog else None)
    matters = render_matters(tasks, now)
    # Record the exact order shown, so a number the user types resolves against this
    # morning list, not a live order that later completions may have renumbered.
    store.save_display(chat_id, [t.id for t in morning_shown(tasks, now)])
    try:
        prose = llm.write_focus(frog, now, client)
    except Exception:
        logger.exception("Daily focus prose failed; sending the priorities with a plain lead")
        prose = "Morning. Here's what matters today."
    send(f"{prose}\n\n{matters}")


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
