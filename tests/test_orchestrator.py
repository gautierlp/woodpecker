from datetime import date, datetime
from zoneinfo import ZoneInfo

from jolt import orchestrator
from jolt.llm import Intent
from jolt.models import (
    PRIORITY_NORMAL,
    STATUS_DONE,
    STATUS_DROPPED,
    STATUS_PENDING,
    Task,
)

TZ = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 7, 12, 8, tzinfo=TZ)


class FakeStore:
    """A minimal in-memory stand-in for Store, exposing only what apply_intent uses."""

    def __init__(self):
        self._tasks: dict[int, Task] = {}
        self._next_id = 1

    def add_task(self, text, priority, deadline, now):
        task = Task(
            id=self._next_id,
            text=text,
            priority=priority,
            deadline=deadline,
            created_at=now,
            status=STATUS_PENDING,
            last_nagged_at=None,
            completed_at=None,
            position=0,
        )
        self._tasks[task.id] = task
        self._next_id += 1
        return task

    def list_pending(self):
        return [t for t in self._tasks.values() if t.status == STATUS_PENDING]

    def get_task(self, task_id):
        return self._tasks.get(task_id)

    def complete_task(self, task_id, now):
        task = self._tasks.get(task_id)
        if task is None or task.status != STATUS_PENDING:
            return None
        updated = Task(**{**task.__dict__, "status": STATUS_DONE, "completed_at": now})
        self._tasks[task_id] = updated
        return updated

    def drop_task(self, task_id):
        task = self._tasks.get(task_id)
        if task is None or task.status != STATUS_PENDING:
            return False
        self._tasks[task_id] = Task(**{**task.__dict__, "status": STATUS_DROPPED})
        return True

    def reschedule_task(self, task_id, deadline, priority):
        task = self._tasks.get(task_id)
        if task is None or task.status != STATUS_PENDING:
            return None
        over = {}
        if deadline is not None:
            over["deadline"] = deadline
        if priority is not None:
            over["priority"] = priority
        updated = Task(**{**task.__dict__, **over})
        self._tasks[task_id] = updated
        return updated


def fresh():
    return FakeStore()


def test_add_intent_creates_task_and_confirms():
    store = fresh()
    reply = orchestrator.apply_intent(
        store, Intent(action="add", text="call vet", deadline=date(2026, 7, 15)), NOW
    )
    assert "call vet" in reply
    assert "2026-07-15" in reply
    assert len(store.list_pending()) == 1


def test_complete_intent_marks_done_with_plain_ack():
    store = fresh()
    t = store.add_task("taxes", "normal", None, NOW)
    reply = orchestrator.apply_intent(store, Intent(action="complete", task_id=t.id), NOW)
    assert reply == "Done, nice."
    assert store.get_task(t.id).status == "done"


def test_complete_unknown_id_is_graceful():
    store = fresh()
    reply = orchestrator.apply_intent(store, Intent(action="complete", task_id=999), NOW)
    assert reply == "Couldn't find that one."


def test_drop_intent():
    store = fresh()
    t = store.add_task("x", "normal", None, NOW)
    reply = orchestrator.apply_intent(store, Intent(action="drop", task_id=t.id), NOW)
    assert reply == "Dropped."
    assert store.get_task(t.id).status == "dropped"


def test_drop_unknown_id_is_graceful():
    store = fresh()
    reply = orchestrator.apply_intent(store, Intent(action="drop", task_id=999), NOW)
    assert reply == "Couldn't find that one."


def test_list_intent_renders_backlog():
    store = fresh()
    store.add_task("call vet", 4, None, NOW)
    reply = orchestrator.apply_intent(store, Intent(action="list"), NOW)
    assert "call vet" in reply
    assert reply.startswith("📋 ")


def test_answer_intent_passes_reply_through():
    store = fresh()
    reply = orchestrator.apply_intent(
        store, Intent(action="answer", reply="Do the taxes first."), NOW
    )
    assert reply == "Do the taxes first."


def test_reschedule_moves_the_existing_task_instead_of_adding_a_copy():
    store = fresh()
    store.add_task("Pick up the costume", PRIORITY_NORMAL, date(2026, 7, 29), NOW)
    reply = orchestrator.apply_intent(
        store,
        Intent(action="reschedule", task_id=1, deadline=date(2026, 7, 30)),
        NOW,
    )
    assert "2026-07-30" in reply
    tasks = store.list_pending()
    # The regression this guards: a reschedule used to fall through to `add`, leaving two
    # copies of the same task in the backlog.
    assert len(tasks) == 1
    assert tasks[0].id == 1
    assert tasks[0].deadline == date(2026, 7, 30)


def test_reschedule_can_raise_priority_without_a_new_deadline():
    store = fresh()
    store.add_task("Ranger bureau", PRIORITY_NORMAL, date(2026, 7, 29), NOW)
    reply = orchestrator.apply_intent(
        store,
        Intent(action="reschedule", task_id=1, priority="important"),
        NOW,
    )
    tasks = store.list_pending()
    assert len(tasks) == 1
    assert tasks[0].priority == "important"
    assert tasks[0].deadline == date(2026, 7, 29)
    assert reply


def test_reschedule_of_unknown_task_reports_not_found():
    store = fresh()
    reply = orchestrator.apply_intent(
        store, Intent(action="reschedule", task_id=404, deadline=date(2026, 7, 30)), NOW
    )
    assert "find" in reply.lower()
    assert store.list_pending() == []
