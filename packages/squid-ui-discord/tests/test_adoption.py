"""Adopting an unsent discord.py view: refusals, translation, and the interaction proxy.

Adversarial by design. The proxy is the part that rots, so most of this file is about calls a
legacy callback makes that would put a second writer on the mount's message.
"""

from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest

import squid_ui as sl
import squid_ui_discord
from squid_ui.assets import Asset, InlineAsset, StoredAsset
from squid_ui.document import Document
from squid_ui.emoji import Emoji
from squid_ui.entity import ChannelType, EntityKind, EntityRef, EntityType
from squid_ui.primitives import (
    ActionStyle,
    Button,
    EntitySelect,
    Gallery,
    GalleryItem,
    LinkButton,
    Node,
    Panel,
    Row,
    Section,
    SelectMenu,
    Text,
    Thumbnail,
)
from squid_ui_discord import Everyone, MessageRoot
from squid_ui_discord.adoption import AdoptionError, adopt
from squid_ui_discord.message_root import _EntityValues
from squid_ui_discord.testing import commit_render, delivered_to, fake_interaction, fake_message


class Paginator(discord.ui.View):
    """The canonical legacy view: mutate self, flip disabled, edit_message(view=self)."""

    def __init__(self, pages: int = 3) -> None:
        super().__init__(timeout=None)
        self.page = 0
        self.pages = pages
        self._sync()

    def _sync(self) -> None:
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page == self.pages - 1

    @discord.ui.button(label="Previous", custom_id="prev")
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        self.page -= 1
        self._sync()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Next", custom_id="next", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        self.page += 1
        self._sync()
        await interaction.response.edit_message(view=self)


def _mounted(view: discord.ui.View, **options: Any) -> tuple[MessageRoot, list[BaseException]]:
    """A committed mount around `view`, plus the list its error hook appends to."""
    errors: list[BaseException] = []
    message_root = MessageRoot(adopt(view, **options), access=Everyone(), timeout=None, on_error=_record(errors))
    commit_render(message_root)
    return message_root, errors


def _mounted_layout(view: discord.ui.LayoutView, **options: Any) -> tuple[MessageRoot, list[BaseException]]:
    errors: list[BaseException] = []
    message_root = MessageRoot(adopt(view, **options), access=Everyone(), timeout=None, on_error=_record(errors))
    commit_render(message_root)
    return message_root, errors


def _row_buttons(row: Row) -> tuple[Button, ...]:
    buttons = tuple(item for item in row.items if isinstance(item, Button))
    assert len(buttons) == len(row.items)
    return buttons


# --- what "unsent" tests ------------------------------------------------------------------


async def test_a_dispatching_view_refuses_because_discord_routes_its_clicks() -> None:
    view = Paginator()
    # What `_start_listening_from_store` leaves behind when a view is sent. `View.message` is a
    # convention discord.py never sets, so this is the only framework-owned signal.
    view.is_dispatching = lambda: True

    with pytest.raises(AdoptionError, match="already dispatching"):
        adopt(view)


async def test_a_finished_view_refuses() -> None:
    view = Paginator()
    view.stop()

    with pytest.raises(AdoptionError, match="already stopped"):
        adopt(view)


async def test_the_message_convention_still_refuses_as_a_second_signal() -> None:
    view = Paginator()
    # An uninitialised `Message` is enough: the check is an isinstance, so that a view whose own
    # button callback happens to be named `message` is not mistaken for a sent one.
    view.message = discord.Message.__new__(discord.Message)  # pyrefly: ignore[missing-attribute]

    with pytest.raises(AdoptionError, match="already holds a message"):
        adopt(view)


async def test_an_unsent_layout_view_is_adopted_as_exact_v2_content() -> None:
    layout = discord.ui.LayoutView(timeout=None)
    layout.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("content"),
            discord.ui.ActionRow(discord.ui.Button(label="Run", custom_id="run")),
            accent_colour=0x123456,
            spoiler=True,
        )
    )

    document = adopt(layout).render()

    assert isinstance(document, Document)
    assert isinstance(document.children[0], Panel)
    panel = document.children[0]
    assert panel.accent == 0x123456 and panel.spoiler is True
    assert isinstance(panel.children[0], Text)
    assert isinstance(panel.children[1], Row)
    assert isinstance(panel.children[1].items[0], Button)
    assert panel.children[1].items[0].key == "run"


