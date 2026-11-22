"""Install OpenTelemetry middleware on the Litestar application."""

import logging
from typing import TYPE_CHECKING

from squid.core.observability import active_telemetry

if TYPE_CHECKING:
    from litestar import Litestar

    from squid.config import ObservabilityConfig

logger = logging.getLogger(__name__)


def instrument_api_app(app: Litestar, config: ObservabilityConfig) -> None:
    """Wrap the app when process telemetry and ASGI instrumentation are available."""
    if not config.enabled or active_telemetry() is None:
        return

    try:
        from opentelemetry.instrumentation.asgi import (  # pyright: ignore[reportMissingImports]
            OpenTelemetryMiddleware,
        )
    except ModuleNotFoundError as exc:
        if exc.name != "opentelemetry" and not (exc.name or "").startswith("opentelemetry."):
            raise
        logger.warning("OpenTelemetry ASGI instrumentation is unavailable.")
        return

    # OpenTelemetry types ASGI scopes as mappings; Litestar uses the equivalent TypedDicts.
    app.asgi_handler = OpenTelemetryMiddleware(app.asgi_handler)  # type: ignore[bad-assignment]
