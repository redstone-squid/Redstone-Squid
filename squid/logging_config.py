"""Central logging configuration for the Redstone Squid application."""

import logging
import logging.config
import sys
from collections.abc import Mapping
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path

from squid.config import LoggingConfig
from squid.core.errors import ConfigurationError

DEFAULT_LOG_LEVEL = "INFO"
"""Default log level for application loggers when SQUID_LOG_LEVEL is not set."""

DEFAULT_ROOT_LOG_LEVEL = "WARNING"
"""Default root log level when SQUID_ROOT_LOG_LEVEL is not set."""

DEFAULT_LOG_DIR_NAME = "logs"
"""Default directory used when SQUID_LOG_DIRECTORY is not set."""

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

JSON_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s %(created)s %(pathname)s %(lineno)s %(service_name)s"
"""Fields emitted for structured application and worker logs."""

JSON_ACCESS_LOG_FORMAT = f"{JSON_LOG_FORMAT} %(client_addr)s %(request_line)s %(status_code)s"
"""Fields emitted for structured uvicorn access logs."""

__all__ = [
    "build_logging_config",
    "configure_api_logging",
    "configure_bot_logging",
    "configure_service_worker_logging",
    "configure_worker_logging",
    "prepare_log_path",
    "resolve_level",
]


def resolve_level(level_name: str) -> int:
    """Convert a log level name to its corresponding logging constant."""
    level = logging.getLevelNamesMapping().get(level_name.upper())
    if level is None:
        msg = f"Invalid log level: {level_name}"
        raise ConfigurationError(msg, context={"log_level": level_name})
    return level


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
    config: LoggingConfig,
    named_logger_levels: Mapping[str, str] | None = None,
    include_uvicorn_loggers: bool = False,
    use_queue: bool = False,
    development_mode: bool = False,
    service_name: str = "redstone-squid",
) -> dict[str, object]:
    """Build a logging configuration dictionary for dictConfig."""
    level = resolve_level(config.level)
    root_level = resolve_level(config.root_level)
    resolved_log_file = prepare_log_path(config.directory, config.log_file)
    resolved_access_log_file = prepare_log_path(config.directory, config.access_log_file)

    default_formatter = "default" if development_mode else "json"
    access_formatter = "access" if development_mode else "json_access"
    handlers: dict[str, dict[str, object]] = {
        "console": {
            "class": "logging.StreamHandler",
            "level": level,
            "formatter": default_formatter,
            "stream": "ext://sys.stdout",
        },
    }
    output_handlers = ["console"]

    if resolved_log_file is not None:
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": level,
            "formatter": default_formatter,
            "filename": str(resolved_log_file),
            "maxBytes": DEFAULT_MAX_BYTES,
            "backupCount": DEFAULT_BACKUP_COUNT,
            "encoding": "utf-8",
        }
        output_handlers.append("file")

    base_handlers = output_handlers
    if use_queue:
        handlers["queue"] = {
            "class": "logging.handlers.QueueHandler",
            "handlers": output_handlers,
        }
        base_handlers = ["queue"]

    if include_uvicorn_loggers:
        handlers["access_console"] = {
            "class": "logging.StreamHandler",
            "level": level,
            "formatter": access_formatter,
            "stream": "ext://sys.stdout",
        }

    access_handlers = ["access_console"] if include_uvicorn_loggers else base_handlers.copy()

    if include_uvicorn_loggers and resolved_access_log_file is not None:
        handlers["access_file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": level,
            "formatter": access_formatter,
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
            "json": {
                "()": "pythonjsonlogger.json.JsonFormatter",
                "format": JSON_LOG_FORMAT,
                "static_fields": {"service_name": service_name},
            },
            "json_access": {
                "()": "pythonjsonlogger.json.JsonFormatter",
                "format": JSON_ACCESS_LOG_FORMAT,
                "static_fields": {"service_name": service_name},
            },
        },
        "handlers": handlers,
        "loggers": loggers,
        "root": {
            "level": root_level,
            "handlers": base_handlers,
        },
    }


def configure_bot_logging(config: LoggingConfig, *, dev_mode: bool = False) -> QueueListener:
    """Configure logging for the Discord bot process."""
    named_logger_levels = {
        "discord": DEFAULT_LOG_LEVEL,
        "squid": DEFAULT_LOG_LEVEL,
    }

    if dev_mode:
        named_logger_levels["discord.gateway"] = "ERROR"
        named_logger_levels["sqlalchemy.engine.Engine"] = "WARNING"

    logging.config.dictConfig(
        build_logging_config(
            config=config,
            named_logger_levels=named_logger_levels,
            use_queue=True,
            development_mode=dev_mode,
            service_name="redstone-squid-bot",
        )
    )
    queue_handler = logging.getHandlerByName("queue")
    if not isinstance(queue_handler, QueueHandler):
        msg = "Queue-backed logging configuration did not create a queue handler"
        raise TypeError(msg)
    if not isinstance(queue_handler.listener, QueueListener):
        msg = "Queue-backed logging configuration did not create a queue listener"
        raise TypeError(msg)

    queue_handler.listener.start()
    return queue_handler.listener


def configure_api_logging(config: LoggingConfig) -> None:
    """Configure logging for the FastAPI and uvicorn process."""
    logging.config.dictConfig(
        build_logging_config(
            config=config,
            named_logger_levels={"squid": DEFAULT_LOG_LEVEL},
            include_uvicorn_loggers=True,
            service_name="redstone-squid-api",
        )
    )


def configure_service_worker_logging(config: LoggingConfig) -> None:
    """Configure logging for the long-lived database worker process."""
    logging.config.dictConfig(
        build_logging_config(
            config=config,
            named_logger_levels={"squid": DEFAULT_LOG_LEVEL},
            service_name="redstone-squid-worker",
        )
    )


def configure_worker_logging() -> None:
    """Configure JSON logging to stderr for a schematic worker child."""
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {
                    "()": "pythonjsonlogger.json.JsonFormatter",
                    "format": JSON_LOG_FORMAT,
                    "static_fields": {"service_name": "redstone-squid-schematic-worker"},
                }
            },
            "handlers": {
                "stderr": {
                    "class": "logging.StreamHandler",
                    "level": "DEBUG",
                    "formatter": "json",
                    "stream": "ext://sys.stderr",
                }
            },
            "root": {"level": "DEBUG", "handlers": ["stderr"]},
        }
    )
