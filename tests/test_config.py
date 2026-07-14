from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from jolt import config
from jolt.models import Task, DailyFocus, PRIORITY_IMPORTANT, STATUS_PENDING


def test_constants_present():
    assert config.STALE_THRESHOLD_DAYS == 3
    assert config.QUIET_START_HOUR == 6
    assert config.QUIET_END_HOUR == 23
    assert config.NAG_HOURS == (9, 13, 19)


def test_now_paris_is_timezone_aware():
    assert config.now_paris().tzinfo is not None


def test_task_and_focus_construct():
    t = Task(
        id=1,
        text="call vet",
        priority=PRIORITY_IMPORTANT,
        deadline=date(2026, 7, 15),
        created_at=datetime(2026, 7, 12, tzinfo=ZoneInfo("Europe/Paris")),
        status=STATUS_PENDING,
        last_nagged_at=None,
        completed_at=None,
    )
    focus = DailyFocus(focus=t, rescues=[])
    assert focus.focus.text == "call vet"
    assert focus.rescues == []


def test_db_path_defaults_when_unset(monkeypatch):
    from jolt import config

    monkeypatch.delenv("JOLT_DB_PATH", raising=False)
    assert config.db_path() == "data/jolt.db"


def test_log_level_defaults_to_info_and_uppercases(monkeypatch):
    from jolt import config

    monkeypatch.delenv("JOLT_LOG_LEVEL", raising=False)
    assert config.log_level() == "INFO"
    monkeypatch.setenv("JOLT_LOG_LEVEL", "debug")
    assert config.log_level() == "DEBUG"


def test_log_file_defaults_to_a_path_under_logs(monkeypatch):
    # A default path (not None) so logs persist out of the box. It lives under ./logs,
    # which is bind-mounted in the container, so the file survives redeploys.
    monkeypatch.delenv("JOLT_LOG_FILE", raising=False)
    assert config.log_file() == "logs/jolt.log"


def test_log_file_respects_env_override(monkeypatch):
    monkeypatch.setenv("JOLT_LOG_FILE", "/app/logs/jolt.log")
    assert config.log_file() == "/app/logs/jolt.log"


def test_log_file_empty_string_disables_file_logging(monkeypatch):
    # An explicit empty value turns file logging off (handy for local runs and tests),
    # rather than creating a stray ./ file.
    monkeypatch.setenv("JOLT_LOG_FILE", "")
    assert config.log_file() is None


def test_call_cost_usd_uses_input_and_output_prices():
    # Input tokens are billed at the input rate, output at the output rate, both per
    # million. Asymmetric inputs catch a swapped-price or missing-/1e6 bug.
    assert config.call_cost_usd(1_000_000, 0) == pytest.approx(
        config.MODEL_PRICE_INPUT_USD_PER_MTOK
    )
    assert config.call_cost_usd(0, 1_000_000) == pytest.approx(
        config.MODEL_PRICE_OUTPUT_USD_PER_MTOK
    )
    assert config.call_cost_usd(0, 0) == 0.0


def test_call_cost_usd_of_a_typical_call():
    # ~4000 input + 60 output on Haiku pricing ($1 / $5 per MTok) is about $0.0043.
    assert config.call_cost_usd(4000, 60) == pytest.approx(0.0043)
