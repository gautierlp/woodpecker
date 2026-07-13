from datetime import date, datetime
from zoneinfo import ZoneInfo

from jolt import db, orchestrator
from jolt.llm import Intent
from jolt.models import STATUS_DROPPED

TZ = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 7, 12, 8, tzinfo=TZ)


def fresh():
    conn = db.connect(":memory:")
    db.init_db(conn)
    return conn


def test_add_intent_creates_task_and_confirms():
    conn = fresh()
    reply = orchestrator.apply_intent(
        conn, Intent(action="add", text="call vet", deadline=date(2026, 7, 15)), NOW
    )
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
    assert reply.startswith("📋 ")


def test_answer_intent_passes_reply_through():
    conn = fresh()
    reply = orchestrator.apply_intent(
        conn, Intent(action="answer", reply="Do the taxes first."), NOW
    )
    assert reply == "Do the taxes first."


def test_block_intent_sets_dependency_and_confirms():
    conn = fresh()
    a = db.add_task(conn, "submit expenses", "important", None, NOW)
    b = db.add_task(conn, "do accounts", "important", None, NOW)
    reply = orchestrator.apply_intent(
        conn, Intent(action="block", task_id=a.id, blocked_by=b.id), NOW
    )
    assert db.get_task(conn, a.id).blocked_by == b.id
    assert "do accounts" in reply


def test_unblock_intent_clears_dependency():
    conn = fresh()
    a = db.add_task(conn, "a", "normal", None, NOW)
    b = db.add_task(conn, "b", "normal", None, NOW)
    db.block_task(conn, a.id, b.id)
    reply = orchestrator.apply_intent(conn, Intent(action="unblock", task_id=a.id), NOW)
    assert db.get_task(conn, a.id).blocked_by is None
    assert reply == "Unblocked."


def test_block_self_is_rejected():
    conn = fresh()
    a = db.add_task(conn, "a", "normal", None, NOW)
    reply = orchestrator.apply_intent(
        conn, Intent(action="block", task_id=a.id, blocked_by=a.id), NOW
    )
    assert db.get_task(conn, a.id).blocked_by is None
    assert "itself" in reply.lower()


def test_block_dangling_id_is_rejected():
    conn = fresh()
    a = db.add_task(conn, "a", "normal", None, NOW)
    reply = orchestrator.apply_intent(
        conn, Intent(action="block", task_id=a.id, blocked_by=999), NOW
    )
    assert db.get_task(conn, a.id).blocked_by is None
    assert "find" in reply.lower()


def test_block_cycle_is_rejected():
    conn = fresh()
    a = db.add_task(conn, "a", "normal", None, NOW)
    b = db.add_task(conn, "b", "normal", None, NOW)
    db.block_task(conn, b.id, a.id)  # b already waits on a
    reply = orchestrator.apply_intent(
        conn,
        Intent(action="block", task_id=a.id, blocked_by=b.id),
        NOW,  # a waits on b -> loop
    )
    assert db.get_task(conn, a.id).blocked_by is None
    assert "loop" in reply.lower()


def test_edit_intent_changes_deadline_and_confirms():
    conn = fresh()
    t = db.add_task(conn, "accounts", "important", date(2026, 7, 20), NOW)
    reply = orchestrator.apply_intent(
        conn, Intent(action="edit", task_id=t.id, deadline=date(2026, 7, 12)), NOW
    )
    assert db.get_task(conn, t.id).deadline == date(2026, 7, 12)
    assert "2026-07-12" in reply


def test_edit_intent_clears_deadline():
    conn = fresh()
    t = db.add_task(conn, "accounts", "important", date(2026, 7, 20), NOW)
    reply = orchestrator.apply_intent(
        conn, Intent(action="edit", task_id=t.id, clear_deadline=True), NOW
    )
    assert db.get_task(conn, t.id).deadline is None
    assert "removed" in reply.lower()


def test_edit_intent_changes_priority():
    conn = fresh()
    t = db.add_task(conn, "x", "normal", None, NOW)
    reply = orchestrator.apply_intent(
        conn, Intent(action="edit", task_id=t.id, priority="important"), NOW
    )
    assert db.get_task(conn, t.id).priority == "important"
    assert reply == "Updated."


def test_edit_intent_empty_change_asks_what_to_change():
    # An edit that carries no concrete change is the model's usual output when the user
    # reports part of a compound task done ("shower is done" on "Groom Rex: shower,
    # wash ears, brush teeth"). We can't tick off part of a task, so instead of the old
    # dead-end "Nothing to change.", say what we can do and ask for the new wording.
    conn = fresh()
    t = db.add_task(conn, "x", "normal", None, NOW)
    reply = orchestrator.apply_intent(conn, Intent(action="edit", task_id=t.id), NOW)
    assert reply == (
        "I can only change a task as a whole, not tick off part of one. Tell me the new "
        "wording if you want it trimmed down, or say it's fully done."
    )


def test_edit_intent_unknown_id_is_graceful():
    conn = fresh()
    reply = orchestrator.apply_intent(
        conn, Intent(action="edit", task_id=999, deadline=date(2026, 7, 12)), NOW
    )
    assert reply == "Couldn't find that one."


def test_merge_folds_two_tasks_and_confirms():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "buy molds", "normal", None, now)
    b = db.add_task(conn, "buy shower drain", "normal", None, now)
    intent = Intent(action="merge", task_id=a.id, merge_from=b.id, text="buy molds and shower drain")
    reply = orchestrator.apply_intent(conn, intent, now)
    assert "buy molds and shower drain" in reply
    assert db.get_task(conn, b.id).status == STATUS_DROPPED
    assert db.get_task(conn, a.id).text == "buy molds and shower drain"


def test_merge_falls_back_to_joined_text_when_claude_gives_none():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "buy molds", "normal", None, now)
    b = db.add_task(conn, "buy shower drain", "normal", None, now)
    intent = Intent(action="merge", task_id=a.id, merge_from=b.id, text=None)
    orchestrator.apply_intent(conn, intent, now)
    assert db.get_task(conn, a.id).text == "buy molds and buy shower drain"


def test_merge_missing_task_reports_not_found():
    conn = db.connect(":memory:")
    db.init_db(conn)
    now = datetime(2026, 7, 12, tzinfo=TZ)
    a = db.add_task(conn, "buy molds", "normal", None, now)
    intent = Intent(action="merge", task_id=a.id, merge_from=9999, text="x")
    reply = orchestrator.apply_intent(conn, intent, now)
    assert reply == "Couldn't find those tasks."
    assert db.get_task(conn, a.id).text == "buy molds"  # unchanged
