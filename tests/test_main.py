import logging
from logging.handlers import RotatingFileHandler

from jolt import main


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
