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

import squid_ui as sl
import squid_ui_discord
from squid_ui.assets import Asset, InlineAsset
from squid_ui.errors import LimitViolationError
from squid_ui.planning.limits import LIMITS
from squid_ui.primitives import Text
from squid_ui_discord import delivery
from squid_ui_discord import testing as sd
from squid_ui_discord.durability import FrontendAddress
from squid_ui_discord.durability.frontend import DiscordFrontend, Promoted, Reconnected, RecoveredBinding
from squid_ui_discord.message_payload import MessageMode, MessageModeError, MessagePayload, message_mode
from squid_ui_discord.testing import delivered_to, fake_interaction, fake_message

CLASSIC = MessageMode.CLASSIC
V2 = MessageMode.COMPONENTS_V2


class Panel(sl.Component[sl.ComponentsV2Target]):
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


def v2(view: discord.ui.LayoutView | None = None, *, assets: tuple[Asset, ...] = ()) -> MessagePayload:
    return MessagePayload.components_v2(a_layout() if view is None else view, assets=assets)


class TestCoherence:
    """The invalid combinations are exactly the ones Discord answers with an unhelpful 400."""

    def test_components_v2_refuses_content_and_embeds(self) -> None:
        with pytest.raises(MessageModeError, match="cannot carry content"):
            MessagePayload(V2, content="hi", view=a_layout())

        with pytest.raises(MessageModeError, match="cannot carry embeds"):
            MessagePayload(V2, embeds=(discord.Embed(title="hi"),), view=a_layout())

    def test_components_v2_needs_a_layout_view(self) -> None:
        with pytest.raises(MessageModeError, match="needs a LayoutView"):
            MessagePayload(V2, view=a_classic_view())

        with pytest.raises(MessageModeError, match="needs a LayoutView"):
            MessagePayload(V2)

    def test_classic_refuses_a_layout_view(self) -> None:
        with pytest.raises(MessageModeError, match="cannot carry a LayoutView"):
            MessagePayload(CLASSIC, view=a_layout())

    def test_classic_refuses_a_view_that_reports_components_v2(self) -> None:
        with pytest.raises(MessageModeError, match="sets the flag implicitly"):
            MessagePayload(CLASSIC, content="hi", view=_FlaggedView(timeout=None))

    def test_every_disagreement_is_reported_at_once(self) -> None:
        with pytest.raises(MessageModeError) as raised:
            MessagePayload(V2, content="hi", embeds=(discord.Embed(title="hi"),))

        assert str(raised.value).count(";") == 2

    def test_a_coherent_presentation_of_each_mode_is_accepted(self) -> None:
        assert v2().mode is V2
        classic = MessagePayload.classic(content="hi", embeds=(discord.Embed(title="hi"),))
        assert classic.mode is CLASSIC
        assert MessagePayload.classic(content="hi", view=a_classic_view()).view is not None

    def test_sequences_are_frozen_into_tuples(self) -> None:
        payload = MessagePayload.classic(content="hi", embeds=[discord.Embed(title="hi")])
        assert isinstance(payload.embeds, tuple)
        assert isinstance(v2(assets=(an_asset(),)).assets, tuple)


