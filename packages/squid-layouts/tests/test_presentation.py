"""The whole outgoing Discord message as one value, and the transitions it may make.

Plan 38: content and embeds used to be a private guess at a `discord.Message` that was very
often `None`. They are part of the payload now, so the tests that matter are the ones about
coherence at construction and about which mode transitions reach Discord at all.
"""

import io
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

import squid_layouts as sl
from squid_layouts.assets import Asset, InlineAsset
from squid_layouts.discord import delivery
from squid_layouts.discord.durability import MountLocator
from squid_layouts.discord.durability.frontend import DiscordFrontend, Promoted, Reconnected, RecoveredBinding
from squid_layouts.discord.presentation import DiscordMode, DiscordModeError, DiscordPresentation, mode_of
from squid_layouts.discord.testing import delivered_to, fake_interaction, fake_message
from squid_layouts.errors import LimitViolationError
from squid_layouts.planning.limits import LIMITS
from squid_layouts.primitives import Text

CLASSIC = DiscordMode.CLASSIC
V2 = DiscordMode.COMPONENTS_V2


class Panel(sl.Component):
    count: int = sl.state(0)

    def render(self):
        return Text(f"count {self.count}")


def a_layout() -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.TextDisplay("hello"))
    return view


def a_classic_view() -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="ok", custom_id="ok"))
    return view


class _FlaggedView(discord.ui.View):
    """A classic view that reports the V2 flag anyway.

    discord.py 2.7 refuses to put an `ActionRow` in a `discord.ui.View`, so the only way to
    reach this state today is a subclass. The check still earns its place: the flag is set
    from `has_components_v2()`, which is a view's own answer rather than a fact discord.py
    derives, and the resulting 400 names nothing.
    """

    def has_components_v2(self) -> bool:
        return True


def an_asset(key: str = "report") -> Asset:
    return Asset(key, f"{key}.txt", "text/plain", InlineAsset(b"full report"))


def v2(view: discord.ui.LayoutView | None = None, *, assets: tuple[Asset, ...] = ()) -> DiscordPresentation:
    return DiscordPresentation.components_v2(a_layout() if view is None else view, assets=assets)


class TestCoherence:
    """The invalid combinations are exactly the ones Discord answers with an unhelpful 400."""

    def test_components_v2_refuses_content_and_embeds(self) -> None:
        with pytest.raises(DiscordModeError, match="cannot carry content"):
            DiscordPresentation(V2, content="hi", view=a_layout())

        with pytest.raises(DiscordModeError, match="cannot carry embeds"):
            DiscordPresentation(V2, embeds=(discord.Embed(title="hi"),), view=a_layout())

    def test_components_v2_needs_a_layout_view(self) -> None:
        with pytest.raises(DiscordModeError, match="needs a LayoutView"):
            DiscordPresentation(V2, view=a_classic_view())

        with pytest.raises(DiscordModeError, match="needs a LayoutView"):
            DiscordPresentation(V2)

    def test_classic_refuses_a_layout_view(self) -> None:
        with pytest.raises(DiscordModeError, match="cannot carry a LayoutView"):
            DiscordPresentation(CLASSIC, view=a_layout())

    def test_classic_refuses_a_view_that_reports_components_v2(self) -> None:
        with pytest.raises(DiscordModeError, match="sets the flag implicitly"):
            DiscordPresentation(CLASSIC, content="hi", view=_FlaggedView(timeout=None))

    def test_every_disagreement_is_reported_at_once(self) -> None:
        with pytest.raises(DiscordModeError) as raised:
            DiscordPresentation(V2, content="hi", embeds=(discord.Embed(title="hi"),))

        assert str(raised.value).count(";") == 2

    def test_a_coherent_presentation_of_each_mode_is_accepted(self) -> None:
        assert v2().mode is V2
        classic = DiscordPresentation.classic(content="hi", embeds=(discord.Embed(title="hi"),))
        assert classic.mode is CLASSIC
        assert DiscordPresentation.classic(content="hi", view=a_classic_view()).view is not None

    def test_sequences_are_frozen_into_tuples(self) -> None:
        presentation = DiscordPresentation.classic(content="hi", embeds=[discord.Embed(title="hi")])
        assert isinstance(presentation.embeds, tuple)
        assert isinstance(v2(assets=(an_asset(),)).assets, tuple)


class TestPayload:
    def test_files_are_repeatable_and_fresh_every_call(self) -> None:
        presentation = v2(assets=(an_asset(),))

        first, second = presentation.files(), presentation.files()

        assert [file.filename for file in first] == ["report.txt"]
        assert first[0] is not second[0]
        assert first[0].fp.read() == second[0].fp.read() == b"full report"

    def test_layout_is_the_view_for_a_v2_presentation_and_refuses_a_classic_one(self) -> None:
        view = a_layout()
        assert v2(view).layout is view

        with pytest.raises(DiscordModeError, match="no LayoutView"):
            _ = DiscordPresentation.classic(content="hi").layout

    def test_mode_of_reads_the_flag_discord_set(self) -> None:
        assert mode_of(fake_message()) is V2
        assert mode_of(fake_message(components_v2=False)) is CLASSIC


