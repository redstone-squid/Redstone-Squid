"""REST API locale negotiation and ambient request binding."""

from fastapi import Request
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from squid.core.i18n import DEFAULT_LOCALE, localization_for, negotiate_locale_candidates
from squid_ui.text import localization_scope


def _parse_accept_language(header: str) -> list[str]:
    """Parse an `Accept-Language` header into tags ordered by descending quality."""
    weighted: list[tuple[float, str]] = []
    for part in header.split(","):
        part = part.strip()
        if not part:
            continue
        tag, _sep, params = part.partition(";")
        tag = tag.strip()
        if not tag or tag == "*":
            continue
        quality = 1.0
        for param in params.split(";"):
            param = param.strip()
            if param.startswith("q="):
                try:
                    quality = float(param[2:])
                except ValueError:
                    quality = 1.0
        weighted.append((quality, tag))
    # Stable sort: equal-quality tags keep the header's original relative order.
    weighted.sort(key=lambda item: item[0], reverse=True)
    return [tag for _quality, tag in weighted]


def locale_for_request(request: Request) -> str:
    """Resolve the response locale for a FastAPI request from `Accept-Language`."""
    header = request.headers.get("accept-language")
    if not header:
        return DEFAULT_LOCALE
    preferred = _parse_accept_language(header)
    if not preferred:
        return DEFAULT_LOCALE
    return negotiate_locale_candidates(preferred)


class LocaleContextMiddleware:
    """Bind the negotiated localization for the duration of one HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        header = Headers(scope=scope).get("accept-language")
        preferred = [] if not header else _parse_accept_language(header)
        locale = negotiate_locale_candidates(preferred) if preferred else DEFAULT_LOCALE
        with localization_scope(localization_for(locale)):
            await self._app(scope, receive, send)
