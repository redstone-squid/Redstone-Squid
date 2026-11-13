"""Typeahead completions for any registered suggestion source.

Clients name a registered source to request completions. A new source uses the
same route and response model, so clients can discover it without a new endpoint.
"""

from typing import Annotated

from litestar import Request, Response, Router, get
from litestar.params import PathParameter, QueryParameter

from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.dependencies import CurrentCaller, Suggestions
from squid.api.errors import responses
from squid.api.security import Caller
from squid.api.v1.schemas.suggest import SuggestionPage, SuggestionSourceInfo
from squid.core.actors import Actor
from squid.core.i18n import negotiate_locale
from squid.permissions.domain.catalogue import CATALOGUE
from squid.suggestions.application import SuggestionService
from squid.suggestions.domain import MAX_SUGGESTIONS, SourceKind, SuggestionRequest

SourceId = Annotated[str, PathParameter(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
CACHE_SECONDS = 30
"""Short enough that an approved tag shows up promptly, long enough to absorb a burst of typing."""


@get(
    "/",
    responses=responses(503),
    operation_id="suggestion_sources_list",
    opt=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def list_sources(suggestions: Suggestions) -> list[SuggestionSourceInfo]:
    """Publish the registered sources and how to drive each one."""
    return [SuggestionSourceInfo.from_domain(source) for source in sorted(suggestions.registry, key=lambda s: s.id)]


@get(
    "/{source:str}",
    responses=responses(404, 422, 503),
    operation_id="suggestions_get",
    opt=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def suggest(
    source: SourceId,
    request: Request,
    suggestions: Suggestions,
    caller: CurrentCaller,
    q: Annotated[str, QueryParameter(max_length=200)] = "",
    limit: Annotated[int, QueryParameter(ge=1, le=MAX_SUGGESTIONS)] = 10,
    cursor: Annotated[int | None, QueryParameter(ge=0)] = None,
    category: Annotated[str | None, QueryParameter(pattern=r"^[a-z][a-z0-9_]{0,63}$")] = None,
) -> Response[SuggestionPage]:
    """Return ranked completions for a partially typed value.

    Unknown sources return 404. Sources hidden from the caller and provider
    failures return an empty list. Request validation errors still return errors.
    """
    definition = suggestions.registry.resolve(source)
    result = await suggestions.suggest(
        SuggestionRequest(
            source=source,
            query=q,
            limit=limit,
            context={} if category is None else {"category": category},
            locale=negotiate_locale(request.headers.get("accept-language")),
            viewer=Actor(account_id=caller.account_id),
            cursor=cursor,
        ),
        authorizer=_CallerAuthorizer(request, caller),
    )
    headers: dict[str, str] = {}
    if definition.kind is SourceKind.ENUMERABLE and result.revision is not None:
        # Enumerable sets are content-addressed, so a client that already holds this revision can
        # skip re-rendering. Private because gated sources vary by caller.
        headers["ETag"] = f'"{result.revision:x}"'
        headers["Cache-Control"] = f"private, max-age={CACHE_SECONDS}"
    return Response(SuggestionPage.from_domain(source, result), headers=headers)


class _CallerAuthorizer:
    """Answer permission questions for an HTTP caller."""

    def __init__(self, request: Request, caller: Caller) -> None:
        self._request = request
        self._caller = caller

    async def allows(self, node: str) -> bool:
        permissions = self._request.app.state.runtime.services.permissions
        return await self._caller.allows(permissions, CATALOGUE[node])


__all__ = ["SuggestionService", "router"]


router = Router(path="/suggest", tags=["suggest"], route_handlers=[list_sources, suggest])
