"""Discord autocomplete adapter tests.

Autocomplete runs outside the command tree's checks and error handling, so these pin the
properties that would otherwise be nobody's responsibility: it stays inside Discord's limits, it
does not leak gated data, and it never propagates a failure.
"""

import anyio

from squid.bot.utils.autocomplete import CHOICE_NAME_LIMIT, suggests
from squid.suggestions.application import (
    Candidate,
    SuggestionRegistry,
    SuggestionService,
    SuggestionSource,
    candidate,
)
from squid.suggestions.domain import Suggestion, SuggestionRequest, ValueType, Visibility
from squid.suggestions.infrastructure.providers import StaticProvider
from tests.helpers.discord import make_autocomplete_interaction

VIEW_PENDING = "build.submission.view_pending"

RESTRICTIONS = ["seamless", "semi_seamless", "full_lamp", "flush"]


class BuildProvider:
    """Stands in for the projection-backed build source, which is integer-valued."""

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        return (candidate("412", "#412 — 4x4 flush TNT door"),)


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


class RecordingProvider:
    def __init__(self, candidates: tuple[Candidate, ...] = ()) -> None:
        self.returned = candidates
        self.requests: list[SuggestionRequest] = []

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        self.requests.append(request)
        return self.returned


def service_with(*sources: SuggestionSource, timeout_seconds: float = 2.0) -> SuggestionService:
    return SuggestionService(SuggestionRegistry.of(sources), timeout_seconds=timeout_seconds)


def restriction_source() -> SuggestionSource:
    return SuggestionSource(
        id="approved_restrictions",
        provider=StaticProvider.of(RESTRICTIONS),
        multi_value=",",
    )


async def test_choices_carry_labels_and_values() -> None:
    interaction = make_autocomplete_interaction(service_with(restriction_source()))
    choices = await suggests("approved_restrictions")(interaction, "seam")
    assert [(choice.name, choice.value) for choice in choices] == [
        ("seamless", "seamless"),
        ("semi_seamless", "semi_seamless"),
    ]


async def test_an_integer_source_yields_integer_choice_values() -> None:
    source = SuggestionSource(id="builds", provider=BuildProvider(), value_type=ValueType.INTEGER)
    interaction = make_autocomplete_interaction(service_with(source))
    (choice,) = await suggests("builds")(interaction, "412")
    assert choice.value == 412
    assert isinstance(choice.value, int)


async def test_an_unregistered_source_returns_empty_instead_of_raising() -> None:
    interaction = make_autocomplete_interaction(service_with(restriction_source()))
    assert await suggests("no_such_source")(interaction, "x") == []


async def test_a_failing_provider_returns_empty() -> None:
    source = SuggestionSource(id="approved_restrictions", provider=FailingProvider())
    interaction = make_autocomplete_interaction(service_with(source))
    assert await suggests("approved_restrictions")(interaction, "seam") == []


async def test_a_hanging_provider_is_bounded_by_the_response_budget() -> None:
    source = SuggestionSource(id="approved_restrictions", provider=HangingProvider())
    interaction = make_autocomplete_interaction(service_with(source, timeout_seconds=0.05))
    with anyio.fail_after(5):
        assert await suggests("approved_restrictions")(interaction, "seam") == []


async def test_a_gated_source_is_empty_for_a_user_without_the_node() -> None:
    source = SuggestionSource(
        id="builds_pending",
        provider=BuildProvider(),
        visibility=Visibility.REQUIRES_NODE,
        required_node=VIEW_PENDING,
        value_type=ValueType.INTEGER,
    )
    interaction = make_autocomplete_interaction(service_with(source))
    assert await suggests("builds_pending")(interaction, "412") == []


