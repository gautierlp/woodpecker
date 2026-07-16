from datetime import datetime, timezone

from jolt import sidecar


def _conn():
    c = sidecar.connect(":memory:")
    sidecar.init_db(c)
    return c


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
