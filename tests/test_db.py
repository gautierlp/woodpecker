from datetime import date, datetime
from zoneinfo import ZoneInfo

from jolt import db
from jolt.models import (
    PRIORITY_IMPORTANT,
    PRIORITY_NORMAL,
    STATUS_DONE,
    STATUS_DROPPED,
    STATUS_PENDING,
)

TZ = ZoneInfo("Europe/Paris")


def fresh():
    conn = db.connect(":memory:")
    db.init_db(conn)
    return conn


def test_add_and_get_roundtrip():
    conn = fresh()
    created = datetime(2026, 7, 12, 8, tzinfo=TZ)
    t = db.add_task(conn, "call vet", PRIORITY_IMPORTANT, date(2026, 7, 15), created)
    assert t.id == 1
    got = db.get_task(conn, 1)
    assert got.text == "call vet"
    assert got.priority == PRIORITY_IMPORTANT
    assert got.deadline == date(2026, 7, 15)
    assert got.created_at == created
    assert got.status == STATUS_PENDING


def test_add_without_deadline():
    conn = fresh()
    t = db.add_task(conn, "tidy desk", PRIORITY_NORMAL, None, datetime(2026, 7, 12, tzinfo=TZ))
    assert t.deadline is None


def test_list_pending_excludes_done_and_dropped():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "a", PRIORITY_NORMAL, None, now)
    b = db.add_task(conn, "b", PRIORITY_NORMAL, None, now)
    c = db.add_task(conn, "c", PRIORITY_NORMAL, None, now)
    db.complete_task(conn, b.id, now)
    db.drop_task(conn, c.id)
    pending = db.list_pending(conn)
    assert [t.id for t in pending] == [a.id]
    assert len(db.list_all(conn)) == 3


def test_complete_sets_status_and_timestamp():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "taxes", PRIORITY_NORMAL, None, now)
    done = db.complete_task(conn, t.id, datetime(2026, 7, 13, tzinfo=TZ))
    assert done.status == STATUS_DONE
    assert done.completed_at == datetime(2026, 7, 13, tzinfo=TZ)


def test_complete_missing_returns_none():
    conn = fresh()
    assert db.complete_task(conn, 999, datetime(2026, 7, 12, tzinfo=TZ)) is None


def test_drop_sets_status():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, None, now)
    dropped = db.drop_task(conn, t.id)
    assert dropped.status == STATUS_DROPPED


def test_mark_nagged_updates_timestamp():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, None, now)
    db.mark_nagged(conn, t.id, datetime(2026, 7, 12, 19, tzinfo=TZ))
    assert db.get_task(conn, t.id).last_nagged_at == datetime(2026, 7, 12, 19, tzinfo=TZ)


def test_add_task_defaults_blocked_by_none():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, None, now)
    assert t.blocked_by is None


def test_block_task_sets_blocked_by():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "a", PRIORITY_NORMAL, None, now)
    b = db.add_task(conn, "b", PRIORITY_NORMAL, None, now)
    updated = db.block_task(conn, a.id, b.id)
    assert updated.blocked_by == b.id
    assert db.get_task(conn, a.id).blocked_by == b.id


def test_unblock_task_clears_blocked_by():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "a", PRIORITY_NORMAL, None, now)
    b = db.add_task(conn, "b", PRIORITY_NORMAL, None, now)
    db.block_task(conn, a.id, b.id)
    updated = db.unblock_task(conn, a.id)
    assert updated.blocked_by is None


def test_completing_blocker_clears_dependents():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    blocker = db.add_task(conn, "blocker", PRIORITY_NORMAL, None, now)
    dep1 = db.add_task(conn, "dep1", PRIORITY_NORMAL, None, now)
    dep2 = db.add_task(conn, "dep2", PRIORITY_NORMAL, None, now)
    db.block_task(conn, dep1.id, blocker.id)
    db.block_task(conn, dep2.id, blocker.id)
    db.complete_task(conn, blocker.id, now)
    assert db.get_task(conn, dep1.id).blocked_by is None
    assert db.get_task(conn, dep2.id).blocked_by is None


def test_dropping_blocker_clears_dependents():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    blocker = db.add_task(conn, "blocker", PRIORITY_NORMAL, None, now)
    dep = db.add_task(conn, "dep", PRIORITY_NORMAL, None, now)
    db.block_task(conn, dep.id, blocker.id)
    db.drop_task(conn, blocker.id)
    assert db.get_task(conn, dep.id).blocked_by is None


def test_update_task_changes_only_given_fields():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "call vet", PRIORITY_NORMAL, date(2026, 7, 15), now)
    updated = db.update_task(conn, t.id, priority=PRIORITY_IMPORTANT)
    assert updated.priority == PRIORITY_IMPORTANT
    assert updated.text == "call vet"  # untouched
    assert updated.deadline == date(2026, 7, 15)  # untouched


def test_update_task_sets_deadline():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, None, now)
    updated = db.update_task(conn, t.id, deadline=date(2026, 7, 12))
    assert updated.deadline == date(2026, 7, 12)


def test_update_task_clears_deadline_with_explicit_none():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, date(2026, 7, 15), now)
    updated = db.update_task(conn, t.id, deadline=None)
    assert updated.deadline is None


def test_update_task_default_leaves_deadline_untouched():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, date(2026, 7, 15), now)
    updated = db.update_task(conn, t.id, text="renamed")
    assert updated.deadline == date(2026, 7, 15)  # not cleared by the default


def test_update_task_never_touches_created_at():
    conn = fresh()
    created = datetime(2026, 7, 1, 8, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, None, created)
    db.update_task(conn, t.id, deadline=date(2026, 7, 20))
    assert db.get_task(conn, t.id).created_at == created