class TestTransitions:
    """`_legacy_fields` guessed; the matrix states every case, including the illegal one."""

    async def test_classic_to_v2_clears_the_legacy_fields(self) -> None:
        message = fake_message(components_v2=False)
        handle = delivery.handle_for(message)
        assert handle.mode is CLASSIC

        await handle.write(v2())

        fields = message.edit.await_args.kwargs
        assert fields["content"] is None
        assert fields["embeds"] == []
        assert isinstance(fields["view"], discord.ui.LayoutView)
        assert handle.mode is V2

    async def test_v2_to_v2_never_names_the_legacy_fields(self) -> None:
        message = fake_message()
        handle = delivery.handle_for(message)

        await handle.write(v2())

        fields = message.edit.await_args.kwargs
        assert "content" not in fields
        assert "embeds" not in fields

    async def test_classic_to_classic_replaces_every_field_it_owns(self) -> None:
        message = fake_message(components_v2=False)
        handle = delivery.handle_for(message)
        embed = discord.Embed(title="hi")

        await handle.write(DiscordPresentation.classic(content="body", embeds=(embed,)))

        fields = message.edit.await_args.kwargs
        assert fields["content"] == "body"
        assert fields["embeds"] == [embed]
        assert fields["view"] is None
        assert handle.mode is CLASSIC

    async def test_v2_to_classic_is_refused_before_any_request(self) -> None:
        message = fake_message()
        handle = delivery.handle_for(message)

        with pytest.raises(DiscordModeError, match="back off a sent message"):
            await handle.write(DiscordPresentation.classic(content="body"))

        message.edit.assert_not_awaited()

    async def test_the_original_response_handle_runs_the_same_matrix(self) -> None:
        interaction = fake_interaction(components_v2=False)
        destination = delivery.respond_to(interaction, wait=True)
        receipt = await destination(v2())
        handle = receipt.handle
        assert handle is not None

        await handle.write(v2())

        fields = interaction.edit_original_response.await_args.kwargs
        assert "content" not in fields
        with pytest.raises(DiscordModeError):
            await handle.write(DiscordPresentation.classic(content="body"))

    async def test_the_webhook_handle_runs_the_same_matrix(self) -> None:
        interaction = fake_interaction(components_v2=False)
        handle = delivery.handle_from(interaction)
        assert handle is not None and handle.mode is CLASSIC

        await handle.write(v2())

        fields = interaction.response.edit_message.await_args.kwargs
        assert fields["content"] is None
        assert fields["embeds"] == []
        assert handle.mode is V2

        with pytest.raises(DiscordModeError):
            await handle.write(DiscordPresentation.classic(content="body"))

    async def test_a_handle_with_no_readable_message_still_refuses_the_illegal_edit(self) -> None:
        # `wait=False` on a fresh response: nothing ever fetched the message, so the mode the
        # destination delivered is the only thing that knows what is on it.
        interaction = fake_interaction()
        receipt = await delivery.respond_to(interaction)(v2())
        handle = receipt.handle
        assert receipt.message is None
        assert handle is not None and handle.mode is V2

        with pytest.raises(DiscordModeError):
            await handle.write(DiscordPresentation.classic(content="body"))

    async def test_keep_attachments_leaves_the_message_files_alone(self) -> None:
        message = fake_message()
        handle = delivery.handle_for(message)

        await handle.write(v2(assets=(an_asset(),)), keep_attachments=True)
        assert "attachments" not in message.edit.await_args.kwargs

        await handle.write(v2(assets=(an_asset(),)))
        assert [file.filename for file in message.edit.await_args.kwargs["attachments"]] == ["report.txt"]

    async def test_a_refused_write_leaves_the_recorded_mode_alone(self) -> None:
        message = fake_message(components_v2=False)
        handle = delivery.handle_for(message)
        message.edit.side_effect = _http_error()

        with pytest.raises(discord.HTTPException):
            await handle.write(v2())

        assert handle.mode is CLASSIC


def _http_error() -> discord.HTTPException:
    response = SimpleNamespace(status=500, reason="server error")
    return discord.HTTPException(response, {"code": 0, "message": "nope"})  # type: ignore[arg-type]


class _Replyable:
    """A `Context`-shaped double that records exactly what was asked of Discord."""

    def __init__(self) -> None:
        self.interaction = None
        self.sent: dict[str, Any] = {}

    async def send(self, **fields: Any) -> Any:
        self.sent = fields
        return fake_message()