async def test_a_gated_source_is_served_to_a_user_holding_the_node() -> None:
    source = SuggestionSource(
        id="builds_pending",
        provider=BuildProvider(),
        visibility=Visibility.REQUIRES_NODE,
        required_node=VIEW_PENDING,
        value_type=ValueType.INTEGER,
    )
    interaction = make_autocomplete_interaction(
        service_with(source),
        allowed_nodes=frozenset({VIEW_PENDING}),
    )
    assert [choice.value for choice in await suggests("builds_pending")(interaction, "412")] == [412]


async def test_multi_value_completes_only_the_last_entry() -> None:
    interaction = make_autocomplete_interaction(service_with(restriction_source()))
    choices = await suggests("approved_restrictions", multi=True)(interaction, "full_lamp, seam")
    assert [choice.value for choice in choices] == ["full_lamp,seamless", "full_lamp,semi_seamless"]


async def test_multi_value_shows_the_committed_prefix_in_the_label() -> None:
    interaction = make_autocomplete_interaction(service_with(restriction_source()))
    (first, _) = await suggests("approved_restrictions", multi=True)(interaction, "full_lamp, seam")
    assert first.name == "full_lamp,seamless"


async def test_without_multi_the_whole_input_is_the_query() -> None:
    provider = RecordingProvider()
    source = SuggestionSource(id="approved_restrictions", provider=provider, multi_value=",")
    interaction = make_autocomplete_interaction(service_with(source))
    await suggests("approved_restrictions")(interaction, "full_lamp, seam")
    assert provider.requests[0].query == "full_lamp, seam"


async def test_choice_names_are_truncated_to_discords_limit() -> None:
    long_label = "x" * 400
    provider = RecordingProvider((Candidate(Suggestion("v", long_label)),))
    source = SuggestionSource(id="approved_restrictions", provider=provider)
    interaction = make_autocomplete_interaction(service_with(source))
    (choice,) = await suggests("approved_restrictions")(interaction, "")
    assert len(choice.name) == CHOICE_NAME_LIMIT
    assert choice.name.startswith("…")


async def test_a_description_is_folded_into_the_choice_name() -> None:
    provider = RecordingProvider((Candidate(Suggestion("seamless", "Seamless", "also flush lamp")),))
    source = SuggestionSource(id="approved_restrictions", provider=provider)
    interaction = make_autocomplete_interaction(service_with(source))
    (choice,) = await suggests("approved_restrictions")(interaction, "")
    assert choice.name == "Seamless — also flush lamp"


async def test_the_discord_client_locale_is_passed_through() -> None:
    provider = RecordingProvider()
    source = SuggestionSource(id="approved_restrictions", provider=provider)
    interaction = make_autocomplete_interaction(service_with(source), locale="zh-CN")
    await suggests("approved_restrictions")(interaction, "seam")
    assert provider.requests[0].locale == "zh-CN"


async def test_a_context_resolver_scopes_the_request() -> None:
    provider = RecordingProvider()
    source = SuggestionSource(
        id="starboard_names",
        provider=provider,
        context_keys=frozenset({"guild_id"}),
    )
    interaction = make_autocomplete_interaction(service_with(source), guild_id=99)
    from squid.bot.utils.autocomplete import guild_context

    await suggests("starboard_names", context=guild_context)(interaction, "")
    assert provider.requests[0].context == {"guild_id": "99"}


async def test_an_account_is_resolved_only_for_a_viewer_scoped_source() -> None:
    provider = RecordingProvider()
    public = SuggestionSource(id="approved_restrictions", provider=provider)
    interaction = make_autocomplete_interaction(service_with(public))
    await suggests("approved_restrictions")(interaction, "seam")
    assert provider.requests[0].viewer.account_id is None

    scoped_provider = RecordingProvider()
    scoped = SuggestionSource(
        id="my_drafts",
        provider=scoped_provider,
        visibility=Visibility.VIEWER_SCOPED,
    )
    scoped_interaction = make_autocomplete_interaction(service_with(scoped), account_id=77)
    await suggests("my_drafts")(scoped_interaction, "")
    assert scoped_provider.requests[0].viewer.account_id == 77
