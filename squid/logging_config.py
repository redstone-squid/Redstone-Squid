"""Central logging configuration for the Redstone Squid application."""

import logging
import logging.config
import os
import sys
from collections.abc import Mapping
from pathlib import Path

DEFAULT_LOG_LEVEL = "INFO"
"""Default log level for application loggers when LOG_LEVEL is not set."""

DEFAULT_ROOT_LOG_LEVEL = "WARNING"
"""Default root log level when ROOT_LOG_LEVEL is not set."""

DEFAULT_LOG_DIR_NAME = "logs"
"""Default directory used when LOG_DIR is not set."""

DEFAULT_DISCORD_LOG_FILE = "discord.log"
"""Default log file for the Discord bot process."""

DEFAULT_MAX_BYTES = 32 * 1024 * 1024
"""Maximum log file size in bytes before rotation."""

DEFAULT_BACKUP_COUNT = 5
"""Number of rotated log files to keep."""

DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
"""Timestamp format for log records."""

DEFAULT_LOG_FORMAT = "[%(asctime)s] [%(levelname)-8s] %(name)s: %(message)s"
"""Default format for non-access log records."""

DEFAULT_ACCESS_LOG_FORMAT = (
    '[%(asctime)s] [%(levelname)-8s] %(name)s: %(client_addr)s - "%(request_line)s" %(status_code)s'
)
"""Format for uvicorn access log records."""

__all__ = [
    "build_logging_config",
    "configure_api_logging",
    "configure_bot_logging",
    "prepare_log_path",
    "resolve_level",
]


def resolve_level(level_name: str) -> int:
    """Convert a log level name to its corresponding logging constant."""
    level = logging.getLevelNamesMapping().get(level_name.upper())
    if level is None:
        msg = f"Invalid log level: {level_name}"
        raise ValueError(msg)
    return level


def _read_env(env_name: str, default: str | None = None) -> str | None:
    """Read an environment variable while preserving empty-string overrides."""
    value = os.environ.get(env_name)
    return default if value is None else value


def prepare_log_path(log_dir: Path, path_str: str | None) -> Path | None:
    """Prepare a relative log path beneath the configured log directory."""
    if not path_str:
        return None

    path = Path(path_str)
    if path.is_absolute():
        print(
            f"Warning: Absolute path '{path_str}' provided for log file. "
            f"Log paths must be relative to the log directory ({log_dir}). "
            "File logging for this path will be disabled.",
            file=sys.stderr,
        )
        return None

    path = log_dir / path

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"Warning: Could not prepare log directory at {path.parent}: {exc}. "
            "File logging for this path will be disabled.",
            file=sys.stderr,
        )
        return None

    return path


def build_logging_config(
    *,
    root_level_name: str = DEFAULT_ROOT_LOG_LEVEL,
    named_logger_levels: Mapping[str, str] | None = None,
    default_log_file: str | None = None,
    default_access_log_file: str | None = None,
    include_uvicorn_loggers: bool = False,
) -> dict[str, object]:
    """Build a logging configuration dictionary for dictConfig."""
    level_name = _read_env("LOG_LEVEL", DEFAULT_LOG_LEVEL)
    if level_name is None:
        level_name = DEFAULT_LOG_LEVEL
    level = resolve_level(level_name)

    resolved_root_level_name = _read_env("ROOT_LOG_LEVEL", root_level_name)
    if resolved_root_level_name is None:
        resolved_root_level_name = DEFAULT_ROOT_LOG_LEVEL
    root_level = resolve_level(resolved_root_level_name)

    log_dir_env = _read_env("LOG_DIR")
    log_dir = Path(log_dir_env) if log_dir_env else Path.cwd() / DEFAULT_LOG_DIR_NAME

    resolved_log_file = prepare_log_path(log_dir, _read_env("LOG_FILE", default_log_file))
    resolved_access_log_file = prepare_log_path(log_dir, _read_env("LOG_ACCESS_FILE", default_access_log_file))

    handlers: dict[str, dict[str, object]] = {
        "console": {
            "class": "logging.StreamHandler",
            "level": level,
            "formatter": "default",
            "stream": "ext://sys.stdout",
        },
    }
    base_handlers = ["console"]

    if resolved_log_file is not None:
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": level,
            "formatter": "default",
            "filename": str(resolved_log_file),
            "maxBytes": DEFAULT_MAX_BYTES,
            "backupCount": DEFAULT_BACKUP_COUNT,
            "encoding": "utf-8",
        }
        base_handlers.append("file")

    if include_uvicorn_loggers:
        handlers["access_console"] = {
            "class": "logging.StreamHandler",
            "level": level,
            "formatter": "access",
            "stream": "ext://sys.stdout",
        }

    access_handlers = ["access_console"] if include_uvicorn_loggers else base_handlers.copy()

    if include_uvicorn_loggers and resolved_access_log_file is not None:
        handlers["access_file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": level,
            "formatter": "access",
            "filename": str(resolved_access_log_file),
            "maxBytes": DEFAULT_MAX_BYTES,
            "backupCount": DEFAULT_BACKUP_COUNT,
            "encoding": "utf-8",
        }
        access_handlers.append("access_file")

    loggers: dict[str, dict[str, object]] = {}
    if named_logger_levels is not None:
        for logger_name, logger_level_name in named_logger_levels.items():
            loggers[logger_name] = {
                "level": resolve_level(logger_level_name),
                "handlers": base_handlers,
                "propagate": False,
            }

    if include_uvicorn_loggers:
        loggers["uvicorn"] = {
            "level": level,
            "handlers": base_handlers,
            "propagate": False,
        }
        loggers["uvicorn.error"] = {
            "level": level,
            "handlers": base_handlers,
            "propagate": False,
        }
        loggers["uvicorn.access"] = {
            "level": level,
            "handlers": access_handlers,
            "propagate": False,
        }

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": DEFAULT_LOG_FORMAT,
                "datefmt": DEFAULT_DATE_FORMAT,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": DEFAULT_ACCESS_LOG_FORMAT,
                "datefmt": DEFAULT_DATE_FORMAT,
                "use_colors": False,
            },
        },
        "handlers": handlers,
        "loggers": loggers,
        "root": {
            "level": root_level,
            "handlers": base_handlers,
        },
    }


def configure_bot_logging(dev_mode: bool = False) -> None:
    """Configure logging for the Discord bot process."""
    root_level_name = DEFAULT_LOG_LEVEL if dev_mode else DEFAULT_ROOT_LOG_LEVEL
    named_logger_levels = {
        "discord": DEFAULT_LOG_LEVEL,
        "squid": DEFAULT_LOG_LEVEL,
    }

    if dev_mode:
        named_logger_levels["discord.gateway"] = "ERROR"
        named_logger_levels["sqlalchemy.engine.Engine"] = "WARNING"

    logging.config.dictConfig(
        build_logging_config(
            root_level_name=root_level_name,
            named_logger_levels=named_logger_levels,
            default_log_file=DEFAULT_DISCORD_LOG_FILE,
        )
    )


def configure_api_logging() -> None:
    """Configure logging for the FastAPI and uvicorn process."""
    logging.config.dictConfig(
        build_logging_config(
            root_level_name=DEFAULT_LOG_LEVEL,
            named_logger_levels={"squid": DEFAULT_LOG_LEVEL},
            include_uvicorn_loggers=True,
        )
    )
