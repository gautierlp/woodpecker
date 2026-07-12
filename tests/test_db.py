from datetime import date, datetime
from zoneinfo import ZoneInfo

from jolt import db
from jolt.models import PRIORITY_IMPORTANT, PRIORITY_NORMAL, STATUS_DONE, STATUS_DROPPED, STATUS_PENDING

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
