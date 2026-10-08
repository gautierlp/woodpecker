from datetime import date, datetime, timedelta, timezone

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


DAY = date(2026, 10, 8)
AT = datetime(2026, 10, 8, 9, tzinfo=timezone.utc)


def test_init_db_twice_is_safe(tmp_conn):
    sidecar.init_db(tmp_conn)


def test_frog_round_trip(tmp_conn):
    sidecar.record_frog(tmp_conn, DAY, 7)
    row = sidecar.frog_of_day(tmp_conn, DAY)
    assert row == sidecar.FrogDay(day=DAY, task_id=7, started_at=None, answered=None)
    sidecar.mark_frog_started(tmp_conn, DAY, AT)
    sidecar.mark_frog_answered(tmp_conn, DAY, "d")
    row = sidecar.frog_of_day(tmp_conn, DAY)
    assert row.started_at == AT
    assert row.answered == "d"


def test_frog_of_an_unknown_day_is_none(tmp_conn):
    assert sidecar.frog_of_day(tmp_conn, DAY) is None


def test_tomorrow_count_counts_days_with_t(tmp_conn):
    for offset in range(3):
        day = DAY + timedelta(days=offset)
        sidecar.record_frog(tmp_conn, day, 7)
        sidecar.mark_frog_answered(tmp_conn, day, "t")
    sidecar.record_frog(tmp_conn, DAY + timedelta(days=3), 7)
    sidecar.mark_frog_answered(tmp_conn, DAY + timedelta(days=3), "d")
    assert sidecar.tomorrow_count(tmp_conn, 7) == 3
    assert sidecar.tomorrow_count(tmp_conn, 8) == 0
    sidecar.clear_tomorrows(tmp_conn, 7)
    assert sidecar.tomorrow_count(tmp_conn, 7) == 0


def test_open_prompt_round_trip_and_replace(tmp_conn):
    assert sidecar.get_open_prompt(tmp_conn) is None
    sidecar.set_open_prompt(tmp_conn, "frog", [7], AT)
    sidecar.set_open_prompt(tmp_conn, "reframe", [3, 4], AT)
    prompt = sidecar.get_open_prompt(tmp_conn)
    assert prompt == sidecar.OpenPrompt(
        kind="reframe", task_ids=[3, 4], sent_at=AT, pending_drop_at=None
    )


def test_the_weekly_prompt_has_its_own_slot(tmp_conn):
    sidecar.set_open_prompt(tmp_conn, "frog", [7], AT)
    sidecar.set_open_prompt(tmp_conn, "weekly", [3, 4], AT)
    assert sidecar.get_open_prompt(tmp_conn).task_ids == [7]
    weekly = sidecar.get_open_prompt(tmp_conn, sidecar.WEEKLY_SLOT)
    assert weekly == sidecar.OpenPrompt(
        kind="weekly", task_ids=[3, 4], sent_at=AT, pending_drop_at=None
    )
    sidecar.clear_open_prompt(tmp_conn, sidecar.WEEKLY_SLOT)
    assert sidecar.get_open_prompt(tmp_conn, sidecar.WEEKLY_SLOT) is None
    assert sidecar.get_open_prompt(tmp_conn).task_ids == [7]


def test_open_prompt_pending_drop_and_clear(tmp_conn):
    sidecar.set_open_prompt(tmp_conn, "frog", [7], AT)
    sidecar.set_pending_drop(tmp_conn, AT)
    assert sidecar.get_open_prompt(tmp_conn).pending_drop_at == AT
    sidecar.set_open_prompt(tmp_conn, "frog", [7], AT)
    assert sidecar.get_open_prompt(tmp_conn).pending_drop_at is None  # a new prompt resets it
    sidecar.clear_open_prompt(tmp_conn)
    assert sidecar.get_open_prompt(tmp_conn) is None
