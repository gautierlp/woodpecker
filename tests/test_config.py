from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from woodpecker import config
from woodpecker.models import Task, PRIORITY_IMPORTANT, STATUS_PENDING


def test_constants_present():
    assert config.STALE_THRESHOLD_DAYS == 3
    assert config.QUIET_START_HOUR == 6
    assert config.QUIET_END_HOUR == 23


def test_now_paris_is_timezone_aware():
    assert config.now_paris().tzinfo is not None


def test_task_constructs():
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
    assert t.text == "call vet"


def test_sidecar_path_defaults_when_unset(monkeypatch):
    from woodpecker import config

    monkeypatch.delenv("WOODPECKER_DB_PATH", raising=False)
    # Deliberately not data/jolt.db: that name belongs to the pre-Vikunja database whose
    # `tasks` table is not a sidecar schema (see docs/CUTOVER.md). Defaulting to it would
    # point a fresh deploy with no WOODPECKER_DB_PATH set at the wrong file.
    assert config.sidecar_path() == "data/sidecar.db"


def test_vikunja_url_reads_env(monkeypatch):
    monkeypatch.setenv("VIKUNJA_URL", "https://vikunja.example.com")
    assert config.vikunja_url() == "https://vikunja.example.com"


def test_vikunja_token_reads_env(monkeypatch):
    monkeypatch.setenv("VIKUNJA_TOKEN", "tk_123")
    assert config.vikunja_token() == "tk_123"


def test_vikunja_project_id_reads_env_as_int(monkeypatch):
    monkeypatch.setenv("VIKUNJA_PROJECT_ID", "7")
    assert config.vikunja_project_id() == 7


def test_log_level_defaults_to_info_and_uppercases(monkeypatch):
    from woodpecker import config

    monkeypatch.delenv("WOODPECKER_LOG_LEVEL", raising=False)
    assert config.log_level() == "INFO"
    monkeypatch.setenv("WOODPECKER_LOG_LEVEL", "debug")
    assert config.log_level() == "DEBUG"


def test_log_file_defaults_to_a_path_under_logs(monkeypatch):
    # A default path (not None) so logs persist out of the box. It lives under ./logs,
    # which is bind-mounted in the container, so the file survives redeploys.
    monkeypatch.delenv("WOODPECKER_LOG_FILE", raising=False)
    assert config.log_file() == "logs/woodpecker.log"


def test_log_file_respects_env_override(monkeypatch):
    monkeypatch.setenv("WOODPECKER_LOG_FILE", "/app/logs/woodpecker.log")
    assert config.log_file() == "/app/logs/woodpecker.log"


def test_log_file_empty_string_disables_file_logging(monkeypatch):
    # An explicit empty value turns file logging off (handy for local runs and tests),
    # rather than creating a stray ./ file.
    monkeypatch.setenv("WOODPECKER_LOG_FILE", "")
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


def test_settings_fall_back_to_the_old_jolt_names(monkeypatch):
    # The bot was called Jolt before. The .env on jarvis still uses the JOLT_* names, so
    # they must keep working until that file is renamed.
    for name in ("DB_PATH", "LOG_LEVEL", "LOG_FILE"):
        monkeypatch.delenv(f"WOODPECKER_{name}", raising=False)
    monkeypatch.setenv("JOLT_DB_PATH", "data/old.db")
    monkeypatch.setenv("JOLT_LOG_LEVEL", "debug")
    monkeypatch.setenv("JOLT_LOG_FILE", "")
    assert config.sidecar_path() == "data/old.db"
    assert config.log_level() == "DEBUG"
    assert config.log_file() is None


def test_new_setting_names_win_over_the_old_ones(monkeypatch):
    monkeypatch.setenv("JOLT_DB_PATH", "data/old.db")
    monkeypatch.setenv("WOODPECKER_DB_PATH", "data/new.db")
    assert config.sidecar_path() == "data/new.db"


def test_nudge_rhythm_tunables():
    assert config.FOCUS_HOUR == 9
    assert config.CHECKIN_HOUR == 14
    assert config.WEEKLY_REVIEW_DAY == "sun"
    assert config.WEEKLY_REVIEW_HOUR == 10
    assert config.REFRAME_AFTER_BUMPS == 3
    assert config.STALE_REVIEW_DAYS == 14
    assert config.DROP_CONFIRM_MINUTES == 10
    assert config.VAULT_SOON_DAYS == 7
    assert config.STEP_ANSWER_MINUTES == 30


def test_vault_path_defaults_to_the_container_mount(monkeypatch):
    monkeypatch.delenv("WOODPECKER_VAULT_PATH", raising=False)
    monkeypatch.delenv("JOLT_VAULT_PATH", raising=False)
    assert config.vault_path() == "/vault"


def test_vault_path_reads_the_env(monkeypatch):
    monkeypatch.setenv("WOODPECKER_VAULT_PATH", "/tmp/v")
    assert config.vault_path() == "/tmp/v"