class TestPayload:
    def test_files_are_repeatable_and_fresh_every_call(self) -> None:
        payload = v2(assets=(an_asset(),))

        first, second = payload.build_files(), payload.build_files()

        assert [file.filename for file in first] == ["report.txt"]
        assert first[0] is not second[0]
        assert first[0].fp.read() == second[0].fp.read() == b"full report"

    def test_layout_is_the_view_for_a_v2_presentation_and_refuses_a_classic_one(self) -> None:
        view = a_layout()
        assert v2(view).layout is view

        with pytest.raises(MessageModeError, match="no LayoutView"):
            _ = MessagePayload.classic(content="hi").layout

    def test_message_mode_reads_the_flag_discord_set(self) -> None:
        assert message_mode(fake_message()) is V2
        assert message_mode(fake_message(components_v2=False)) is CLASSIC


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

        await handle.write(MessagePayload.classic(content="body", embeds=(embed,)))

        fields = message.edit.await_args.kwargs
        assert fields["content"] == "body"
        assert fields["embeds"] == [embed]
        assert fields["view"] is None
        assert handle.mode is CLASSIC

    async def test_v2_to_classic_is_refused_before_any_request(self) -> None:
        message = fake_message()
        handle = delivery.handle_for(message)

        with pytest.raises(MessageModeError, match="back off a sent message"):
            await handle.write(MessagePayload.classic(content="body"))

        message.edit.assert_not_awaited()

    async def test_the_original_response_handle_runs_the_same_matrix(self) -> None:
        interaction = fake_interaction(components_v2=False)
        destination = delivery.respond_to(interaction, wait=True)
        result = await destination(v2())
        handle = result.handle
        assert handle is not None

        await handle.write(v2())

        fields = interaction.edit_original_response.await_args.kwargs
        assert "content" not in fields
        with pytest.raises(MessageModeError):
            await handle.write(MessagePayload.classic(content="body"))

    async def test_the_webhook_handle_runs_the_same_matrix(self) -> None:
        interaction = fake_interaction(components_v2=False)
        handle = delivery.handle_from(interaction)
        assert handle is not None and handle.mode is CLASSIC

        await handle.write(v2())

        fields = interaction.response.edit_message.await_args.kwargs
        assert fields["content"] is None
        assert fields["embeds"] == []
        assert handle.mode is V2

        with pytest.raises(MessageModeError):
            await handle.write(MessagePayload.classic(content="body"))

    async def test_a_handle_with_no_readable_message_still_refuses_the_illegal_edit(self) -> None:
        # `wait=False` on a fresh response: nothing ever fetched the message, so the mode the
        # destination delivered is the only thing that knows what is on it.
        interaction = fake_interaction()
        result = await delivery.respond_to(interaction)(v2())
        handle = result.handle
        assert result.message is None
        assert handle is not None and handle.mode is V2

        with pytest.raises(MessageModeError):
            await handle.write(MessagePayload.classic(content="body"))

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
        message.edit.side_effect = sd.http_error()

        with pytest.raises(discord.HTTPException):
            await handle.write(v2())

        assert handle.mode is CLASSIC


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

        await delivery.reply_to(ctx)(MessagePayload.classic(content="body", embeds=(embed,)))

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

        result = await delivery.reply_to(ctx)(v2())

        assert result.handle is not None
        assert result.handle.mode is V2

    async def test_edit_to_writes_a_message_the_bot_already_owns(self) -> None:
        message = fake_message()

        result = await delivery.edit_to(message)(v2())

        assert result.message is message
        assert isinstance(message.edit.await_args.kwargs["view"], discord.ui.LayoutView)

    async def test_edit_to_hands_back_a_handle_carrying_the_presentations_mode(self) -> None:
        """The bare `handle_for` a host would reach for reads the flag, which is now stale."""
        message = fake_message(components_v2=False)

        result = await delivery.edit_to(message)(v2())

        assert result.handle is not None
        assert result.handle.mode is V2
        assert delivery.handle_for(message).mode is MessageMode.CLASSIC

    async def test_edit_to_runs_the_transition_matrix_for_the_message_it_edits(self) -> None:
        message = fake_message(components_v2=False)

        await delivery.edit_to(message)(v2())

        # The one transition with legacy fields to clear, chosen from the message's own flag.
        assert message.edit.await_args.kwargs["content"] is None
        assert message.edit.await_args.kwargs["embeds"] == []

    async def test_edit_to_refuses_a_transition_discord_does_not_offer(self) -> None:
        message = fake_message()

        with pytest.raises(MessageModeError):
            await delivery.edit_to(message)(MessagePayload.classic(content="body"))

        message.edit.assert_not_awaited()

    async def test_edit_to_merges_host_files_ahead_of_the_presentations_own(self) -> None:
        message = fake_message()
        host = discord.File(io.BytesIO(b"host"), filename="host.txt")

        await delivery.edit_to(message, files=[host])(v2(assets=(an_asset(),)))

        attachments = message.edit.await_args.kwargs["attachments"]
        assert [file.filename for file in attachments] == ["host.txt", "report.txt"]

    async def test_edit_to_translates_expired_authority(self) -> None:
        message = fake_message()
        message.edit.side_effect = sd.stale_http_error()

        with pytest.raises(delivery.StaleHandleError):
            await delivery.edit_to(message)(v2())

    async def test_deliver_to_answers_a_command_through_reply_to(self) -> None:
        ctx = _Replyable()

        result = await delivery.deliver_to(ctx, ephemeral=True)(v2())

        assert ctx.sent["ephemeral"] is True
        assert result.handle is not None

    async def test_deliver_to_answers_an_interaction_through_respond_to(self) -> None:
        interaction = fake_interaction(user_id=7)

        await delivery.deliver_to(interaction, ephemeral=True, wait=False)(v2())

        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


