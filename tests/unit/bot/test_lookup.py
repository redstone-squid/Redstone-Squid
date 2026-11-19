"""Domain pickers preserve identities, paging, and provider failure semantics."""

from types import SimpleNamespace
from typing import Any, override
from unittest.mock import AsyncMock, Mock

import anyio
import pytest

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.lookup import LookupScreen, SuggestionSource, pick
from squid.core.suggestions.application import Candidate, SuggestionRegistry, SuggestionService
from squid.core.suggestions.application import SuggestionSource as RegisteredSource
from squid.core.suggestions.domain import SourceKind, Suggestion, SuggestionRequest
from squid_ui.ext.discord.target_types import ComponentsV2Target
from squid_ui.ext.discord.testing import StagedUI, stage
from tests.unit.suggestions.test_services import AllowingAuthorizer, RecordingProvider


async def test_enumerable_picker_reaches_items_after_discords_first_25() -> None:
    provider = RecordingProvider(tuple(Candidate.create(str(i), f"Tag {i}") for i in range(60)))
    service = SuggestionService(
        SuggestionRegistry.of((RegisteredSource("tags", provider, kind=SourceKind.ENUMERABLE),))
    )
    source = SuggestionSource(service, SuggestionRequest("tags"), AllowingAuthorizer(allowed=True))
    first = await source.fetch(sl.sources.ORIGIN, 25)
    assert first.next is not None
    second = await source.fetch(first.next, 25)
    assert [item.value for item in second.items] == [str(i) for i in range(25, 50)]
    assert len(provider.requests) == 1
    assert provider.requests[0].limit == 0


async def test_queried_picker_preserves_matches_found_through_secondary_terms() -> None:
    provider = RecordingProvider((Candidate.create("stable-uuid", "Record", terms=("subtitle",)),))
    service = SuggestionService(SuggestionRegistry.of((RegisteredSource("records", provider),)))
    source = SuggestionSource(service, SuggestionRequest("records", query="subtitle"), AllowingAuthorizer(allowed=True))
    window = await source.fetch(sl.sources.ORIGIN, 25)
    assert [item.value for item in window.items] == ["stable-uuid"]
    assert window.next is None


async def test_failed_fetch_is_retried_instead_of_cached_as_empty() -> None:
    class FlakyProvider:
        calls = 0

        async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporarily unavailable")
            return (Candidate.create("7", "Recovered"),)

    service = SuggestionService(SuggestionRegistry.of((RegisteredSource("tags", FlakyProvider()),)))
    source = SuggestionSource(service, SuggestionRequest("tags"), AllowingAuthorizer(allowed=True))
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        await source.fetch(sl.sources.ORIGIN, 25)
    assert (await source.fetch(sl.sources.ORIGIN, 25)).items[0].label == "Recovered"


@pytest.mark.parametrize(
    ("kind", "confirm"),
    [(SourceKind.QUERIED, True), (SourceKind.QUERIED, False), (SourceKind.ENUMERABLE, True)],
)
async def test_lookup_returns_the_selected_identity_only_after_confirmation(kind: SourceKind, confirm: bool) -> None:
    provider = RecordingProvider((Candidate.create("first", "Same name"), Candidate.create("second", "Same name")))
    service = SuggestionService(SuggestionRegistry.of((RegisteredSource("creators", provider, kind=kind),)))

    class Parent(sd.Screen):
        policy = sd.ResponseSpec(session=sd.SessionSpec("lookup-test"))
        value: str = sl.state("")
        presented: sd.Presented[Any] | None = sl.state(None, opaque=True)

        @override
        def render(self) -> sl.LayoutNode[ComponentsV2Target, sd.Request]:
            return sl.action_controls(sl.action_control("Open", self.open, key="open"), key="actions")

        async def open(self, event: sd.PressEvent) -> None:
            child = LookupScreen(
                "Creator", service, SuggestionRequest("creators"), AllowingAuthorizer(allowed=True), confirm="Follow"
            )
            result = await self.open_child(event, child, on_result=self.accept)
            assert isinstance(result, sd.Presented), result
            self.presented = result

        async def accept(self, event: sd.ScreenResultEvent[Suggestion]) -> None:
            self.value = event.value.value

    parent = Parent()
    async with stage(parent, owner=object()) as ui:
        interaction = await ui.press(ui.key_for("open"))
        assert parent.presented is not None, interaction.sends
        child = StagedUI(parent.presented, client=ui._client, user_id=1, interaction=ui.interaction)
        with anyio.fail_after(2):
            while not any(key.endswith("lookup.results") for key in child.root.snapshot().handler_keys):
                await anyio.sleep(0.01)
        has_search = any(key.endswith("lookup.search") for key in child.root.snapshot().handler_keys)
        assert has_search == (kind == SourceKind.QUERIED)
        await child.select(child.key_for("lookup.results"), ["second"])
        assert parent.value == ""
        await child.press(child.key_for("confirm" if confirm else "cancel"))
        assert parent.value == ("second" if confirm else "")
        assert not ui.root.finished
        assert child.root.finished


async def test_pick_opens_child_with_the_event_request() -> None:
    provider = RecordingProvider((Candidate.create("creator-id", "Creator"),))
    service = SuggestionService(SuggestionRegistry.of((RegisteredSource("creators", provider),)))
    parent = Mock(spec=sd.Screen)
    parent.open_child = AsyncMock()
    resolve = AsyncMock(return_value=None)
    request = SimpleNamespace(
        root=SimpleNamespace(component=parent),
        client=SimpleNamespace(actors=SimpleNamespace(resolve=resolve), services=SimpleNamespace(suggestions=service)),
        guild=SimpleNamespace(id=123),
    )
    event = Mock(context=request, locale="en-US")
    on_pick = AsyncMock()

    await pick(event, "creators", "Creator", on_pick, confirm="Follow")

    resolve.assert_awaited_once_with(request)
    parent.open_child.assert_awaited_once()
    child = parent.open_child.call_args.args[1]
    source = child.picker.search("")
    assert source.request.context == {"guild_id": "123"}
    assert source.authorizer.request is request
    assert child.confirm == "Follow"
    selected = parent.open_child.call_args.kwargs["on_result"]
    result = Mock(value=Suggestion(value="creator-id", label="Creator", kind="creator"))
    await selected(result)
    on_pick.assert_awaited_once_with(result, result.value)
