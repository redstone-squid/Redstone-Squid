"""Suggestion service tests.

These pin the two rules that make the service safe to call on every keystroke: it stays within its
limits and it does not surface failures the user cannot act on.
"""

import anyio
import pytest

from squid.core.errors import InvalidStateError
from squid.suggestions.application import (
    Candidate,
    SuggestionRegistry,
    SuggestionService,
    SuggestionSource,
    UnknownSuggestionSourceError,
    candidate,
    content_revision,
)
from squid.suggestions.domain import (
    MAX_QUERY_LENGTH,
    MAX_SUGGESTIONS,
    SourceKind,
    Suggestion,
    SuggestionRequest,
    SuggestionResult,
    SuggestionViewer,
    Visibility,
)
from squid.suggestions.infrastructure.providers import StaticProvider

NAMES = [f"restriction_{index:02d}" for index in range(40)]


class RecordingProvider:
    def __init__(self, candidates: tuple[Candidate, ...] = ()) -> None:
        self.candidates_returned = candidates
        self.requests: list[SuggestionRequest] = []

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        self.requests.append(request)
        return self.candidates_returned


class FailingProvider:
    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        msg = "the database is on fire"
        raise RuntimeError(msg)


class HangingProvider:
    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        await anyio.sleep(30)
        return ()


class AllowingAuthorizer:
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed
        self.nodes: list[str] = []

    async def allows(self, node: str) -> bool:
        self.nodes.append(node)
        return self.allowed


def service_for(source: SuggestionSource, *, timeout_seconds: float = 2.0) -> SuggestionService:
    return SuggestionService(SuggestionRegistry.of((source,)), timeout_seconds=timeout_seconds)


def gated_source(node: str = "build.submission.view_pending") -> SuggestionSource:
    return SuggestionSource(
        id="restrictions",
        provider=StaticProvider.of(NAMES),
        visibility=Visibility.REQUIRES_NODE,
        required_node=node,
    )


async def test_unknown_source_raises_because_it_is_a_bad_url_not_a_transient_failure() -> None:
    service = service_for(SuggestionSource(id="restrictions", provider=StaticProvider.of(NAMES)))
    with pytest.raises(UnknownSuggestionSourceError):
        await service.suggest(SuggestionRequest(source="nope"))


async def test_provider_failure_returns_empty_instead_of_raising() -> None:
    service = service_for(SuggestionSource(id="restrictions", provider=FailingProvider()))
    assert await service.suggest(SuggestionRequest(source="restrictions")) == SuggestionResult()


async def test_a_hanging_provider_is_bounded_and_returns_empty() -> None:
    service = service_for(SuggestionSource(id="restrictions", provider=HangingProvider()), timeout_seconds=0.05)
    with anyio.fail_after(5):
        result = await service.suggest(SuggestionRequest(source="restrictions"))
    assert result.items == ()


async def test_limit_is_clamped_to_discords_ceiling() -> None:
    service = service_for(SuggestionSource(id="restrictions", provider=StaticProvider.of(NAMES)))
    result = await service.suggest(SuggestionRequest(source="restrictions", limit=500))
    assert len(result.items) == MAX_SUGGESTIONS


async def test_query_is_truncated_before_reaching_a_provider() -> None:
    provider = RecordingProvider()
    service = service_for(SuggestionSource(id="restrictions", provider=provider))
    await service.suggest(SuggestionRequest(source="restrictions", query="x" * 5_000))
    assert len(provider.requests[0].query) == MAX_QUERY_LENGTH


async def test_cursor_is_clamped_into_the_truncated_query() -> None:
    provider = RecordingProvider()
    service = service_for(SuggestionSource(id="restrictions", provider=provider))
    await service.suggest(SuggestionRequest(source="restrictions", query="x" * 5_000, cursor=4_000))
    assert provider.requests[0].cursor == MAX_QUERY_LENGTH


async def test_gated_source_is_refused_without_an_authorizer() -> None:
    source = gated_source()
    result = await service_for(source).suggest(SuggestionRequest(source="restrictions"))
    assert result.items == ()


async def test_gated_source_is_refused_when_the_authorizer_denies() -> None:
    source = gated_source()
    authorizer = AllowingAuthorizer(allowed=False)
    result = await service_for(source).suggest(SuggestionRequest(source="restrictions"), authorizer=authorizer)
    assert result.items == ()
    assert authorizer.nodes == ["build.submission.view_pending"]