def test_layout_view_preserves_nested_media_and_assets() -> None:
    layout = discord.ui.LayoutView(timeout=None)
    layout.add_item(
        discord.ui.Container(
            discord.ui.Section(
                discord.ui.TextDisplay("description"),
                accessory=discord.ui.Thumbnail("https://example.invalid/thumb.png", spoiler=True),
            ),
            discord.ui.MediaGallery(
                discord.MediaGalleryItem("attachment://gallery.png", description="gallery", spoiler=True),
            ),
        )
    )
    asset = Asset("gallery", "gallery.png", "image/png", InlineAsset(b"bytes"))

    document = adopt(layout, assets=(asset,)).render()

    assert isinstance(document, Document)
    panel = document.children[0]
    assert isinstance(panel, Panel)
    assert isinstance(panel.children[0], Section)
    assert isinstance(panel.children[0].accessory, Thumbnail)
    assert panel.children[0].accessory.spoiler is True
    assert isinstance(panel.children[1], Gallery)
    assert isinstance(panel.children[1].items[0], GalleryItem)
    assert panel.children[1].items[0].url == "attachment://gallery.png"
    assert document.assets == (asset,)


def test_layout_view_rejects_missing_and_ambiguous_assets() -> None:
    layout = discord.ui.LayoutView(timeout=None)
    layout.add_item(discord.ui.File("attachment://download.zip"))
    asset = Asset("download", "download.zip", "application/zip", InlineAsset(b"bytes"))

    with pytest.raises(AdoptionError, match="no supplied Asset"):
        adopt(layout)

    duplicate = Asset("other", "download.zip", "application/zip", InlineAsset(b"other"))
    with pytest.raises(AdoptionError, match="attachment name"):
        adopt(layout, assets=(asset, duplicate))

    assert adopt(layout, assets=(asset,)).render() is not None


def test_layout_view_resolves_http_files_through_stored_asset_metadata() -> None:
    layout = discord.ui.LayoutView(timeout=None)
    layout.add_item(discord.ui.File("https://example.invalid/report.txt"))
    asset = Asset(
        "report",
        "report.txt",
        "text/plain",
        StoredAsset("https://example.invalid/report.txt"),
    )

    document = adopt(layout, assets=(asset,)).render()

    assert isinstance(document, Document)
    file = document.children[0]
    assert file.asset_key == "report"  # pyrefly: ignore[missing-attribute]
    assert file.name == "report.txt"  # pyrefly: ignore[missing-attribute]
    assert file.media_type == "text/plain"  # pyrefly: ignore[missing-attribute]


def test_layout_view_uses_structural_keys_for_nested_controls() -> None:
    layout = discord.ui.LayoutView(timeout=None)
    layout.add_item(
        discord.ui.Container(
            discord.ui.ActionRow(discord.ui.Button(label="Run")),
        )
    )

    document = adopt(layout).render()

    assert isinstance(document, Document)
    panel = document.children[0]
    assert isinstance(panel, Panel)
    assert isinstance(panel.children[0], Row)
    assert isinstance(panel.children[0].items[0], Button)
    assert panel.children[0].items[0].key == "adopted-0.0.0"


def test_layout_view_rejects_duplicate_keys_across_nested_branches() -> None:
    layout = discord.ui.LayoutView(timeout=None)
    layout.add_item(
        discord.ui.Container(
            discord.ui.ActionRow(discord.ui.Button(label="One", custom_id="same")),
            discord.ui.ActionRow(discord.ui.Button(label="Two", custom_id="same")),
        )
    )

    with pytest.raises(AdoptionError, match="share the key"):
        adopt(layout)


async def test_layout_view_dispatches_original_callback_and_reconstructs_the_tree() -> None:
    layout = discord.ui.LayoutView(timeout=None)
    text = discord.ui.TextDisplay("before")
    button = discord.ui.Button(label="Run", custom_id="run")

    async def callback(interaction: discord.Interaction) -> None:
        text.content = "after"
        button.disabled = True
        await interaction.response.edit_message(view=layout)

    button.callback = callback
    layout.add_item(discord.ui.Container(text, discord.ui.ActionRow(button)))
    message_root, errors = _mounted_layout(layout)
    interaction = fake_interaction()

    await message_root.dispatch("run", interaction)

    assert not errors
    assert text.content == "after"
    response = interaction.response.edit_message
    assert response.await_count == 1
    drawn = response.await_args.kwargs["view"]
    assert [item.content for item in drawn.walk_children() if isinstance(item, discord.ui.TextDisplay)] == ["after"]
    assert [item.disabled for item in drawn.walk_children() if isinstance(item, discord.ui.Button)] == [True]


