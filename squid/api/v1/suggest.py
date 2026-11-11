"""Typeahead completions for any registered suggestion source.

Clients name a registered source to request completions. A new source uses the
same route and response model, so clients can discover it without a new endpoint.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

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

router = APIRouter(prefix="/suggest", tags=["suggest"])

SourceId = Annotated[str, Query(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
CACHE_SECONDS = 30
"""Short enough that an approved tag shows up promptly, long enough to absorb a burst of typing."""


@router.get(
    "",
    response_model=list[SuggestionSourceInfo],
    responses=responses(503),
    operation_id="suggestion_sources_list",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def list_sources(suggestions: Suggestions) -> list[SuggestionSourceInfo]:
    """Publish the registered sources and how to drive each one."""
    return [SuggestionSourceInfo.from_domain(source) for source in sorted(suggestions.registry, key=lambda s: s.id)]


@router.get(
    "/{source}",
    response_model=SuggestionPage,
    responses=responses(404, 422, 503),
    operation_id="suggestions_get",
    openapi_extra=contract(security=[ANONYMOUS], cli=transport_only()),
)
async def suggest(
    source: Annotated[str, SourceId],
    request: Request,
    response: Response,
    suggestions: Suggestions,
    caller: CurrentCaller,
    q: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=MAX_SUGGESTIONS)] = 10,
    cursor: Annotated[int | None, Query(ge=0)] = None,
    category: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]{0,63}$")] = None,
) -> SuggestionPage:
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
    if definition.kind is SourceKind.ENUMERABLE and result.revision is not None:
        # Enumerable sets are content-addressed, so a client that already holds this revision can
        # skip re-rendering. Private because gated sources vary by caller.
        response.headers["ETag"] = f'"{result.revision:x}"'
        response.headers["Cache-Control"] = f"private, max-age={CACHE_SECONDS}"
    return SuggestionPage.from_domain(source, result)


class _CallerAuthorizer:
    """Answer permission questions for an HTTP caller."""

    def __init__(self, request: Request, caller: Caller) -> None:
        self._request = request
        self._caller = caller

    async def allows(self, node: str) -> bool:
        permissions = self._request.app.state.runtime.services.permissions
        return await self._caller.allows(permissions, CATALOGUE[node])


__all__ = ["SuggestionService", "router"]
