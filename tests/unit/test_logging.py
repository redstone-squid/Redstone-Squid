"""Process logging configuration tests."""

import io
import json
import logging
import logging.config
from pathlib import Path

import pytest

from squid.config import LoggingConfig
from squid.core.errors import ConfigurationError
from squid.logging_config import build_logging_config, prepare_log_path, resolve_level


class TestResolveLevel:
    def test_accepts_standard_level_names(self) -> None:
        assert resolve_level("info") == 20
        assert resolve_level("WARNING") == 30

    def test_rejects_unknown_level_names(self) -> None:
        with pytest.raises(ConfigurationError, match="Invalid log level: loud"):
            resolve_level("loud")


class TestPrepareLogPath:
    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        resolved_path = prepare_log_path(tmp_path, "bot/discord.log")

        assert resolved_path is not None
        assert resolved_path == tmp_path / "bot" / "discord.log"
        assert resolved_path.parent.exists()

    def test_rejects_absolute_paths(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        absolute_path = (tmp_path / "discord.log").resolve()

        resolved_path = prepare_log_path(tmp_path, str(absolute_path))

        assert resolved_path is None
        captured = capsys.readouterr()
        assert "Absolute path" in captured.err


class TestBuildLoggingConfig:
    def test_uses_explicit_file_settings(
        self,
        tmp_path: Path,
    ) -> None:
        config = build_logging_config(
            config=LoggingConfig(
                level="INFO",
                root_level="WARNING",
                directory=tmp_path,
                log_file="custom.log",
                access_log_file="access.log",
            ),
            named_logger_levels={"squid": "INFO"},
            include_uvicorn_loggers=True,
        )

        handlers = config["handlers"]
        assert isinstance(handlers, dict)
        assert handlers["file"]["filename"] == str(tmp_path / "custom.log")
        assert handlers["access_file"]["filename"] == str(tmp_path / "access.log")

    def test_keeps_bot_default_file_when_env_is_unset(
        self,
        tmp_path: Path,
    ) -> None:
        config = build_logging_config(
            config=LoggingConfig(
                level="INFO",
                root_level="WARNING",
                directory=tmp_path,
                log_file="discord.log",
                access_log_file=None,
            ),
            named_logger_levels={"squid": "INFO"},
        )

        handlers = config["handlers"]
        assert isinstance(handlers, dict)
        assert handlers["file"]["filename"] == str(tmp_path / "discord.log")

    def test_routes_loggers_through_queue(self, tmp_path: Path) -> None:
        config = build_logging_config(
            config=LoggingConfig(
                level="INFO",
                root_level="WARNING",
                directory=tmp_path,
                log_file="discord.log",
                access_log_file=None,
            ),
            named_logger_levels={"squid": "INFO", "discord": "INFO"},
            use_queue=True,
        )

        handlers = config["handlers"]
        loggers = config["loggers"]
        root = config["root"]
        assert isinstance(handlers, dict)
        assert isinstance(loggers, dict)
        assert isinstance(root, dict)
        assert handlers["queue"]["handlers"] == ["console", "file"]
        assert root["handlers"] == ["queue"]
        assert loggers["squid"]["handlers"] == ["queue"]
        assert loggers["discord"]["handlers"] == ["queue"]

    def test_uses_json_formatters_outside_development(self, tmp_path: Path) -> None:
        config = build_logging_config(
            config=LoggingConfig(
                level="INFO",
                root_level="WARNING",
                directory=tmp_path,
                log_file="discord.log",
                access_log_file="access.log",
            ),
            include_uvicorn_loggers=True,
        )

        handlers = config["handlers"]
        assert isinstance(handlers, dict)
        assert handlers["console"]["formatter"] == "json"
        assert handlers["file"]["formatter"] == "json"
        assert handlers["access_console"]["formatter"] == "json_access"
        assert handlers["access_file"]["formatter"] == "json_access"

    def test_keeps_human_formatters_in_development(self, tmp_path: Path) -> None:
        config = build_logging_config(
            config=LoggingConfig(
                level="INFO",
                root_level="WARNING",
                directory=tmp_path,
                log_file="discord.log",
                access_log_file="access.log",
            ),
            include_uvicorn_loggers=True,
            development_mode=True,
        )

        handlers = config["handlers"]
        assert isinstance(handlers, dict)
        assert handlers["console"]["formatter"] == "default"
        assert handlers["access_console"]["formatter"] == "access"

    def test_json_formatter_serializes_structured_fields(self, tmp_path: Path) -> None:
        config = build_logging_config(
            config=LoggingConfig(
                level="INFO",
                root_level="INFO",
                directory=tmp_path,
                log_file=None,
                access_log_file=None,
            )
        )
        formatter_config = config["formatters"]
        assert isinstance(formatter_config, dict)
        logging.config.dictConfig(
            {
                "version": 1,
                "formatters": {"json": formatter_config["json"]},
                "handlers": {"stream": {"class": "logging.StreamHandler", "formatter": "json"}},
            }
        )
        stream = io.StringIO()
        handler = logging.getHandlerByName("stream")
        assert isinstance(handler, logging.StreamHandler)
        handler.setStream(stream)

        record = logging.LogRecord("squid.test", logging.INFO, __file__, 1, "Build %s submitted", (42,), None)
        setattr(record, "squid.build.id", 42)
        handler.handle(record)

        payload = json.loads(stream.getvalue())
        assert payload["levelname"] == "INFO"
        assert payload["name"] == "squid.test"
        assert payload["message"] == "Build 42 submitted"
        assert payload["squid.build.id"] == 42
