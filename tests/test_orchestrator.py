from datetime import date, datetime
from zoneinfo import ZoneInfo

from jolt import db, orchestrator
from jolt.llm import Intent

TZ = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 7, 12, 8, tzinfo=TZ)


def fresh():
    conn = db.connect(":memory:")
    db.init_db(conn)
    return conn


def test_add_intent_creates_task_and_confirms():
    conn = fresh()
    reply = orchestrator.apply_intent(
        conn, Intent(action="add", text="call vet", deadline=date(2026, 7, 15)), NOW)
    assert "call vet" in reply
    assert "2026-07-15" in reply
    assert len(db.list_pending(conn)) == 1


def test_complete_intent_marks_done_with_plain_ack():
    conn = fresh()
    t = db.add_task(conn, "taxes", "normal", None, NOW)
    reply = orchestrator.apply_intent(conn, Intent(action="complete", task_id=t.id), NOW)
    assert reply == "Done, nice."
    assert db.get_task(conn, t.id).status == "done"


def test_complete_unknown_id_is_graceful():
    conn = fresh()
    reply = orchestrator.apply_intent(conn, Intent(action="complete", task_id=999), NOW)
    assert reply == "Couldn't find that one."


def test_drop_intent():
    conn = fresh()
    t = db.add_task(conn, "x", "normal", None, NOW)
    reply = orchestrator.apply_intent(conn, Intent(action="drop", task_id=t.id), NOW)
    assert reply == "Dropped."
    assert db.get_task(conn, t.id).status == "dropped"


def test_list_intent_renders_backlog():
    conn = fresh()
    db.add_task(conn, "call vet", "important", None, NOW)
    reply = orchestrator.apply_intent(conn, Intent(action="list"), NOW)
    assert "call vet" in reply
    assert reply.startswith("Backlog:")


def test_answer_intent_passes_reply_through():
    conn = fresh()
    reply = orchestrator.apply_intent(conn, Intent(action="answer", reply="Do the taxes first."), NOW)
    assert reply == "Do the taxes first."
