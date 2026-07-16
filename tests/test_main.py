import logging
from logging.handlers import RotatingFileHandler

from jolt import main
from jolt.store import Store


def _file_handlers(handlers):
    return [h for h in handlers if isinstance(h, RotatingFileHandler)]


def _console_handlers(handlers):
    # A plain stdout handler is a StreamHandler that is not a FileHandler
    # (RotatingFileHandler subclasses StreamHandler).
    return [
        h
        for h in handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]


def test_build_log_handlers_adds_file_when_configured(tmp_path, monkeypatch):
    # A rotating file handler is added at the configured path, and its missing parent
    # directory is created. The stdout handler stays, so `docker logs` keeps working.
    log_file = tmp_path / "nested" / "jolt.log"
    monkeypatch.setenv("JOLT_LOG_FILE", str(log_file))

    handlers = main._build_log_handlers()

    assert _console_handlers(handlers)
    files = _file_handlers(handlers)
    assert len(files) == 1
    assert files[0].baseFilename == str(log_file)
    assert log_file.parent.is_dir()


def test_build_log_handlers_file_handler_persists_records(tmp_path, monkeypatch):
    # The redeploy-log-loss fix: records written through the file handler land in a file
    # on the (bind-mounted) host path, so history survives container recreation.
    log_file = tmp_path / "jolt.log"
    monkeypatch.setenv("JOLT_LOG_FILE", str(log_file))

    handler = _file_handlers(main._build_log_handlers())[0]
    record = logging.LogRecord("jolt.test", logging.INFO, __file__, 1, "persisted-line", None, None)
    handler.emit(record)
    handler.flush()
    handler.close()

    assert "persisted-line" in log_file.read_text()


def test_build_log_handlers_omits_file_when_disabled(monkeypatch):
    # JOLT_LOG_FILE="" turns file logging off but keeps the stdout handler.
    monkeypatch.setenv("JOLT_LOG_FILE", "")

    handlers = main._build_log_handlers()

    assert not _file_handlers(handlers)
    assert _console_handlers(handlers)


class _FakeApplication:
    """Stands in for telegram.ext.Application: main() only needs bot_data, the two
    add_handler-style calls, and a run_polling it must not actually block on."""

    def __init__(self):
        self.bot_data = {}
        self.handlers = []
        self.error_handlers = []
        self.ran_polling = False

    def add_handler(self, handler):
        self.handlers.append(handler)

    def add_error_handler(self, handler):
        self.error_handlers.append(handler)

    def run_polling(self):
        self.ran_polling = True


class _FakeApplicationBuilder:
    """Mimics Application.builder().token(...).post_init(...).build() without touching
    the network."""

    def __init__(self, application):
        self._application = application

    def token(self, _token):
        return self

    def post_init(self, _post_init):
        return self

    def build(self):
        return self._application


def test_main_wires_vikunja_backed_store_into_bot_data(tmp_path, monkeypatch):
    # The Task 8 refactor made main() build a VikunjaClient + Store on top of the sidecar
    # connection and stash the Store in application.bot_data. Every external boundary
    # (env config, sidecar, Vikunja HTTP client, Telegram Application, log setup) is
    # mocked so this exercises only main()'s wiring, not real connections or polling.
    db_path = tmp_path / "jolt.db"
    env = {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_CHAT_ID": "42",
        "ANTHROPIC_API_KEY": "test-anthropic-key",
        "VIKUNJA_URL": "https://vikunja.example.com",
        "VIKUNJA_TOKEN": "test-vikunja-token",
        "VIKUNJA_PROJECT_ID": "7",
        "JOLT_LOG_FILE": "",
        "JOLT_DB_PATH": str(db_path),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    monkeypatch.setattr(main, "setup_logging", lambda: None)

    fake_conn = object()
    connect_calls = []
    init_db_calls = []
    monkeypatch.setattr(
        main.sidecar, "connect", lambda path: connect_calls.append(path) or fake_conn
    )
    monkeypatch.setattr(main.sidecar, "init_db", lambda conn: init_db_calls.append(conn))

    vikunja_calls = []
    vikunja_instances = []

    class _FakeVikunjaClient:
        def __init__(self, base_url, token, project_id):
            vikunja_calls.append((base_url, token, project_id))
            vikunja_instances.append(self)

    monkeypatch.setattr(main, "VikunjaClient", _FakeVikunjaClient)
    monkeypatch.setattr(main.llm, "build_client", lambda api_key: "fake-anthropic-client")

    fake_application = _FakeApplication()
    monkeypatch.setattr(
        main,
        "Application",
        type(
            "_Application",
            (),
            {"builder": staticmethod(lambda: _FakeApplicationBuilder(fake_application))},
        ),
    )

    main.main()

    # The Vikunja client was built from config, not hardcoded values.
    assert vikunja_calls == [("https://vikunja.example.com", "test-vikunja-token", 7)]
    # The sidecar connection feeding the Store is the one main() opened and initialized.
    assert connect_calls == [str(db_path)]
    assert init_db_calls == [fake_conn]

    store = fake_application.bot_data["store"]
    assert isinstance(store, Store)
    # Argument order matters: Store(vikunja, sidecar_conn), not the other way around.
    assert store._vk is vikunja_instances[0]
    assert store._conn is fake_conn
    assert fake_application.ran_polling
