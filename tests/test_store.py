from datetime import datetime, timezone

from jolt import sidecar, store
from jolt.models import PRIORITY_NORMAL, STATUS_PENDING, Task


class FakeVikunja:
    def __init__(self, open_tasks=None):
        self._open = {t.id: t for t in (open_tasks or [])}
        self.deleted = []
        self.completed = []

    def list_open(self):
        return list(self._open.values())

    def create_task(self, text, priority, deadline):
        t = Task(
            id=99,
            text=text,
            priority=priority,
            deadline=deadline,
            created_at=datetime.now(timezone.utc),
            status=STATUS_PENDING,
            last_nagged_at=None,
            completed_at=None,
            position=0,
        )
        self._open[t.id] = t
        return t

    def get_task(self, task_id):
        return self._open.get(task_id)

    def mark_done(self, task_id):
        self.completed.append(task_id)
        return self._open.get(task_id)

    def delete_task(self, task_id):
        if task_id in self._open:
            self.deleted.append(task_id)
            return True
        return False


def _task(id, **over):
    base = dict(
        text=f"t{id}",
        priority=PRIORITY_NORMAL,
        deadline=None,
        created_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        status=STATUS_PENDING,
        last_nagged_at=None,
        completed_at=None,
        position=0,
    )
    base.update(over)
    return Task(id=id, **base)


def _store(vk):
    c = sidecar.connect(":memory:")
    sidecar.init_db(c)
    return store.Store(vk, c), c


def test_list_pending_merges_last_nagged_from_sidecar():
    vk = FakeVikunja([_task(1), _task(2)])
    s, c = _store(vk)
    when = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)
    sidecar.set_last_nagged(c, 2, when)
    by_id = {t.id: t for t in s.list_pending()}
    assert by_id[1].last_nagged_at is None
    assert by_id[2].last_nagged_at == when


def test_mark_nagged_persists_and_shows_next_read():
    vk = FakeVikunja([_task(1)])
    s, c = _store(vk)
    when = datetime(2026, 7, 16, 13, 0, tzinfo=timezone.utc)
    s.mark_nagged(1, when)
    assert s.list_pending()[0].last_nagged_at == when


def test_complete_calls_vikunja_mark_done():
    vk = FakeVikunja([_task(1)])
    s, _ = _store(vk)
    s.complete_task(1, datetime.now(timezone.utc))
    assert vk.completed == [1]


def test_complete_missing_returns_none():
    vk = FakeVikunja([])
    s, _ = _store(vk)
    assert s.complete_task(123, datetime.now(timezone.utc)) is None


def test_drop_deletes_in_vikunja():
    vk = FakeVikunja([_task(1)])
    s, _ = _store(vk)
    assert s.drop_task(1) is True
    assert vk.deleted == [1]
