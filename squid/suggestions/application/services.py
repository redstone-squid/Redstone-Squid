"""The suggestion application service.

Every caller of this service is a keystroke, which drives two rules the rest of the application
does not have. It never raises for a failure the user cannot act on — a broken provider shows an
empty dropdown, not a red error under a half-typed word — and it is bounded in time, because a
suggestion that arrives after the user has finished typing is worse than none.
"""

import hashlib
import logging
from collections.abc import Sequence

import anyio

from squid.core.errors import ValidationError
from squid.core.i18n import _
from squid.observability import add_counter, trace_span
from squid.suggestions.application.matching import rank
from squid.suggestions.application.ports import ComposedSuggestionProvider, SuggestionAuthorizer
from squid.suggestions.application.registry import SuggestionRegistry, SuggestionSource
from squid.suggestions.domain import (
    MAX_QUERY_LENGTH,
    MAX_SUGGESTIONS,
    SourceKind,
    Suggestion,
    SuggestionRequest,
    SuggestionResult,
    Visibility,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 2.0
"""Upper bound on any one provider. Surfaces with a tighter budget pass their own."""


class SuggestionService:
    """Rank, bound, and gate candidate completions from registered sources."""

    def __init__(
        self,
        registry: SuggestionRegistry,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._registry = registry
        self._timeout_seconds = timeout_seconds

    @property
    def registry(self) -> SuggestionRegistry:
        """The registry this service resolves against."""
        return self._registry

    async def suggest(
        self,
        request: SuggestionRequest,
        *,
        authorizer: SuggestionAuthorizer | None = None,
    ) -> SuggestionResult:
        """Return ranked completions for a partially typed value.

        Raises `UnknownSuggestionSourceError` for an unregistered source, because that is a caller
        bug or a bad URL rather than a transient failure. Everything else resolves to empty.
        """
        source = self._registry.resolve(request.source)
        normalized = _normalize(request)
        with trace_span("suggestions.suggest", {"squid.suggestion.source": source.id}) as span:
            if not await self._permitted(source, normalized, authorizer):
                add_counter("suggestions.denied", attributes={"squid.suggestion.source": source.id})
                return SuggestionResult()
            if missing := source.context_keys - normalized.context.keys():
                logger.warning(
                    "Suggestion request missing required context",
                    extra={"source": source.id, "missing": sorted(missing)},
                )
                return SuggestionResult()
            try:
                with anyio.fail_after(self._timeout_seconds):
                    return await self._produce(source, normalized)
            except TimeoutError:
                span.set_error()
                add_counter("suggestions.timeout", attributes={"squid.suggestion.source": source.id})
                logger.warning("Suggestion source timed out", extra={"source": source.id})
            except Exception:
                span.set_error()
                add_counter("suggestions.failed", attributes={"squid.suggestion.source": source.id})
                logger.exception("Suggestion source failed", extra={"source": source.id})
            return SuggestionResult()

    async def enumerate(
        self,
        source_id: str,
        *,
        context: dict[str, str] | None = None,
        locale: str | None = None,
    ) -> SuggestionResult:
        """Return an enumerable source's full candidate set with its content revision.

        This is what serves form option sets and `ETag`-able reads, as opposed to `suggest`, which
        answers one keystroke.
        """
        source = self._registry.resolve(source_id)
        if source.kind is not SourceKind.ENUMERABLE:
            msg = _("{source_id} cannot be enumerated")
            raise ValidationError(msg, message_params={"source_id": source_id})
        request = SuggestionRequest(
            source=source_id,
            limit=0,
            context=context or {},
            locale=locale,
        )
        return await self.suggest(request)

    async def _produce(self, source: SuggestionSource, request: SuggestionRequest) -> SuggestionResult:
        provider = source.provider
        # A composed provider has already decided its own order and span — typically because a
        # database ranked it — so it is passed through rather than re-ranked here.
        if isinstance(provider, ComposedSuggestionProvider):
            return _labelled(await provider.suggest(request), source)
        candidates = await provider.candidates(request)
        # `limit=0` means enumerate: keep the provider's full ordered set rather than ranking it.
        items = (
            tuple(item.suggestion for item in candidates)
            if request.limit == 0
            else rank(request.query, candidates, limit=request.limit)
        )
        revision = content_revision(items) if source.kind is SourceKind.ENUMERABLE else None
        return _labelled(SuggestionResult(items=items, revision=revision), source)

    async def _permitted(
        self,
        source: SuggestionSource,
        request: SuggestionRequest,
        authorizer: SuggestionAuthorizer | None,
    ) -> bool:
        match source.visibility:
            case Visibility.PUBLIC:
                return True
            case Visibility.VIEWER_SCOPED:
                return request.viewer.account_id is not None
            case Visibility.REQUIRES_NODE:
                assert source.required_node is not None
                if authorizer is None:
                    logger.warning(
                        "Gated suggestion source requested without an authorizer",
                        extra={"source": source.id, "node": source.required_node},
                    )
                    return False
                return await authorizer.allows(source.required_node)


def content_revision(items: Sequence[Suggestion]) -> int:
    """Derive a stable, content-addressed revision for a candidate set.

    Deterministic across processes and restarts so two API replicas issue the same `ETag`, and so a
    client can tell an unchanged option set from a re-fetched one.
    """
    payload = "\n".join(f"{item.value}\0{item.label}" for item in items).encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") or 1


def _normalize(request: SuggestionRequest) -> SuggestionRequest:
    limit = request.limit if request.limit == 0 else max(1, min(request.limit, MAX_SUGGESTIONS))
    query = request.query[:MAX_QUERY_LENGTH]
    if limit == request.limit and query == request.query:
        return request
    cursor = None if request.cursor is None else min(request.cursor, len(query))
    return SuggestionRequest(
        source=request.source,
        query=query,
        limit=limit,
        context=request.context,
        locale=request.locale,
        viewer=request.viewer,
        cursor=cursor,
    )


def _labelled(result: SuggestionResult, source: SuggestionSource) -> SuggestionResult:
    """Stamp the source's default kind onto candidates that did not set one."""
    if not source.kind_label or all(item.kind for item in result.items):
        return result
    items = tuple(
        item if item.kind else Suggestion(item.value, item.label, item.description, source.kind_label)
        for item in result.items
    )
    return SuggestionResult(items=items, revision=result.revision, replacement=result.replacement)