async def test_an_overridden_on_timeout_refuses_unless_discarded() -> None:
    cleaned: list[str] = []

    class Closing(Paginator):
        async def on_timeout(self) -> None:
            cleaned.append("released")

    with pytest.raises(AdoptionError, match="on_timeout"):
        adopt(Closing())

    assert adopt(Closing(), discard_timeout=True) is not None
    assert cleaned == []


# --- translation --------------------------------------------------------------------------


async def test_rows_reproduce_discord_packing() -> None:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="auto"))
    view.add_item(discord.ui.Button(label="pinned", row=2))
    view.add_item(discord.ui.Select(placeholder="pick", options=[discord.SelectOption(label="a")]))
    view.add_item(discord.ui.Button(label="auto too"))

    nodes = cast(list[Node], adopt(view).render())

    # Row 0 takes both auto buttons, in `view.children` order; the select cannot share a row
    # with anything, and the explicitly-pinned button keeps row 2.
    first, second, third = nodes
    assert isinstance(first, Row)
    assert [button.label for button in _row_buttons(first)] == ["auto", "auto too"]
    assert isinstance(second, SelectMenu)
    assert isinstance(third, Row)
    assert [button.label for button in _row_buttons(third)] == ["pinned"]


async def test_a_link_button_becomes_a_link_button() -> None:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="docs", url="https://example.invalid"))

    row = cast(list[Node], adopt(view).render())[0]

    assert isinstance(row, Row)
    assert row.items == (LinkButton(label="docs", url="https://example.invalid"),)


async def test_a_channel_select_round_trips_types_and_defaults() -> None:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            default_values=[discord.Object(id=77)],
            placeholder="where",
            max_values=2,
        )
    )

    node = cast(list[Node], adopt(view).render())[0]

    assert isinstance(node, EntitySelect)
    assert node.entity_type is EntityType.CHANNEL
    assert node.channel_types == (ChannelType.TEXT, ChannelType.ANNOUNCEMENT)
    assert node.default_values == (EntityRef(EntityKind.CHANNEL, 77),)
    assert node.max_values == 2


async def test_a_premium_button_and_a_dynamic_item_refuse() -> None:
    premium = discord.ui.View(timeout=None)
    premium.add_item(discord.ui.Button(sku_id=1))
    with pytest.raises(AdoptionError, match="premium"):
        adopt(premium)

    class Dynamic(discord.ui.DynamicItem[discord.ui.Button[Any]], template=r"dyn:(?P<id>\d+)"):
        def __init__(self) -> None:
            super().__init__(discord.ui.Button(label="dyn", custom_id="dyn:1"))

        @classmethod
        async def from_custom_id(cls, interaction, item, match):
            return cls()

    dynamic = discord.ui.View(timeout=None)
    dynamic.add_item(Dynamic())
    with pytest.raises(AdoptionError, match="Router"):
        adopt(dynamic)


# --- keys ---------------------------------------------------------------------------------


async def test_author_custom_ids_are_keys_and_generated_ones_are_positional() -> None:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="named", custom_id="chosen"))
    view.add_item(discord.ui.Button(label="anonymous"))

    row = cast(list[Node], adopt(view).render())[0]

    assert isinstance(row, Row)
    assert [button.key for button in _row_buttons(row)] == ["chosen", "adopted-1"]


async def test_duplicate_keys_refuse_rather_than_sharing_a_handler() -> None:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="one", custom_id="same"))
    view.add_item(discord.ui.Button(label="two", custom_id="same"))

    with pytest.raises(AdoptionError, match="share the key"):
        adopt(view)


async def test_keys_override_both_defaults() -> None:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="one", custom_id="ignored"))

    row = cast(list[Node], adopt(view, keys=lambda item: f"by-label-{item.label}").render())[0]

    assert isinstance(row, Row)
    assert isinstance(row.items[0], Button)
    assert row.items[0].key == "by-label-one"


# --- the canonical paginator ---------------------------------------------------------------


