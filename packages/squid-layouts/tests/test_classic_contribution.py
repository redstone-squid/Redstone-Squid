"""Contributing a Squid region to a classic message someone else owns."""

import discord
import pytest

from squid_layouts.errors import UnsolvableLayoutError
from squid_layouts.semantic import Heading, Paragraph
from squid_layouts.discord import ExistingLayoutError, classic
from squid_layouts.discord.inspection import measure
from squid_layouts.discord.limits import CLASSIC_LIMITS
from squid_layouts.discord.presentation import DiscordPresentation
from squid_layouts.discord.presentation import DiscordModeError
from squid_layouts.planning.limits import CONTENT_TEXT, CONTROLS, EMBED_TEXT, EMBEDS, ROWS


def host(*, content=None, embeds=(), controls=()) -> DiscordPresentation:
    view = None
    if controls:
        view = discord.ui.View(timeout=None)
        for item in controls:
            view.add_item(item)
    return DiscordPresentation.classic(content=content, embeds=list(embeds), view=view)


def button(label: str, row: int | None = None) -> discord.ui.Button:
    return discord.ui.Button(label=label, custom_id=label, row=row)


def labels(view: discord.ui.LayoutView | discord.ui.View | None) -> list[str | None]:
    assert view is not None
    return [item.label for item in view.children if isinstance(item, discord.ui.Button)]


class TestMeasurement:
    def test_usage_and_reservation_agree_when_nothing_is_all_or_nothing(self) -> None:
        embed = discord.Embed(description="x" * 100)
        reservation = measure(host(embeds=[embed]))

        assert reservation.usage.values == reservation.reserved.values

    def test_any_host_content_reserves_the_whole_slot(self) -> None:
        """A message has one content field, and Squid cannot append to someone else's."""
        reservation = measure(host(content="hi"))

        assert reservation.usage.get(CONTENT_TEXT) == 2
        assert reservation.reserved.get(CONTENT_TEXT) == CLASSIC_LIMITS.content

    def test_absent_content_reserves_nothing(self) -> None:
        assert measure(host()).reserved.get(CONTENT_TEXT) == 0

    def test_embeds_rows_and_controls_are_all_accounted(self) -> None:
        reservation = measure(host(embeds=[discord.Embed(description="abc")], controls=[button("a"), button("b")]))

        assert reservation.reserved.get(EMBEDS) == 1
        assert reservation.reserved.get(EMBED_TEXT) == 3
        assert reservation.reserved.get(ROWS) == 1
        assert reservation.reserved.get(CONTROLS) == 2

    def test_an_already_invalid_host_raises_rather_than_being_repaired(self) -> None:
        broken = host(embeds=[discord.Embed(title="x" * 300)])

        with pytest.raises(ExistingLayoutError):
            measure(broken).raise_if_invalid()

    def test_a_v2_presentation_is_measured_in_v2_axes(self) -> None:
        layout = discord.ui.LayoutView(timeout=None)
        layout.add_item(discord.ui.TextDisplay("hello"))

        assert measure(DiscordPresentation.components_v2(layout)).reserved.get("display_text") == 5


class TestContribution:
    def test_the_squid_region_lands_after_the_hosts_embeds(self) -> None:
        result = classic.contribute(Paragraph("squid"), to=host(embeds=[discord.Embed(description="host")]))

        assert [embed.description for embed in result.presentation.embeds] == ["host", "squid"]

    def test_trailing_embeds_land_after_the_squid_region(self) -> None:
        result = classic.contribute(
            Paragraph("squid"),
            to=host(embeds=[discord.Embed(description="host")]),
            followed_by=[discord.Embed(description="tail")],
        )

        assert [embed.description for embed in result.presentation.embeds] == ["host", "squid", "tail"]

    def test_host_content_is_never_overwritten(self) -> None:
        result = classic.contribute(Paragraph("squid"), to=host(content="@here"))

        assert result.presentation.content == "@here"

    def test_squid_controls_go_into_rows_after_the_hosts(self) -> None:
        from squid_layouts.semantic import Actions, Link

        host_view_message = host(controls=[button("host")])
        result = classic.contribute(
            [Paragraph("body"), Actions((Link("d", "Docs", "https://example.invalid"),), key="k")],
            to=host_view_message,
        )
        view = result.presentation.view

        assert labels(view) == ["host", "Docs"]
        assert view is not None
        assert view.children[-1].row == 1

    def test_the_host_view_is_mutated_rather_than_cloned(self) -> None:
        """A control's callback registration belongs to the view that owns it."""
        from squid_layouts.semantic import Actions, Link

        message = host(controls=[button("host")])
        result = classic.contribute(Actions((Link("d", "Docs", "https://example.invalid"),), key="k"), to=message)

        assert result.presentation.view is message.view

    def test_content_and_embeds_stay_immutable_values(self) -> None:
        embed = discord.Embed(description="host")
        message = host(content="@here", embeds=[embed])

        result = classic.contribute(Paragraph("squid"), to=message)

        assert message.embeds == (embed,)
        assert result.presentation.embeds is not message.embeds

    def test_removal_is_identity_based(self) -> None:
        from squid_layouts.semantic import Actions, Link

        message = host(controls=[button("host")])
        result = classic.contribute(Actions((Link("d", "Docs", "https://example.invalid"),), key="k"), to=message)
        result.remove()

        assert labels(message.view) == ["host"]

    def test_the_report_is_passed_through_rather_than_swallowed(self) -> None:
        result = classic.contribute([Heading("Title"), Paragraph("body")], to=host())

        assert result.report is result.plan.report


class TestPreflight:
    def test_a_duplicate_custom_id_fails_before_anything_moves(self) -> None:
        from squid_layouts.semantic import Actions, RoutedAction

        message = host(controls=[button("shared")])
        assert isinstance(message.view, discord.ui.View)
        before = list(message.view.children)

        with pytest.raises(ExistingLayoutError, match="already used in this message"):
            classic.contribute(
                Actions((RoutedAction("r", "Go", "shared"),), key="k"),
                to=message,
            )

        assert list(message.view.children) == before

    def test_a_host_leaving_no_room_fails_during_planning_not_after(self) -> None:
        """The reservation *is* a smaller target, so there is nothing left to draw with."""
        embeds = [discord.Embed(description="x") for _ in range(10)]
        message = host(embeds=embeds)

        with pytest.raises(UnsolvableLayoutError):
            classic.contribute(Paragraph("squid"), to=message)

        assert len(message.embeds) == 10

    def test_a_components_v2_host_is_refused_by_mode(self) -> None:
        layout = discord.ui.LayoutView(timeout=None)
        layout.add_item(discord.ui.TextDisplay("hi"))

        with pytest.raises(DiscordModeError, match="needs a classic host presentation"):
            classic.contribute(Paragraph("squid"), to=DiscordPresentation.components_v2(layout))

    def test_a_component_local_action_cannot_enter_a_host_view(self) -> None:
        """The host view's callbacks stay under its owner."""
        from squid_layouts.semantic import Action, Actions

        async def press(event) -> None: ...

        with pytest.raises(TypeError, match="mounted Discord frontend"):
            classic.contribute(Actions((Action("a", "Press", press),), key="k"), to=host())


class TestEffectiveRows:
    def test_the_private_row_state_is_cross_checked_against_the_payload(self) -> None:
        from squid_layouts.discord.inspection import effective_rows

        view = discord.ui.View(timeout=None)
        view.add_item(button("a"))
        view.add_item(button("b", row=2))

        assert effective_rows(view) == (0, 2)
        assert len({*effective_rows(view)}) == sum(
            1 for component in view.to_components() if component.get("type") == 1
        )
