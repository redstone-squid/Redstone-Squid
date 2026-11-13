"""Build isolated native route applications with explicit dependency replacements."""

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import copy, deepcopy
from functools import partial
from typing import Any, cast

from httpx import ASGITransport
from litestar import Litestar, Request, Router
from litestar.di import Provide
from litestar.routes import HTTPRoute
from litestar.types import Empty, HTTPScope

from squid.api.dependencies import service_dependencies
from squid.api.errors import exception_handlers

type DependencyOverrides = dict[Callable[..., Any], Callable[..., Any]]


def asgi_transport(app: Litestar) -> ASGITransport:
    # HTTPX and Litestar describe the same ASGI interface with incompatible scope aliases.
    return ASGITransport(app=app)  # type: ignore


def make_request(scope: dict[str, Any], receive: Callable[[], Awaitable[Any]] | None = None) -> Request:
    if "route_handler" in scope:
        handler = copy(scope["route_handler"])
        handler.request_max_body_size = None
        scope = {**scope, "route_handler": handler}
    if receive is not None:
        return Request(cast(HTTPScope, scope), receive=receive)
    return Request(cast(HTTPScope, scope))


def route_app(
    routers: Sequence[Router],
    *,
    overrides: Mapping[Callable[..., Any], Callable[..., Any]],
    handle_errors: bool = True,
) -> Litestar:
    """Replace providers before registration without changing the production route instances."""
    dependencies = service_dependencies()

    def replacement(provider: Provide) -> Provide:
        callback = partial(overrides[provider.dependency]) if provider.dependency in overrides else provider.dependency
        return Provide(callback, sync_to_thread=None if inspect.iscoroutinefunction(callback) else False)

    copies = []
    for router in routers:
        cloned = deepcopy(router, {id(router.owner): None} if router.owner is not None else {})
        for route in cloned.routes:
            if not isinstance(route, HTTPRoute):
                continue
            for handler in route.route_handlers:
                handler.dependencies = {
                    name: replacement(provider if isinstance(provider, Provide) else Provide(provider))
                    for name, provider in (handler.dependencies or {}).items()
                }
                handler._resolved_dependencies = Empty
                handler._signature_model = Empty
        copies.append(cloned)
    return Litestar(
        route_handlers=copies,
        dependencies={name: replacement(provider) for name, provider in dependencies.items()},
        exception_handlers=exception_handlers() if handle_errors else None,
        openapi_config=None,
        logging_config=None,
        request_max_body_size=None,
    )