async def test_the_paginator_re_renders_and_disables_with_no_http_of_its_own() -> None:
    view = Paginator()
    message_root, errors = _mounted(view)
    interaction = fake_interaction()

    await message_root.dispatch("next", interaction)

    assert view.page == 1
    # Exactly one edit reached Discord, and the mount made it. Two would mean the legacy object
    # wrote the message itself, which is the failure adoption exists to prevent.
    assert interaction.response.edit_message.await_count == 1
    drawn = interaction.response.edit_message.await_args.kwargs["view"]
    buttons = [item for item in drawn.walk_children() if isinstance(item, discord.ui.Button)]
    assert [button.label for button in buttons] == ["Previous", "Next"]
    assert [button.disabled for button in buttons] == [False, False]

    await message_root.dispatch("next", fake_interaction())

    assert view.page == 2
    assert view.next.disabled


async def test_a_string_selects_values_reach_the_legacy_callback() -> None:
    seen: list[list[str]] = []

    class Picker(discord.ui.View):
        @discord.ui.select(
            custom_id="pick",
            options=[discord.SelectOption(label="a"), discord.SelectOption(label="b")],
        )
        async def pick(self, interaction: discord.Interaction, select: discord.ui.Select[Any]) -> None:
            seen.append(list(select.values))
            await interaction.response.edit_message(view=self)

    message_root, errors = _mounted(Picker())

    await message_root.dispatch("pick", fake_interaction(), ["b"])

    assert seen == [["b"]]


async def test_an_entity_selects_resolved_objects_reach_the_legacy_callback() -> None:
    seen: list[list[object]] = []

    class Picker(discord.ui.View):
        @discord.ui.select(cls=discord.ui.UserSelect, custom_id="who")
        async def who(self, interaction: discord.Interaction, select: discord.ui.UserSelect[Any]) -> None:
            seen.append(list(select.values))
            await interaction.response.edit_message(view=self)

    message_root, errors = _mounted(Picker())
    member = discord.Object(id=5)

    await message_root.dispatch("who", fake_interaction(), _EntityValues((EntityRef(EntityKind.USER, 5),), (member,)))

    assert seen == [[member]]


# --- the proxy's refusals -------------------------------------------------------------------