async def test_gated_source_is_served_when_the_authorizer_allows() -> None:
    source = gated_source()
    authorizer = AllowingAuthorizer(allowed=True)
    result = await service_for(source).suggest(SuggestionRequest(source="restrictions"), authorizer=authorizer)
    assert len(result.items) == MAX_SUGGESTIONS


async def test_viewer_scoped_source_is_refused_for_an_anonymous_caller() -> None:
    source = SuggestionSource(id="restrictions", provider=StaticProvider.of(NAMES), visibility=Visibility.VIEWER_SCOPED)
    service = service_for(source)
    assert (await service.suggest(SuggestionRequest(source="restrictions"))).items == ()
    signed_in = SuggestionRequest(source="restrictions", viewer=SuggestionViewer(account_id=7))
    assert (await service.suggest(signed_in)).items


async def test_missing_required_context_returns_empty_rather_than_a_wrong_answer() -> None:
    provider = RecordingProvider()
    source = SuggestionSource(id="restrictions", provider=provider, context_keys=frozenset({"category"}))
    result = await service_for(source).suggest(SuggestionRequest(source="restrictions"))
    assert result.items == ()
    assert provider.requests == []


async def test_enumerable_sources_carry_a_content_revision() -> None:
    source = SuggestionSource(id="restrictions", provider=StaticProvider.of(NAMES), kind=SourceKind.ENUMERABLE)
    result = await service_for(source).suggest(SuggestionRequest(source="restrictions"))
    assert result.revision == content_revision(result.items)


async def test_queried_sources_carry_no_revision_because_the_set_is_not_stable() -> None:
    source = SuggestionSource(id="restrictions", provider=StaticProvider.of(NAMES))
    assert (await service_for(source).suggest(SuggestionRequest(source="restrictions"))).revision is None


async def test_enumerate_returns_the_whole_set_unranked() -> None:
    source = SuggestionSource(id="restrictions", provider=StaticProvider.of(NAMES), kind=SourceKind.ENUMERABLE)
    result = await service_for(source).enumerate("restrictions")
    assert [item.value for item in result.items] == NAMES


async def test_enumerate_refuses_a_queried_source() -> None:
    service = service_for(SuggestionSource(id="restrictions", provider=StaticProvider.of(NAMES)))
    with pytest.raises(ValueError, match="cannot be enumerated"):
        await service.enumerate("restrictions")


async def test_source_kind_label_is_stamped_onto_unlabelled_candidates() -> None:
    provider = RecordingProvider((candidate("seamless", "Seamless"),))
    source = SuggestionSource(id="restrictions", provider=provider, kind_label="restriction")
    result = await service_for(source).suggest(SuggestionRequest(source="restrictions"))
    assert result.items[0].kind == "restriction"


async def test_a_candidates_own_kind_wins_over_the_source_default() -> None:
    provider = RecordingProvider((Candidate(Suggestion("a", "A", kind="alias")),))
    source = SuggestionSource(id="restrictions", provider=provider, kind_label="restriction")
    result = await service_for(source).suggest(SuggestionRequest(source="restrictions"))
    assert result.items[0].kind == "alias"


def test_content_revision_is_stable_and_content_addressed() -> None:
    first = (Suggestion("a", "A"), Suggestion("b", "B"))
    assert content_revision(first) == content_revision(first)
    assert content_revision(first) != content_revision((Suggestion("a", "A"),))
    assert content_revision(first) != content_revision(tuple(reversed(first)))
    # Never zero, so a revision is always distinguishable from "unset" on the wire.
    assert content_revision(()) > 0


def test_a_source_requiring_a_node_must_name_one() -> None:
    with pytest.raises(InvalidStateError, match="required_node"):
        SuggestionSource(id="x", provider=StaticProvider.of([]), visibility=Visibility.REQUIRES_NODE)


def test_a_public_source_may_not_name_a_node() -> None:
    with pytest.raises(InvalidStateError, match="required_node"):
        SuggestionSource(id="x", provider=StaticProvider.of([]), required_node="some.node")


def test_source_ids_must_be_addressable_as_form_option_sources() -> None:
    with pytest.raises(InvalidStateError, match="invalid suggestion source id"):
        SuggestionSource(id="Not-Valid", provider=StaticProvider.of([]))


def test_duplicate_source_ids_fail_at_startup() -> None:
    source = SuggestionSource(id="dupe", provider=StaticProvider.of([]))
    with pytest.raises(InvalidStateError, match="duplicate suggestion source"):
        SuggestionRegistry.of((source, source))
