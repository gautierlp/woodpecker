from datetime import date, datetime, timezone

import pytest

from woodpecker import sidecar


def _conn():
    c = sidecar.connect(":memory:")
    sidecar.init_db(c)
    return c


@pytest.fixture
def tmp_conn():
    conn = sidecar.connect(":memory:")
    sidecar.init_db(conn)
    yield conn
    conn.close()


def test_last_nagged_round_trip():
    c = _conn()
    when = datetime(2026, 7, 16, 9, 0, tzinfo=timezone.utc)
    sidecar.set_last_nagged(c, 42, when)
    assert sidecar.last_nagged_map(c) == {42: when}


def test_set_last_nagged_is_upsert():
    c = _conn()
    sidecar.set_last_nagged(c, 42, datetime(2026, 7, 15, tzinfo=timezone.utc))
    later = datetime(2026, 7, 16, tzinfo=timezone.utc)
    sidecar.set_last_nagged(c, 42, later)
    assert sidecar.last_nagged_map(c)[42] == later


def test_display_snapshot_round_trip():
    c = _conn()
    sidecar.save_display(c, 100, [3, 1, 2])
    assert sidecar.load_display(c, 100) == [3, 1, 2]
    assert sidecar.load_display(c, 999) is None


def test_prune_drops_stale_nag_rows():
    c = _conn()
    sidecar.set_last_nagged(c, 1, datetime(2026, 7, 16, tzinfo=timezone.utc))
    sidecar.set_last_nagged(c, 2, datetime(2026, 7, 16, tzinfo=timezone.utc))
    sidecar.prune(c, live_ids={1})
    assert set(sidecar.last_nagged_map(c)) == {1}


def test_apply_due_snapshot_new_task_starts_at_zero(tmp_conn):
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 18)})
    assert sidecar.bump_counts(tmp_conn) == {1: 0}


def test_apply_due_snapshot_forward_move_increments(tmp_conn):
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 18)})
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 19)})
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 20)})
    assert sidecar.bump_counts(tmp_conn) == {1: 2}


def test_apply_due_snapshot_pull_in_and_equal_do_not_increment(tmp_conn):
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 20)})
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 20)})  # equal
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 18)})  # pulled in
    assert sidecar.bump_counts(tmp_conn) == {1: 0}


def test_apply_due_snapshot_skips_tasks_without_a_due_date(tmp_conn):
    sidecar.apply_due_snapshot(tmp_conn, {1: None})
    assert sidecar.bump_counts(tmp_conn) == {}


def test_apply_due_snapshot_starts_tracking_when_a_date_appears(tmp_conn):
    sidecar.apply_due_snapshot(tmp_conn, {1: None})
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 18)})
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 19)})
    assert sidecar.bump_counts(tmp_conn) == {1: 1}


def test_prune_removes_bump_state_for_dead_tasks(tmp_conn):
    sidecar.apply_due_snapshot(tmp_conn, {1: date(2026, 7, 18), 2: date(2026, 7, 18)})
    sidecar.prune(tmp_conn, {1})
    assert sidecar.bump_counts(tmp_conn) == {1: 0}