class _Channel:
    def __init__(self, message: Any) -> None:
        self.id = message.channel.id
        self._message = message

    async def fetch_message(self, message_id: int) -> Any:
        assert message_id == self._message.id
        return self._message


class TestDurableMode:
    """The mode is recorded beside the address, so recovery does not have to re-derive it."""

    async def test_promotion_records_the_mode_and_recovery_restores_it(self) -> None:
        message = fake_message()
        message_root = squid_ui_discord.MessageRoot(Panel(), access=squid_ui_discord.Everyone(), timeout=None)
        sent = await message_root.send(delivered_to(message))
        assert isinstance(sent, delivery.Delivered)
        client = SimpleNamespace(get_channel=lambda _id: _Channel(message), fetch_channel=AsyncMock())
        frontend = DiscordFrontend(client)  # type: ignore[arg-type]

        promoted = await frontend.promote(message_root, sent.result)

        assert isinstance(promoted, Promoted)
        assert promoted.address.values["mode"] == "components_v2"

        # The stored mode wins over the flag on the message that comes back, which is what
        # makes it a restored fact rather than a re-derived one.
        restored = squid_ui_discord.MessageRoot(Panel(), access=squid_ui_discord.Everyone(), timeout=None)
        address = FrontendAddress("discord", {**promoted.address.values, "mode": "classic"})
        message.edit.reset_mock()

        result = await frontend.reconnect([RecoveredBinding("mount-1", restored, address)])

        assert isinstance(result, Reconnected)
        assert message.edit.await_args.kwargs["content"] is None
        assert message.edit.await_args.kwargs["embeds"] == []

    async def test_a_record_written_before_the_mode_existed_falls_back_to_the_flag(self) -> None:
        message = fake_message()
        message_root = squid_ui_discord.MessageRoot(Panel(), access=squid_ui_discord.Everyone(), timeout=None)
        client = SimpleNamespace(get_channel=lambda _id: _Channel(message), fetch_channel=AsyncMock())
        frontend = DiscordFrontend(client)  # type: ignore[arg-type]
        address = FrontendAddress("discord", {"channel_id": message.channel.id, "message_id": message.id})

        result = await frontend.reconnect([RecoveredBinding("mount-1", message_root, address)])

        assert isinstance(result, Reconnected)
        assert "content" not in message.edit.await_args.kwargs

    def test_the_mode_survives_a_durable_record_round_trip(self) -> None:
        address = FrontendAddress("discord", {"channel_id": 5, "message_id": 99, "mode": "components_v2"})
        record = squid_ui_discord.durability.DurableMessageRootRecord(
            protocol=1,
            state=squid_ui_discord.durability.MessageRootState(
                protocol=squid_ui_discord.durability.MessageRootStateCodec.protocol,
                component_key="panel",
                component_version=1,
                components=(),
                presentation=squid_ui_discord.durability.PresentationSnapshot({}, {}, {}, {}),
                target_fingerprint=squid_ui_discord.DISCORD_V2_DPY27.fingerprint,
            ),
            address=address,
        )

        encoded = squid_ui_discord.durability.DurableMessageRootCodec.dumps(record)
        assert '"state"' in encoded
        assert '"address"' in encoded
        assert '"snapshot"' not in encoded
        assert '"locator"' not in encoded

        restored = squid_ui_discord.durability.DurableMessageRootCodec.loads(encoded)
        assert restored.address.values["mode"] == "components_v2"
