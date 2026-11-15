"""Classic content and exact embeds retain slots, resources, and authored policies."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
import squid_ui_html

import squid_ui as sl
from squid_ui import scene
from squid_ui.document import Document
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.joint_search import JointInfeasibleError, JointUnsupportedError
from squid_ui.planning.navigation import default_nav
from squid_ui.planning.resources import Axis
from squid_ui.planning.semantics import check_alternative
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState
from squid_ui.presentations import Alternative
from squid_ui.primitives import nodes as native
from squid_ui.primitives.constraints import Paginate, Truncate
from squid_ui.target_types import DiscordAdapter, Renderable
from squid_ui_discord.limits import ClassicLimits, EmbedLimits
from squid_ui_discord.planning.discord import classic_target, components_v2_target

ADAPTER = AdapterProfile(DiscordAdapter, "native-classic", "1")
TARGET = classic_target(ADAPTER)


def test_card_metadata_and_attachment_cost_survive_shared_composition() -> None:
    asset = sl.file(b"image", filename="preview.png", media_type="image/png")
    instant = datetime(2026, 1, 1, tzinfo=UTC)
    card = native.Card(
        (native.Text("First"), native.Text("Second")),
        title="Title",
        url="https://example.com",
        fields=(native.CardField("Name", "Value", inline=True),),
        footer=native.CardFooter("Footer", "https://example.com/footer.png"),
        author=native.CardAuthor("Author", "https://example.com/author", "https://example.com/icon.png"),
        image=native.CardMedia("", "Image", asset.name),
        thumbnail=native.CardMedia("", "Thumb", asset.name),
        timestamp=instant,
        accent=0x123456,
    )
    result = plan(Document((card,), assets=(asset,)), target=TARGET, strict=True)
    assert result.allocation is not None
    (embed,) = result.scene.body.embeds
    assert embed == scene.Embed(
        title="Title",
        url=card.url,
        description="First\n\nSecond",
        fields=(scene.EmbedField("Name", "Value", inline=True),),
        footer=scene.EmbedFooter("Footer", "https://example.com/footer.png"),
        author=scene.EmbedAuthor("Author", "https://example.com/author", "https://example.com/icon.png"),
        colour=0x123456,
        image=scene.EmbedMedia(f"attachment://{asset.name}", "Image", asset.name),
        thumbnail=scene.EmbedMedia(f"attachment://{asset.name}", "Thumb", asset.name),
        timestamp=instant.isoformat(),
    )
    assert result.cost.get(Axis.ATTACHMENTS) == 1
    assert result.cost.get(Axis.EMBED_TEXT) == sum(
        map(len, ("Title", "First\n\nSecond", "Name", "Value", "Footer", "Author"))
    )


def test_embed_aggregate_counts_titles_authors_and_footers() -> None:
    card = native.Card(title="Title", author=native.CardAuthor("Author"), footer=native.CardFooter("Footer"))
    with pytest.raises(JointInfeasibleError):
        plan(card, target=classic_target(ADAPTER, limits=ClassicLimits(embed_text=16)))
    assert plan(card, target=TARGET).cost.get(Axis.EMBED_TEXT) == 17


def test_each_card_slot_obeys_its_own_shortening_policy_and_limit() -> None:
    card = native.Card(
        title=native.Text("abcdefghij", overflow=Truncate(keep="tail")),
        fields=(native.CardField("Name", native.Text("abcdefghij", overflow=Truncate())),),
    )
    target = classic_target(ADAPTER, limits=ClassicLimits(embeds=EmbedLimits(title=5, field_value=6)))
    result = plan(card, target=target)
    assert result.scene.body.embeds[0].title == "…ghij"
    assert result.scene.body.embeds[0].fields[0].value == "abcde…"
    assert len(result.report.events) == 2
    with pytest.raises(JointInfeasibleError):
        plan(card, target=target, strict=True)


def test_cards_are_indivisible_and_checked_alternatives_preserve_their_metadata() -> None:
    card = native.Card((native.Text("First"), native.Text("Second")), accent=0x123456)
    identity = OccurrenceId(("root", "r"))
    with pytest.raises(LayoutInvariantError):
        plan(
            sl.region(card, key="r", title="R"),
            target=TARGET,
            composition=CompositionOptions(automatic=True, partitions={identity: (1, 2)}),
        )
    choice = sl.alternative(replace(card, accent=None), key="plain", fidelity=sl.equivalent())
    with pytest.raises(LayoutInvariantError, match="preserve the Card boundary"):
        check_alternative(sl.alternatives(choice, content=card, key="choice"), choice)


def test_card_slots_cannot_contain_local_pagers() -> None:
    card = native.Card(title=native.Text("First\nSecond", overflow=Paginate(key="title", per=1)))
    with pytest.raises(JointUnsupportedError) as error:
        plan(sl.region(card, key="r", title="R"), target=TARGET)
    assert "embed text cannot contain pager controls" in str(error.value.diagnostics)


async def turn(event: sl.PageEvent) -> None:
    pytest.fail("planning invoked a page handler")


def test_native_content_pages_reuse_the_current_controlled_owner() -> None:
    session = PresentationState()

    def node(index: int) -> native.Content:
        return native.Content("First\nSecond", overflow=Paginate(key="body", per=1, page=sl.controlled(index, turn)))

    first = plan(node(0), target=TARGET, session=session)
    session.apply_updates(first.session_updates)
    second = plan(node(1), target=TARGET, session=session)
    assert second.allocation is first.allocation
    assert (second.scene.body.content or "").startswith("Second\n")
    assert "First" not in (second.scene.body.content or "")


def test_native_content_repeats_on_keyed_document_pages() -> None:
    document = Document(
        (native.Content("Announcement"), native.Card(title="First"), native.Card(title="Second")), key="message"
    )
    result = plan(document, target=classic_target(ADAPTER, limits=ClassicLimits(embed_count=1)), nav=default_nav)
    assert result.allocation is not None
    identity = next(iter(result.allocation.regions))
    assert len(result.allocation.regions[identity].cuts) == 2
    for index, title in enumerate(("First", "Second")):
        page = result.for_page({identity: index})
        assert (page.scene.body.content or "").startswith("Announcement\n")
        assert page.scene.body.embeds[0].title == title


def test_classic_native_structures_report_the_v2_capability_boundary() -> None:
    target: Target[Any, Any, Any, Any] = components_v2_target(ADAPTER)
    with pytest.raises(JointUnsupportedError) as error:
        plan(sl.region(native.Card(title="Title"), key="r", title="R"), target=target)
    assert "native classic embed on another target" in str(error.value.diagnostics)


@pytest.mark.parametrize(
    "target", [TARGET, components_v2_target(ADAPTER), squid_ui_html.target()], ids=lambda target: target.id
)
def test_checked_alternatives_select_a_supported_native_layout(target: Target[Any, Any, Any, Any]) -> None:
    choices: tuple[Alternative[Any], ...] = (
        sl.alternative(
            native.Panel((native.Text("Body"),)),
            key="v2",
            fidelity=sl.equivalent(),
            correspondence=(sl.corresponds(sl.ref("@0", "@0"), "body"),),
        ),
        sl.alternative(
            native.Card((native.Text("Body"),)),
            key="classic",
            fidelity=sl.equivalent(),
            correspondence=(sl.corresponds(sl.ref("@0", "@description", "@0"), "body"),),
        ),
        sl.alternative(sl.paragraph("Body", key="body"), key="portable", fidelity=sl.equivalent(), preference=1),
    )
    document = sl.alternatives(*choices, content=sl.paragraph("Body", key="body"), key="choice")
    result = plan(document, target=target, strict=True)
    assert result.allocation is not None
    assert "Body" in scene.Codec.dumps(result.scene)
    if isinstance(result.scene.body, scene.ClassicMessage):
        assert result.scene.body.embeds[0].description == "Body"
    elif isinstance(result.scene.body, scene.ComponentsV2):
        assert isinstance(result.scene.body.children[0], scene.Panel)


def test_canonical_content_does_not_need_a_realization_on_the_selected_target() -> None:
    source: Renderable[Any] = native.Text("Body")
    choice: Alternative[Any] = sl.alternative(
        sl.paragraph("Body", key="body"),
        key="html",
        fidelity=sl.equivalent(),
        correspondence=(sl.corresponds("body", "@0"),),
    )
    document = sl.alternatives(choice, content=source, key="choice")
    target: Target[Any, Any, Any, Any] = squid_ui_html.target()
    result = plan(document, target=target, strict=True)
    assert result.allocation is not None
    assert "Body" in squid_ui_html.Renderer().draw(result.scene, plan=result)
