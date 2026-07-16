import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from jolt import sidecar
from scripts import migrate_to_vikunja as m


def _old_db(path, rows):
    c = sqlite3.connect(path)
    c.execute(
        "CREATE TABLE tasks (id INTEGER PRIMARY KEY, text TEXT, priority TEXT, "
        "deadline TEXT, created_at TEXT, status TEXT, last_nagged_at TEXT, "
        "completed_at TEXT, blocked_by INTEGER)"
    )
    c.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    c.close()


class FakeVikunja:
    def __init__(self, empty=True):
        self._empty = empty
        self.created = []
        self.completed = []
        self._next = 1000

    def list_open(self):
        return [] if self._empty else [object()]

    def create_task(self, text, priority, deadline):
        from jolt.models import STATUS_PENDING, Task

        self._next += 1
        t = Task(
            id=self._next,
            text=text,
            priority=priority,
            deadline=deadline,
            created_at=datetime.now(timezone.utc),
            status=STATUS_PENDING,
            last_nagged_at=None,
            completed_at=None,
            position=0,
        )
        self.created.append(t)
        return t

    def mark_done(self, task_id):
        self.completed.append(task_id)
        return None


def test_aborts_if_backlog_not_empty(tmp_path):
    p = str(tmp_path / "old.db")
    _old_db(p, [])
    c = sidecar.connect(":memory:")
    sidecar.init_db(c)
    with pytest.raises(m.MigrationAborted):
        m.migrate(p, FakeVikunja(empty=False), c, datetime.now(timezone.utc))


def test_imports_pending_and_seeds_last_nagged(tmp_path):
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    p = str(tmp_path / "old.db")
    _old_db(
        p,
        [
            (
                1,
                "Pay taxes",
                "important",
                "2026-07-31",
                "2026-07-10T08:00:00+00:00",
                "pending",
                "2026-07-15T09:00:00+00:00",
                None,
                None,
            ),
        ],
    )
    c = sidecar.connect(":memory:")
    sidecar.init_db(c)
    vk = FakeVikunja()
    counts = m.migrate(p, vk, c, now)
    assert counts["pending"] == 1
    assert vk.created[0].text == "Pay taxes"
    # last_nagged seeded under the NEW vikunja id
    assert sidecar.last_nagged_map(c)[vk.created[0].id].isoformat().startswith("2026-07-15")


def test_recent_done_imported_old_done_skipped(tmp_path):
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    p = str(tmp_path / "old.db")
    recent = (now - timedelta(days=10)).date().isoformat()
    old = (now - timedelta(days=200)).date().isoformat()
    _old_db(
        p,
        [
            (
                1,
                "recent done",
                "normal",
                None,
                "2026-01-01T00:00:00+00:00",
                "done",
                None,
                f"{recent}T00:00:00+00:00",
                None,
            ),
            (
                2,
                "ancient done",
                "normal",
                None,
                "2025-01-01T00:00:00+00:00",
                "done",
                None,
                f"{old}T00:00:00+00:00",
                None,
            ),
        ],
    )
    c = sidecar.connect(":memory:")
    sidecar.init_db(c)
    vk = FakeVikunja()
    counts = m.migrate(p, vk, c, now)
    assert counts["done"] == 1
    assert [t.text for t in vk.created] == ["recent done"]
    assert len(vk.completed) == 1


def test_naive_completed_at_is_treated_as_utc(tmp_path):
    # Older / hand-edited rows can have a completed_at with no timezone offset. That
    # must not crash the comparison against the (aware) cutoff, and it should be
    # treated as UTC for the recent/ancient cutoff decision, same as an equivalent
    # explicitly-UTC timestamp would be.
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    recent_naive = (now - timedelta(days=10)).replace(tzinfo=None).isoformat()
    old_naive = (now - timedelta(days=200)).replace(tzinfo=None).isoformat()
    p = str(tmp_path / "old.db")
    _old_db(
        p,
        [
            (
                1,
                "recent done naive",
                "normal",
                None,
                "2026-01-01T00:00:00",
                "done",
                None,
                recent_naive,
                None,
            ),
            (
                2,
                "ancient done naive",
                "normal",
                None,
                "2025-01-01T00:00:00",
                "done",
                None,
                old_naive,
                None,
            ),
        ],
    )
    c = sidecar.connect(":memory:")
    sidecar.init_db(c)
    vk = FakeVikunja()
    counts = m.migrate(p, vk, c, now)  # must not raise TypeError on naive vs aware
    assert counts["done"] == 1
    assert [t.text for t in vk.created] == ["recent done naive"]


def test_dropped_skipped(tmp_path):
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    p = str(tmp_path / "old.db")
    _old_db(p, [(1, "x", "normal", None, "2026-07-10T00:00:00+00:00", "dropped", None, None, None)])
    c = sidecar.connect(":memory:")
    sidecar.init_db(c)
    vk = FakeVikunja()
    counts = m.migrate(p, vk, c, now)
    assert counts["skipped_dropped"] == 1
    assert vk.created == []