class TestDestinations:
    async def test_a_v2_send_names_only_the_view(self) -> None:
        ctx = _Replyable()

        await delivery.reply_to(ctx)(v2())

        assert "content" not in ctx.sent
        assert "embeds" not in ctx.sent
        assert isinstance(ctx.sent["view"], discord.ui.LayoutView)

    async def test_a_classic_send_names_content_embeds_and_view(self) -> None:
        ctx = _Replyable()
        embed = discord.Embed(title="hi")

        await delivery.reply_to(ctx)(DiscordPresentation.classic(content="body", embeds=(embed,)))

        assert ctx.sent["content"] == "body"
        assert ctx.sent["embeds"] == [embed]
        assert ctx.sent["view"] is None

    async def test_host_files_come_before_the_presentations_own(self) -> None:
        ctx = _Replyable()
        host = discord.File(io.BytesIO(b"host"), filename="host.txt")

        await delivery.reply_to(ctx, files=[host])(v2(assets=(an_asset(),)))

        assert [file.filename for file in ctx.sent["files"]] == ["host.txt", "report.txt"]

    async def test_attachment_overflow_is_refused_before_discord_sees_it(self) -> None:
        ctx = _Replyable()
        assets = tuple(an_asset(f"report{index}") for index in range(LIMITS.attachments))

        with pytest.raises(LimitViolationError, match=f"not {LIMITS.attachments + 1}"):
            await delivery.reply_to(ctx, files=[discord.File(io.BytesIO(b"host"), filename="host.txt")])(
                v2(assets=assets)
            )

        assert ctx.sent == {}

    async def test_the_delivered_mode_is_what_the_handle_starts_from(self) -> None:
        ctx = _Replyable()

        receipt = await delivery.reply_to(ctx)(v2())

        assert receipt.handle is not None
        assert receipt.handle.mode is V2


class _Channel:
    def __init__(self, message: Any) -> None:
        self.id = message.channel.id
        self._message = message

    async def fetch_message(self, message_id: int) -> Any:
        assert message_id == self._message.id
        return self._message


class TestDurableMode:
    """The mode is recorded beside the locator, so recovery does not have to re-derive it."""

    async def test_promotion_records_the_mode_and_recovery_restores_it(self) -> None:
        message = fake_message()
        mount = sl.discord.Mount(Panel(), access=sl.discord.Everyone(), timeout=None)
        sent = await mount.send(delivered_to(message))
        assert isinstance(sent, delivery.Delivered)
        client = SimpleNamespace(get_channel=lambda _id: _Channel(message), fetch_channel=AsyncMock())
        frontend = DiscordFrontend(client)  # type: ignore[arg-type]

        promoted = await frontend.promote(mount, sent.receipt)

        assert isinstance(promoted, Promoted)
        assert promoted.locator.values["mode"] == "components_v2"

        # The stored mode wins over the flag on the message that comes back, which is what
        # makes it a restored fact rather than a re-derived one.
        restored = sl.discord.Mount(Panel(), access=sl.discord.Everyone(), timeout=None)
        locator = MountLocator("discord", {**promoted.locator.values, "mode": "classic"})
        message.edit.reset_mock()

        result = await frontend.reconnect([RecoveredBinding("mount-1", restored, locator)])

        assert isinstance(result, Reconnected)
        assert message.edit.await_args.kwargs["content"] is None
        assert message.edit.await_args.kwargs["embeds"] == []

    async def test_a_record_written_before_the_mode_existed_falls_back_to_the_flag(self) -> None:
        message = fake_message()
        mount = sl.discord.Mount(Panel(), access=sl.discord.Everyone(), timeout=None)
        client = SimpleNamespace(get_channel=lambda _id: _Channel(message), fetch_channel=AsyncMock())
        frontend = DiscordFrontend(client)  # type: ignore[arg-type]
        locator = MountLocator("discord", {"channel_id": message.channel.id, "message_id": message.id})

        result = await frontend.reconnect([RecoveredBinding("mount-1", mount, locator)])

        assert isinstance(result, Reconnected)
        assert "content" not in message.edit.await_args.kwargs

    def test_the_mode_survives_a_durable_record_round_trip(self) -> None:
        locator = MountLocator("discord", {"channel_id": 5, "message_id": 99, "mode": "components_v2"})
        record = sl.discord.durability.DurableMountRecord(
            protocol=1,
            state=sl.discord.durability.MountState(
                protocol=sl.discord.durability.MountStateCodec.protocol,
                component_key="panel",
                component_version=1,
                components=(),
                presentation=sl.discord.durability.PresentationState({}, {}, {}, {}),
                target_fingerprint=sl.discord.V2_TARGET.fingerprint,
            ),
            locator=locator,
        )

        restored = sl.discord.durability.DurableMountCodec.loads(sl.discord.durability.DurableMountCodec.dumps(record))

        assert restored.locator.values["mode"] == "components_v2"
