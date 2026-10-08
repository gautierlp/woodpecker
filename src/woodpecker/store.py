from dataclasses import replace
from datetime import date, datetime

from . import sidecar
from .models import STATUS_DONE, Task


class Store:
    """The task-storage seam. Vikunja is the source of truth for tasks; the sidecar holds
    Woodpecker's own nag state and the display snapshot. Returns and accepts the Task dataclass so
    the rest of Woodpecker is unaware of Vikunja."""

    def __init__(self, vikunja, sidecar_conn):
        self._vk = vikunja
        self._conn = sidecar_conn

    def add_task(self, text: str, priority: str, deadline: date | None, now: datetime) -> Task:
        return self._vk.create_task(text, priority, deadline)

    def list_pending(self) -> list[Task]:
        tasks = self._vk.list_open()
        sidecar.apply_due_snapshot(self._conn, {t.id: t.deadline for t in tasks})
        nagged = sidecar.last_nagged_map(self._conn)
        counts = sidecar.bump_counts(self._conn)
        tasks = [
            replace(t, last_nagged_at=nagged.get(t.id), bump_count=counts.get(t.id, 0))
            for t in tasks
        ]
        # An empty result is ambiguous between "truly no open tasks" and a broken
        # filter/transient glitch. Only prune when we have at least one live task to
        # prune against, so a flaky response can't wipe everyone's sidecar state.
        if tasks:
            sidecar.prune(self._conn, {t.id for t in tasks})
        return tasks

    def get_task(self, task_id: int) -> Task | None:
        task = self._vk.get_task(task_id)
        if task is None:
            return None
        nagged = sidecar.last_nagged_map(self._conn)
        counts = sidecar.bump_counts(self._conn)
        return replace(task, last_nagged_at=nagged.get(task.id), bump_count=counts.get(task.id, 0))

    def complete_task(self, task_id: int, now: datetime) -> Task | None:
        task = self._vk.get_task(task_id)
        if task is None or task.status == STATUS_DONE:
            return None
        return self._vk.mark_done(task_id)

    def reschedule_task(
        self, task_id: int, deadline: date | None, priority: str | None
    ) -> Task | None:
        """Move an existing task's due date and/or priority. Deliberately an update, not an
        add: the bump counter in the sidecar keys off task_id, so re-adding the task under a
        new id would reset the avoidance signal that Woodpecker nags on. The bump itself is counted
        by apply_due_snapshot on the next list_pending, which diffs the stored due date."""
        task = self._vk.get_task(task_id)
        if task is None or task.status == STATUS_DONE:
            return None
        return self._vk.update_task(task_id, deadline=deadline, priority=priority)

    def drop_task(self, task_id: int) -> bool:
        task = self._vk.get_task(task_id)
        if task is None or task.status == STATUS_DONE:
            return False
        return self._vk.delete_task(task_id)

    def mark_nagged(self, task_id: int, when: datetime) -> None:
        sidecar.set_last_nagged(self._conn, task_id, when)

    def save_display(self, chat_id: int, task_ids: list[int]) -> None:
        sidecar.save_display(self._conn, chat_id, task_ids)

    def load_display(self, chat_id: int) -> list[int] | None:
        return sidecar.load_display(self._conn, chat_id)

    def rename_task(self, task_id: int, text: str) -> Task | None:
        """Rewrite the title in place (same task_id, so the bump history stays)."""
        task = self._vk.get_task(task_id)
        if task is None or task.status == STATUS_DONE:
            return None
        return self._vk.update_task(task_id, text=text)

    def record_frog(self, day: date, task_id: int) -> None:
        sidecar.record_frog(self._conn, day, task_id)

    def frog_of_day(self, day: date) -> sidecar.FrogDay | None:
        return sidecar.frog_of_day(self._conn, day)

    def mark_frog_started(self, day: date, when: datetime) -> None:
        sidecar.mark_frog_started(self._conn, day, when)

    def mark_frog_answered(self, day: date, letter: str) -> None:
        sidecar.mark_frog_answered(self._conn, day, letter)

    def tomorrow_count(self, task_id: int) -> int:
        return sidecar.tomorrow_count(self._conn, task_id)

    def clear_tomorrows(self, task_id: int) -> None:
        sidecar.clear_tomorrows(self._conn, task_id)

    def set_open_prompt(self, kind: str, task_ids: list[int], when: datetime) -> None:
        sidecar.set_open_prompt(self._conn, kind, task_ids, when)

    def get_open_prompt(self) -> sidecar.OpenPrompt | None:
        return sidecar.get_open_prompt(self._conn)

    def set_pending_drop(self, when: datetime | None) -> None:
        sidecar.set_pending_drop(self._conn, when)

    def clear_open_prompt(self) -> None:
        sidecar.clear_open_prompt(self._conn)
