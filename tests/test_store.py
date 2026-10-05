from datetime import date, datetime, timezone

from woodpecker import sidecar, store
from woodpecker.models import PRIORITY_IMPORTANT, PRIORITY_NORMAL, STATUS_DONE, STATUS_PENDING, Task


class FakeVikunja:
    def __init__(self, open_tasks=None, done_tasks=None):
        self._tasks = {t.id: t for t in (open_tasks or [])}
        self._tasks.update({t.id: t for t in (done_tasks or [])})
        self.deleted = []
        self.completed = []

    def set_open(self, open_tasks):
        """Replace the open tasks the fake returns from list_open, so a test can
        simulate a due date moving forward between two reads."""
        self._tasks = {t.id: t for t in open_tasks}

    def list_open(self):
        return [t for t in self._tasks.values() if t.status == STATUS_PENDING]

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
        self._tasks[t.id] = t
        return t

    def get_task(self, task_id):
        return self._tasks.get(task_id)

    def mark_done(self, task_id):
        self.completed.append(task_id)
        return self._tasks.get(task_id)

    def delete_task(self, task_id):
        if task_id in self._tasks:
            self.deleted.append(task_id)
            return True
        return False

    def update_task(self, task_id, deadline=None, priority=None):
        t = self._tasks.get(task_id)
        if t is None:
            return None
        over = {}
        if deadline is not None:
            over["deadline"] = deadline
        if priority is not None:
            over["priority"] = priority
        updated = Task(**{**t.__dict__, **over})
        self._tasks[task_id] = updated
        return updated


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


def test_complete_already_done_returns_none_and_does_not_call_mark_done():
    vk = FakeVikunja(done_tasks=[_task(1, status=STATUS_DONE)])
    s, _ = _store(vk)
    assert s.complete_task(1, datetime.now(timezone.utc)) is None
    assert vk.completed == []


def test_drop_already_done_returns_false_and_does_not_delete():
    vk = FakeVikunja(done_tasks=[_task(1, status=STATUS_DONE)])
    s, _ = _store(vk)
    assert s.drop_task(1) is False
    assert vk.deleted == []


def test_list_pending_with_empty_result_does_not_prune_nag_state():
    vk = FakeVikunja([])
    s, c = _store(vk)
    when = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)
    sidecar.set_last_nagged(c, 42, when)
    assert s.list_pending() == []
    assert sidecar.last_nagged_map(c) == {42: when}


def test_list_pending_injects_bump_count_after_a_forward_move():
    vk = FakeVikunja([_task(1, deadline=date(2026, 7, 18))])
    s, _c = _store(vk)
    first = s.list_pending()
    assert first[0].bump_count == 0
    vk.set_open([_task(1, deadline=date(2026, 7, 19))])
    second = s.list_pending()
    assert second[0].bump_count == 1


def test_reschedule_task_moves_the_due_date_on_the_existing_task():
    vk = FakeVikunja([_task(1, deadline=date(2026, 7, 29))])
    s, _ = _store(vk)
    updated = s.reschedule_task(1, deadline=date(2026, 7, 30), priority=None)
    assert updated is not None
    assert updated.id == 1
    assert updated.deadline == date(2026, 7, 30)
    # No new task was invented: the backlog still holds exactly the one task.
    assert [t.id for t in s.list_pending()] == [1]


def test_reschedule_task_sets_priority():
    vk = FakeVikunja([_task(1, deadline=date(2026, 7, 29))])
    s, _ = _store(vk)
    updated = s.reschedule_task(1, deadline=None, priority=PRIORITY_IMPORTANT)
    assert updated.priority == PRIORITY_IMPORTANT
    assert updated.deadline == date(2026, 7, 29)


def test_reschedule_task_returns_none_for_missing_or_done_task():
    vk = FakeVikunja([_task(1)], done_tasks=[_task(2, status=STATUS_DONE)])
    s, _ = _store(vk)
    assert s.reschedule_task(404, deadline=date(2026, 7, 30), priority=None) is None
    assert s.reschedule_task(2, deadline=date(2026, 7, 30), priority=None) is None


def test_pushing_a_due_date_forward_counts_a_bump():
    """The point of rescheduling in place rather than re-adding: the sidecar sees the same
    task_id move forward and increments the avoidance signal."""
    vk = FakeVikunja([_task(1, deadline=date(2026, 7, 29))])
    s, _ = _store(vk)
    assert s.list_pending()[0].bump_count == 0
    s.reschedule_task(1, deadline=date(2026, 7, 30), priority=None)
    assert s.list_pending()[0].bump_count == 1
    s.reschedule_task(1, deadline=date(2026, 7, 31), priority=None)
    assert s.list_pending()[0].bump_count == 2


def test_pulling_a_due_date_earlier_does_not_count_a_bump():
    vk = FakeVikunja([_task(1, deadline=date(2026, 7, 29))])
    s, _ = _store(vk)
    assert s.list_pending()[0].bump_count == 0
    s.reschedule_task(1, deadline=date(2026, 7, 28), priority=None)
    assert s.list_pending()[0].bump_count == 0