def test_update_task_no_fields_is_noop():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, None, now)
    updated = db.update_task(conn, t.id)
    assert updated.text == "x"


def test_update_task_no_fields_on_done_returns_none():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, None, now)
    db.complete_task(conn, t.id, now)
    assert db.update_task(conn, t.id) is None


def test_update_task_on_done_returns_none():
    conn = fresh()
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "x", PRIORITY_NORMAL, None, now)
    db.complete_task(conn, t.id, now)
    assert db.update_task(conn, t.id, priority=PRIORITY_IMPORTANT) is None


def test_update_task_missing_returns_none():
    conn = fresh()
    assert db.update_task(conn, 999, priority=PRIORITY_IMPORTANT) is None


def test_load_display_returns_none_when_never_saved():
    conn = fresh()
    assert db.load_display(conn, 42) is None


def test_save_display_round_trips():
    conn = fresh()
    db.save_display(conn, 42, [45, 46, 47])
    assert db.load_display(conn, 42) == [45, 46, 47]


def test_save_display_round_trips_empty_list():
    conn = fresh()
    db.save_display(conn, 42, [])
    assert db.load_display(conn, 42) == []


def test_save_display_overwrites_previous():
    conn = fresh()
    db.save_display(conn, 42, [1, 2, 3])
    db.save_display(conn, 42, [9, 8])
    assert db.load_display(conn, 42) == [9, 8]


def test_display_snapshot_is_per_chat():
    conn = fresh()
    db.save_display(conn, 1, [1, 2])
    assert db.load_display(conn, 2) is None


def test_display_snapshot_survives_reconnect(tmp_path):
    # The whole point of persisting it: the snapshot lives on disk, so a restart (a fresh
    # connection to the same file) keeps it instead of dropping number-resolution back to
    # the live order. This is the decisive half of the "28 done hit the wrong task" bug.
    path = str(tmp_path / "jolt.db")
    conn = db.connect(path)
    db.init_db(conn)
    db.save_display(conn, 42, [45, 46, 47])
    conn.close()
    reopened = db.connect(path)
    db.init_db(reopened)
    assert db.load_display(reopened, 42) == [45, 46, 47]


def test_init_db_migrates_existing_table_without_blocked_by():
    conn = db.connect(":memory:")
    # Simulate the live table created before the blocked_by column existed.
    conn.execute(
        "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, "
        "priority TEXT NOT NULL, deadline TEXT, created_at TEXT NOT NULL, status TEXT NOT NULL, "
        "last_nagged_at TEXT, completed_at TEXT)"
    )
    conn.commit()
    db.init_db(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
    assert "blocked_by" in cols


def test_unblock_returns_none_for_a_done_task():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "wax the car", PRIORITY_NORMAL, None, now)
    db.complete_task(conn, t.id, now)  # now done, not pending
    assert db.unblock_task(conn, t.id) is None


def test_unblock_returns_none_for_a_missing_task():
    conn = db.connect(":memory:")
    db.init_db(conn)
    assert db.unblock_task(conn, 9999) is None


def test_block_rejects_self_reference():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "paint fence", PRIORITY_NORMAL, None, now)
    assert db.block_task(conn, t.id, t.id) is None
    assert db.get_task(conn, t.id).blocked_by is None


def test_block_rejects_missing_blocker():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "paint fence", PRIORITY_NORMAL, None, now)
    assert db.block_task(conn, t.id, 9999) is None
    assert db.get_task(conn, t.id).blocked_by is None


def test_block_still_works_for_valid_input():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "buy paint", PRIORITY_NORMAL, None, now)
    b = db.add_task(conn, "paint fence", PRIORITY_NORMAL, None, now)
    result = db.block_task(conn, b.id, a.id)
    assert result is not None
    assert result.blocked_by == a.id


def test_merge_takes_earlier_deadline_and_higher_priority_and_drops_other():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    survivor = db.add_task(conn, "buy molds", PRIORITY_NORMAL, None, now)
    other = db.add_task(conn, "buy shower drain", PRIORITY_IMPORTANT, date(2026, 7, 17), now)
    merged = db.merge_tasks(conn, survivor.id, other.id, "buy molds and shower drain")
    assert merged is not None
    assert merged.id == survivor.id
    assert merged.text == "buy molds and shower drain"
    assert merged.deadline == date(2026, 7, 17)  # the only / earlier deadline wins
    assert merged.priority == PRIORITY_IMPORTANT  # important beats normal
    assert merged.created_at == survivor.created_at  # survivor keeps its age
    assert db.get_task(conn, other.id).status == STATUS_DROPPED


def test_merge_keeps_the_sooner_of_two_deadlines():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "a", PRIORITY_NORMAL, date(2026, 7, 20), now)
    b = db.add_task(conn, "b", PRIORITY_NORMAL, date(2026, 7, 15), now)
    merged = db.merge_tasks(conn, a.id, b.id, "a and b")
    assert merged.deadline == date(2026, 7, 15)


def test_merge_rejects_self_merge():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    t = db.add_task(conn, "a", PRIORITY_NORMAL, None, now)
    assert db.merge_tasks(conn, t.id, t.id, "a") is None


def test_merge_rejects_missing_or_done_task():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "a", PRIORITY_NORMAL, None, now)
    b = db.add_task(conn, "b", PRIORITY_NORMAL, None, now)
    db.complete_task(conn, b.id, now)  # b no longer pending
    assert db.merge_tasks(conn, a.id, b.id, "a and b") is None
    assert db.merge_tasks(conn, a.id, 9999, "a and gone") is None
    assert db.get_task(conn, a.id).text == "a"  # survivor untouched on rejection
