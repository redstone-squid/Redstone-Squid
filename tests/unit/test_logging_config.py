from pathlib import Path

import pytest

from squid.logging_config import build_logging_config, prepare_log_path, resolve_level


class TestResolveLevel:
    def test_accepts_standard_level_names(self) -> None:
        assert resolve_level("info") == 20
        assert resolve_level("WARNING") == 30

    def test_rejects_unknown_level_names(self) -> None:
        with pytest.raises(ValueError, match="Invalid log level: loud"):
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
    def test_uses_env_overrides_for_files(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("LOG_DIR", str(tmp_path))
        monkeypatch.setenv("LOG_FILE", "custom.log")
        monkeypatch.setenv("LOG_ACCESS_FILE", "access.log")

        config = build_logging_config(
            root_level_name="WARNING",
            named_logger_levels={"squid": "INFO"},
            default_log_file="discord.log",
            include_uvicorn_loggers=True,
        )

        handlers = config["handlers"]
        assert isinstance(handlers, dict)
        assert handlers["file"]["filename"] == str(tmp_path / "custom.log")
        assert handlers["access_file"]["filename"] == str(tmp_path / "access.log")

    def test_keeps_bot_default_file_when_env_is_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("LOG_DIR", str(tmp_path))

        config = build_logging_config(
            root_level_name="WARNING",
            named_logger_levels={"squid": "INFO"},
            default_log_file="discord.log",
        )

        handlers = config["handlers"]
        assert isinstance(handlers, dict)
        assert handlers["file"]["filename"] == str(tmp_path / "discord.log")
