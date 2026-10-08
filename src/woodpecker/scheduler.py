import logging
from datetime import date, datetime
from pathlib import Path

from . import config, llm, render, vault
from .models import STATUS_PENDING
from .replies import FROG, REFRAME
from .selection import is_quiet_hours, select_frog, select_vault_due, select_vault_reminders

logger = logging.getLogger(__name__)


def _vault_part(
    vault_path: str, today: date, select, render_lines, report_unreadable: bool = True
) -> str:
    """The vault text of a message. In the morning a read error becomes one line: a missing
    mount must never silence the frog. The check-in passes report_unreadable=False: the
    morning already said it, and an error alone is no reason to send a check-in."""
    try:
        tasks = vault.read_vault(Path(vault_path))
    except (vault.VaultUnreadable, OSError) as exc:
        logger.warning("Vault unreadable at %s: %s", vault_path, exc)
        return render.render_vault_unreadable(str(exc)) if report_unreadable else ""
    return render_lines(select(tasks, today), today)


def _is_reframe(store, task_id: int) -> bool:
    return store.tomorrow_count(task_id) >= config.REFRAME_AFTER_BUMPS


def _pick_frog(store, tasks: list, now: datetime):
    """A task with its third "t" is the frog the next morning, even over an overdue one,
    so the reframe question comes at once. Oldest first if several; else select_frog."""
    pending = [t for t in tasks if t.status == STATUS_PENDING]
    reframes = [t for t in pending if _is_reframe(store, t.id)]
    if reframes:
        return min(reframes, key=lambda t: t.created_at)
    return select_frog(tasks, now)


def _frog_part(store, client, frog, now: datetime) -> str:
    store.record_frog(now.date(), frog.id)
    if _is_reframe(store, frog.id):
        try:
            lead = llm.write_reframe(frog, client)
        except Exception:
            logger.exception("Reframe prose failed; sending the plain question")
            lead = (
                f'"{frog.text}" keeps moving to tomorrow. Too big as written, or not yours to do?'
            )
        store.set_open_prompt(REFRAME, [frog.id], now)
        return f"{lead}\n\n{render.REFRAME_LEGEND}"
    try:
        lead = llm.write_focus(frog, now, client)
    except Exception:
        logger.exception("Morning prose failed; sending the plain line")
        lead = f'Today: "{frog.text}".'
    store.set_open_prompt(FROG, [frog.id], now)
    return f"{lead}\n\n{render.FROG_LEGEND}"


def send_morning(store, send, client, now: datetime, vault_path: str) -> None:
    logger.info("Morning job firing at %s", now.isoformat())
    if is_quiet_hours(now):
        logger.info("Skipping morning: quiet hours")
        return
    parts = []
    try:
        tasks = store.list_pending()
    except Exception:
        logger.exception("Morning job could not load pending tasks")
        store.clear_open_prompt()  # yesterday's frog must not take today's letters
        parts.append("My task list is unreachable this morning.")
    else:
        frog = _pick_frog(store, tasks, now)
        if frog is None:
            parts.append("Backlog empty. Nothing to chase today.")
        else:
            logger.info("frog %s", frog.id)
            parts.append(_frog_part(store, client, frog, now))
    block = _vault_part(vault_path, now.date(), select_vault_reminders, render.render_vault_block)
    if block:
        parts.append(block)
    send("\n\n".join(parts))


def send_checkin(store, send, now: datetime, vault_path: str) -> None:
    """One reminder, only when useful: the frog has no answer yet, or a vault task is due."""
    logger.info("Check-in job firing at %s", now.isoformat())
    if is_quiet_hours(now):
        logger.info("Skipping check-in: quiet hours")
        return
    parts = []
    frog_day = store.frog_of_day(now.date())
    if frog_day is not None and frog_day.answered is None:
        try:
            task = store.get_task(frog_day.task_id)
        except Exception:
            logger.exception("Check-in could not load the frog")
            task = None
        if task is not None:
            kind = REFRAME if _is_reframe(store, task.id) else FROG
            legend = render.REFRAME_LEGEND if kind == REFRAME else render.FROG_LEGEND
            parts.append(render.render_checkin(task, frog_day.started_at is not None, legend))
            store.set_open_prompt(kind, [task.id], now)
    due = _vault_part(
        vault_path, now.date(), select_vault_due, render.render_vault_due, report_unreadable=False
    )
    if due:
        parts.append(due)
    if not parts:
        logger.info("Skipping check-in: nothing useful to say")
        return
    send("\n\n".join(parts))
