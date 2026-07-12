from datetime import datetime
from zoneinfo import ZoneInfo

from jolt import db, scheduler

TZ = ZoneInfo("Europe/Paris")


def fresh():
    conn = db.connect(":memory:")
    db.init_db(conn)
    return conn


class FakeClient:
    """Records prose calls; returns canned text so no network is hit."""

    def __init__(self):
        self.messages = self
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        from types import SimpleNamespace

        return SimpleNamespace(content=[SimpleNamespace(type="text", text="canned prose")])


def collector():
    sent = []
    return sent, lambda msg: sent.append(msg)


def test_daily_focus_sends_prose_then_backlog():
    conn = fresh()
    now = datetime(2026, 7, 12, 6, tzinfo=TZ)
    db.add_task(conn, "call vet", "important", None, now)
    sent, send = collector()
    scheduler.send_daily_focus(conn, send, FakeClient(), now)
    assert len(sent) == 1
    assert "canned prose" in sent[0]
    assert "📋 " in sent[0]
    assert "call vet" in sent[0]


def test_nags_skipped_during_quiet_hours():
    conn = fresh()
    now = datetime(2026, 7, 12, 5, tzinfo=TZ)  # before 06:00
    db.add_task(conn, "taxes", "normal", None, now)
    sent, send = collector()
    scheduler.send_nags(conn, send, FakeClient(), now)
    assert sent == []


def test_nags_skipped_when_no_pending_task():
    conn = fresh()
    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    sent, send = collector()
    scheduler.send_nags(conn, send, FakeClient(), now)
    assert sent == []


def test_nag_sends_and_marks_nagged():
    conn = fresh()
    created = datetime(2026, 7, 8, tzinfo=TZ)
    now = datetime(2026, 7, 12, 13, tzinfo=TZ)
    t = db.add_task(conn, "taxes", "normal", None, created)
    sent, send = collector()
    scheduler.send_nags(conn, send, FakeClient(), now)
    assert len(sent) == 1
    assert db.get_task(conn, t.id).last_nagged_at == now