async def test_editing_with_a_different_view_refuses() -> None:
    class Swapper(discord.ui.View):
        @discord.ui.button(label="go", custom_id="go")
        async def go(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            await interaction.response.edit_message(view=discord.ui.View(timeout=None))

    message_root, errors = _mounted(Swapper())
    await message_root.dispatch("go", fake_interaction())

    assert isinstance(errors[0], AdoptionError)
    assert "different screen" in str(errors[0])


async def test_editing_with_a_payload_the_message_root_owns_refuses() -> None:
    class Chatty(discord.ui.View):
        @discord.ui.button(label="go", custom_id="go")
        async def go(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            await interaction.response.edit_message(content="hello", view=self)

    message_root, errors = _mounted(Chatty())
    await message_root.dispatch("go", fake_interaction())

    assert isinstance(errors[0], AdoptionError)
    assert "content" in str(errors[0])


async def test_the_second_writer_calls_all_refuse() -> None:
    class SecondWriter(discord.ui.View):
        @discord.ui.button(label="original", custom_id="original")
        async def original(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            await interaction.edit_original_response(view=self)

        @discord.ui.button(label="message", custom_id="message")
        async def via_message(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            assert interaction.message is not None
            await interaction.message.edit(view=self)

        @discord.ui.button(label="delete", custom_id="delete")
        async def delete(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            await interaction.delete_original_response()

    for key, fragment in (("original", "second writer"), ("message", "second writer"), ("delete", "finish")):
        message_root, errors = _mounted(SecondWriter())
        errors: list[BaseException] = []
        message_root.on_error = _record(errors)

        await message_root.dispatch(key, fake_interaction())

        assert isinstance(errors[0], AdoptionError), key
        assert fragment in str(errors[0]), key


async def test_an_unsupported_response_call_refuses_by_name() -> None:
    class Odd(discord.ui.View):
        @discord.ui.button(label="go", custom_id="go")
        async def go(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            await interaction.response.pong()

    message_root, errors = _mounted(Odd())
    await message_root.dispatch("go", fake_interaction())

    assert isinstance(errors[0], AdoptionError)
    assert "pong" in str(errors[0])


# --- the proxy's translations ---------------------------------------------------------------


async def test_defer_and_ephemeral_send_message_go_through_the_responder() -> None:
    class Answering(discord.ui.View):
        @discord.ui.button(label="defer", custom_id="defer")
        async def defer(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            await interaction.response.defer()

        @discord.ui.button(label="notice", custom_id="notice")
        async def notice(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            await interaction.response.send_message("only you", ephemeral=True)

    message_root, errors = _mounted(Answering())
    deferred = fake_interaction()
    await message_root.dispatch("defer", deferred)
    assert deferred.response.defer.await_count == 1

    message_root, errors = _mounted(Answering())
    noticed = fake_interaction()
    await message_root.dispatch("notice", noticed)
    assert noticed.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_is_done_reports_the_swallowed_edit() -> None:
    seen: list[bool] = []

    class Checking(discord.ui.View):
        @discord.ui.button(label="go", custom_id="go")
        async def go(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            seen.append(interaction.response.is_done())
            await interaction.response.edit_message(view=self)
            seen.append(interaction.response.is_done())

    message_root, errors = _mounted(Checking())

    await message_root.dispatch("go", fake_interaction())

    assert seen == [False, True]


async def test_followup_passes_through_after_acknowledging_the_real_interaction() -> None:
    class Talkative(discord.ui.View):
        @discord.ui.button(label="go", custom_id="go")
        async def go(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("aside", ephemeral=True)

    message_root, errors = _mounted(Talkative())
    interaction = fake_interaction()

    await message_root.dispatch("go", interaction)

    # The swallowed edit left the real interaction unanswered, so the followup would have 404'd
    # without the proxy deferring first.
    assert interaction.response.defer.await_count == 1
    assert interaction.followup.send.await_count == 1


# --- view-level API ---------------------------------------------------------------------------


async def test_stop_inside_a_callback_finishes_the_root() -> None:
    class Closing(discord.ui.View):
        @discord.ui.button(label="done", custom_id="done")
        async def done(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            self.stop()

    message_root = MessageRoot(adopt(Closing()), access=Everyone(), timeout=None)
    await message_root.send(delivered_to(fake_message()))

    await message_root.dispatch("done", fake_interaction())

    assert message_root.finished


async def test_an_overridden_interaction_check_can_refuse_the_press() -> None:
    ran: list[str] = []

    class Guarded(discord.ui.View):
        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            return False

        @discord.ui.button(label="go", custom_id="go")
        async def go(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            ran.append("go")

    message_root, errors = _mounted(Guarded())

    await message_root.dispatch("go", fake_interaction())

    assert ran == []


async def test_a_refusing_interaction_check_still_reports_mutation_and_finishes() -> None:
    class Guarded(discord.ui.View):
        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            self.go.disabled = True
            self.stop()
            return False

        @discord.ui.button(label="go", custom_id="go")
        async def go(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            raise AssertionError("the item callback must not run")

    view = Guarded()
    message_root, errors = _mounted(view)

    await message_root.dispatch("go", fake_interaction())

    button = view.children[0]
    assert isinstance(button, discord.ui.Button)
    assert button.disabled
    assert message_root.finished
    assert errors == []


async def test_an_interaction_check_error_uses_the_legacy_error_hook() -> None:
    caught: list[BaseException] = []

    class Failing(discord.ui.View):
        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            raise RuntimeError("check failed")

        async def on_error(self, interaction, error, item) -> None:
            caught.append(error)

        @discord.ui.button(label="go", custom_id="go")
        async def go(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            raise AssertionError("the item callback must not run")

    message_root, errors = _mounted(Failing())

    await message_root.dispatch("go", fake_interaction())

    assert [type(error) for error in caught] == [RuntimeError]
    assert errors == []


async def test_without_a_legacy_error_hook_an_interaction_check_error_reaches_the_root() -> None:
    class Failing(discord.ui.View):
        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            raise RuntimeError("check failed")

        @discord.ui.button(label="go", custom_id="go")
        async def go(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            raise AssertionError("the item callback must not run")

    message_root, errors = _mounted(Failing())

    await message_root.dispatch("go", fake_interaction())

    assert [type(error) for error in errors] == [RuntimeError]


async def test_an_overridden_on_error_intercepts_before_the_message_root_sees_it() -> None:
    caught: list[BaseException] = []

    class Failing(discord.ui.View):
        async def on_error(self, interaction, error, item) -> None:
            caught.append(error)

        @discord.ui.button(label="go", custom_id="go")
        async def go(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            raise RuntimeError("legacy")

    message_root, errors = _mounted(Failing())
    await message_root.dispatch("go", fake_interaction())

    assert [type(error) for error in caught] == [RuntimeError]
    assert errors == []


async def test_without_an_override_the_error_reaches_the_mounts_hook() -> None:
    class Failing(discord.ui.View):
        @discord.ui.button(label="go", custom_id="go")
        async def go(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            self.broke = True
            raise RuntimeError("legacy")

    view = Failing()
    message_root, errors = _mounted(view)
    await message_root.dispatch("go", fake_interaction())

    assert [type(error) for error in errors] == [RuntimeError]
    # `mutated` cannot roll an in-place write back, and the docstring says so rather than
    # pretending the legacy object took part in the transaction.
    assert view.broke is True


# --- the modal round-trip ----------------------------------------------------------------------


async def test_a_modal_submit_refreshes_the_message_root_and_issues_no_edit_of_its_own() -> None:
    class Renaming(discord.ui.Modal, title="Rename"):
        field: discord.ui.TextInput[Any] = discord.ui.TextInput(label="name")

        def __init__(self, view: Named) -> None:
            super().__init__()
            self.owner = view

        async def on_submit(self, interaction: discord.Interaction) -> None:
            self.owner.name = "renamed"
            await interaction.response.edit_message(view=self.owner)

    class Named(discord.ui.View):
        def __init__(self) -> None:
            super().__init__(timeout=None)
            self.name = "original"

        @discord.ui.button(label="rename", custom_id="rename")
        async def rename(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            await interaction.response.send_modal(Renaming(self))

    view = Named()
    message_root = MessageRoot(adopt(view), access=Everyone(), timeout=None)
    await message_root.send(delivered_to(fake_message()))
    press = fake_interaction()

    await message_root.dispatch("rename", press)

    modal = press.response.send_modal.await_args.args[0]
    submit = fake_interaction()
    await modal.on_submit(submit)

    assert view.name == "renamed"
    # The modal's own `edit_message(view=self.owner)` performed no HTTP; the submit was only
    # acknowledged, and the mount redrew through its own handle.
    assert submit.response.edit_message.await_count == 0
    assert submit.response.defer.await_count == 1


async def test_a_modal_after_the_response_is_spent_refuses_with_an_adoption_error() -> None:
    class Late(discord.ui.View):
        @discord.ui.button(label="go", custom_id="go")
        async def go(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
            await interaction.response.defer()
            await interaction.response.send_modal(discord.ui.Modal(title="too late"))

    message_root, errors = _mounted(Late())
    await message_root.dispatch("go", fake_interaction())

    assert isinstance(errors[0], AdoptionError)
    assert "first response" in str(errors[0])


# --- the drawn result --------------------------------------------------------------------------


async def test_the_adopted_scene_conforms_strictly() -> None:
    message_root, errors = _mounted(Paginator())
    interaction = fake_interaction()

    await message_root.dispatch("next", interaction)

    drawn = interaction.response.edit_message.await_args.kwargs["view"]
    assert squid_ui_discord.conform(drawn, strict=True) == []


async def test_an_adopted_view_embeds_in_a_larger_squid_screen() -> None:
    class Screen(sl.Component):
        def __init__(self, child: sl.Component) -> None:
            self.child = child

        def render(self):
            return [sl.semantic.Paragraph("Legacy controls below"), self.boundary(self.child, key="legacy")]

    message_root = MessageRoot(Screen(adopt(Paginator())), access=Everyone(), timeout=None)
    commit_render(message_root)
    interaction = fake_interaction()

    await message_root.dispatch("legacy.next", interaction)

    drawn = interaction.response.edit_message.await_args.kwargs["view"]
    labels = [item.label for item in drawn.walk_children() if isinstance(item, discord.ui.Button)]
    assert labels == ["Previous", "Next"]


def _record(errors: list[BaseException]) -> Any:
    async def hook(interaction: Any, error: BaseException, context: str) -> None:
        errors.append(error)

    return AsyncMock(side_effect=hook)


def test_adoption_error_is_a_layout_error() -> None:
    assert issubclass(AdoptionError, sl.errors.LayoutError)
    assert AdoptionError in (squid_ui_discord.AdoptionError,)


def test_button_translation_keeps_style_and_emoji() -> None:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="danger", style=discord.ButtonStyle.danger, emoji="\N{FIRE}"))

    row = cast(list[Node], adopt(view).render())[0]

    assert isinstance(row, Row)
    button = row.items[0]
    assert isinstance(button, Button)
    assert button.style is ActionStyle.DANGER
    assert button.emoji == Emoji("\N{FIRE}")
