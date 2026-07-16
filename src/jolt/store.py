import logging
from dataclasses import replace
from datetime import date, datetime

from . import sidecar
from .models import Task

logger = logging.getLogger(__name__)


class Store:
    """The task-storage seam. Vikunja is the source of truth for tasks; the sidecar holds
    Jolt's own nag state and the display snapshot. Returns and accepts the Task dataclass so
    the rest of Jolt is unaware of Vikunja."""

    def __init__(self, vikunja, sidecar_conn):
        self._vk = vikunja
        self._conn = sidecar_conn

    def add_task(self, text: str, priority: str, deadline: date | None, now: datetime) -> Task:
        return self._vk.create_task(text, priority, deadline)

    def list_pending(self) -> list[Task]:
        nagged = sidecar.last_nagged_map(self._conn)
        tasks = [replace(t, last_nagged_at=nagged.get(t.id)) for t in self._vk.list_open()]
        sidecar.prune(self._conn, {t.id for t in tasks})
        return tasks

    def get_task(self, task_id: int) -> Task | None:
        task = self._vk.get_task(task_id)
        if task is None:
            return None
        nagged = sidecar.last_nagged_map(self._conn)
        return replace(task, last_nagged_at=nagged.get(task.id))

    def complete_task(self, task_id: int, now: datetime) -> Task | None:
        return self._vk.mark_done(task_id)

    def drop_task(self, task_id: int) -> bool:
        return self._vk.delete_task(task_id)

    def mark_nagged(self, task_id: int, when: datetime) -> None:
        sidecar.set_last_nagged(self._conn, task_id, when)

    def save_display(self, chat_id: int, task_ids: list[int]) -> None:
        sidecar.save_display(self._conn, chat_id, task_ids)

    def load_display(self, chat_id: int) -> list[int] | None:
        return sidecar.load_display(self._conn, chat_id)
