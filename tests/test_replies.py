import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from woodpecker import replies, sidecar
from woodpecker.models import STATUS_PENDING, Task
from woodpecker.store import Store
from woodpecker.vikunja import VikunjaError

TZ = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 10, 8, 9, 30, tzinfo=TZ)


class FakeVikunja:
    def __init__(self, open_tasks=None):
        self._open = {t.id: t for t in (open_tasks or [])}
        self.calls = []

    def list_open(self):
        return list(self._open.values())

    def get_task(self, task_id):
        return self._open.get(task_id)

    def mark_done(self, task_id):
        self.calls.append(("done", task_id))
        return self._open.pop(task_id, None)

    def delete_task(self, task_id):
        self.calls.append(("delete", task_id))
        return self._open.pop(task_id, None) is not None

    def update_task(self, task_id, deadline=None, priority=None, text=None):
        self.calls.append(("update", task_id, deadline, text))
        task = self._open.get(task_id)
        if task is None:
            return None
        changes = {}
        if deadline is not None:
            changes["deadline"] = deadline
        if text is not None:
            changes["text"] = text
        task = replace(task, **changes)
        self._open[task_id] = task
        return task


def _task(id):
    return Task(
        id=id,
        text=f"task {id}",
        priority=0,
        deadline=None,
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        status=STATUS_PENDING,
        last_nagged_at=None,
        completed_at=None,
    )


def fresh(*ids):
    conn = sidecar.connect(":memory:")
    sidecar.init_db(conn)
    vk = FakeVikunja([_task(i) for i in ids])
    return Store(vk, conn), vk


def with_frog(kind=replies.FROG, task_id=1, *ids):
    store, vk = fresh(task_id, *ids)
    store.record_frog(NOW.date(), task_id)
    store.set_open_prompt(kind, [task_id], NOW)
    return store, vk


def test_d_completes_the_frog_once():
    store, vk = with_frog()
    assert replies.answer(store, "d", NOW) == "Done, nice."
    assert vk.calls == [("done", 1)]
    assert store.frog_of_day(NOW.date()).answered == "d"
    assert store.get_open_prompt() is None


def test_letters_are_trimmed_and_case_insensitive():
    store, vk = with_frog()
    assert replies.answer(store, "  D ", NOW) == "Done, nice."


def test_o_records_a_start_and_keeps_the_prompt():
    store, vk = with_frog()
    replies.answer(store, "o", NOW)
    assert store.frog_of_day(NOW.date()).started_at == NOW
    assert store.get_open_prompt() is not None
    assert vk.calls == []


def test_t_reschedules_to_tomorrow():
    store, vk = with_frog()
    replies.answer(store, "t", NOW)
    assert vk.calls == [("update", 1, NOW.date() + timedelta(days=1), None)]
    assert store.frog_of_day(NOW.date()).answered == "t"


def test_x_needs_a_second_x_within_ten_minutes():
    store, vk = with_frog()
    assert replies.answer(store, "x", NOW) == 'Drop "task 1"? Send x again.'
    assert vk.calls == []
    assert replies.answer(store, "x", NOW + timedelta(minutes=10)) == "Dropped."
    assert vk.calls == [("delete", 1)]
    assert store.frog_of_day(NOW.date()).answered == "x"


def test_a_late_second_x_asks_again():
    store, vk = with_frog()
    replies.answer(store, "x", NOW)
    assert replies.answer(store, "x", NOW + timedelta(minutes=11)).startswith("Drop ")
    assert vk.calls == []


def test_no_open_prompt_is_refused():
    store, vk = fresh(1)
    assert replies.answer(store, "d", NOW) == "Nothing to answer right now."
    assert vk.calls == []


def test_a_longer_message_is_not_a_letter_reply():
    store, vk = with_frog()
    assert replies.answer(store, "done with the taxes", NOW) is None


def test_a_reframe_letter_on_a_frog_prompt_gets_the_legend():
    store, vk = with_frog()
    assert "d done" in replies.answer(store, "s", NOW)
    assert vk.calls == []


def test_s_then_the_next_message_rewrites_the_title_and_resets_t():
    store, vk = with_frog(replies.REFRAME)
    store.mark_frog_answered(NOW.date(), "t")
    assert "smaller step" in replies.answer(store, "s", NOW).lower()
    reply = replies.answer(store, "Open the Doctolib page", NOW + timedelta(minutes=5))
    assert reply == 'Now the task is "Open the Doctolib page".'
    assert vk.get_task(1).text == "Open the Doctolib page"
    assert store.tomorrow_count(1) == 0
    assert store.get_open_prompt() is None


def test_the_step_window_closes_after_30_minutes():
    store, vk = with_frog(replies.REFRAME)
    replies.answer(store, "s", NOW)
    assert replies.answer(store, "buy milk", NOW + timedelta(minutes=31)) is None
    assert vk.get_task(1).text == "task 1"
    assert store.get_open_prompt() is None


def test_n_drops_with_a_confirm():
    store, vk = with_frog(replies.REFRAME)
    assert replies.answer(store, "n", NOW).startswith("Not yours?")
    assert vk.calls == []
    assert replies.answer(store, "n", NOW + timedelta(minutes=1)) == "Dropped."
    assert vk.calls == [("delete", 1)]


def test_each_reply_logs_task_and_action(caplog):
    store, vk = with_frog()
    with caplog.at_level(logging.INFO, logger="woodpecker.replies"):
        replies.answer(store, "t", NOW)
    assert "reply 1 t" in caplog.text


class RenameFails(FakeVikunja):
    def update_task(self, task_id, deadline=None, priority=None, text=None):
        if text is not None:
            raise VikunjaError("PUT /tasks -> 503")
        return super().update_task(task_id, deadline, priority, text)


def test_a_failed_rename_keeps_the_step_prompt_open():
    store, vk = with_frog(replies.REFRAME)
    replies.answer(store, "s", NOW)
    store._vk = RenameFails([_task(1)])
    with pytest.raises(VikunjaError):  # the bot answers "unreachable, try again"
        replies.answer(store, "Open the page", NOW + timedelta(minutes=1))
    assert store.get_open_prompt().kind == replies.STEP
    store._vk = vk
    reply = replies.answer(store, "Open the page", NOW + timedelta(minutes=2))
    assert reply == 'Now the task is "Open the page".'


def test_a_letter_in_the_step_window_does_not_rename():
    store, vk = with_frog(replies.REFRAME)
    replies.answer(store, "s", NOW)
    assert replies.answer(store, "x", NOW + timedelta(minutes=1)) == replies.NOTHING_OPEN
    assert vk.get_task(1).text == "task 1"
    assert store.get_open_prompt() is None
