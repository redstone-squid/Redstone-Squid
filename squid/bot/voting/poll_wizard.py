"""Ephemeral modal workflow for composing generic polls."""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast, override

import discord
from whenever import Instant

from squid.bot._types import GuildMessageable
from squid.bot.errors import ErrorHandledModal, ExpiringLayoutView
from squid.bot.message_adapter import to_tracked_message
from squid.bot.utils.components import edit_interaction_layout, edit_layout, no_mentions, text_layout
from squid.bot.voting.generic_session import GenericVoteSession
from squid.voting.domain import VoteOption, VoteVisibility
from squid.voting.errors import InvalidVoteConfigurationError

if TYPE_CHECKING:
    from squid.bot.voting.vote import VoteCog

_DURATION = re.compile(r"^(\d+)\s*([mhd])$", re.IGNORECASE)


def parse_poll_duration(value: str) -> int:
    """Parse a compact duration and return seconds within the supported range."""
    match = _DURATION.fullmatch(value.strip())
    if match is None:
        msg = "Duration must look like `30m`, `12h`, or `7d`."
        raise InvalidVoteConfigurationError(msg)
    amount = int(match.group(1))
    multiplier = {"m": 60, "h": 3600, "d": 86400}[match.group(2).lower()]
    seconds = amount * multiplier
    if not 60 <= seconds <= 30 * 86400:
        msg = "Poll duration must be between 1 minute and 30 days."
        raise InvalidVoteConfigurationError(msg)
    return seconds


@dataclass(frozen=True, slots=True)
class PollDraft:
    question: str
    duration: str
    options: str
    visibility: VoteVisibility


class PollModal(ErrorHandledModal):
    """Collect and validate the editable poll fields."""

    def __init__(self, cog: "VoteCog", draft: PollDraft | None = None):
        super().__init__(title="Create a poll")
        self.cog = cog
        self.question = discord.ui.TextInput(default=draft.question if draft else "", max_length=300)
        self.duration = discord.ui.TextInput(default=draft.duration if draft else "24h", max_length=8)
        self.options = discord.ui.TextInput(
            default=draft.options if draft else "",
            style=discord.TextStyle.paragraph,
            placeholder="emoji | label (emoji may be omitted)",
            min_length=3,
            max_length=1000,
        )
        self.visibility = discord.ui.TextInput(
            default=draft.visibility if draft else "anonymous_live",
            placeholder="anonymous_live, visible_live, or anonymous_hidden",
            max_length=24,
        )
        self.add_item(discord.ui.Label(text="Question", component=self.question))
        self.add_item(discord.ui.Label(text="Duration", component=self.duration))
        self.add_item(discord.ui.Label(text="Options (one per line)", component=self.options))
        self.add_item(discord.ui.Label(text="Visibility", component=self.visibility))

    @override
    async def on_submit(self, interaction: discord.Interaction) -> None:
        visibility = self.visibility.value.strip()
        if visibility not in ("anonymous_live", "visible_live", "anonymous_hidden"):
            await interaction.response.send_message("Invalid visibility.", ephemeral=True)
            return
        try:
            parse_poll_duration(self.duration.value)
            draft = PollDraft(
                self.question.value.strip(),
                self.duration.value.strip(),
                self.options.value.strip(),
                visibility,
            )
            options = await self.cog.parse_poll_options(interaction, draft.options)
        except InvalidVoteConfigurationError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        confirmation = PollConfirmation(self.cog, interaction.user.id, draft, options)
        await interaction.response.send_message(
            view=confirmation,
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )
        confirmation.bind_message(await interaction.original_response())


class PollConfirmation(ExpiringLayoutView):
    """Preview controls that publish, edit, or cancel a poll draft."""

    actions = discord.ui.ActionRow()

    def __init__(self, cog: "VoteCog", owner_id: int, draft: PollDraft, options: tuple[VoteOption, ...]):
        super().__init__(timeout=900)
        self.cog = cog
        self.owner_id = owner_id
        self.draft = draft
        self.options = options
        self.published = False
        controls = self.actions
        self.clear_items()
        preview = "\n".join([f"## {draft.question}", *(f"{item.emoji} {item.label}" for item in options)])
        self.add_item(discord.ui.TextDisplay(preview))
        self.add_item(controls)

    @override
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("This poll draft belongs to another member.", ephemeral=True)
        return False

    @actions.button(label="Publish", style=discord.ButtonStyle.success)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button["PollConfirmation"]) -> None:
        if self.published:
            await interaction.response.send_message("This poll has already been published.", ephemeral=True)
            return
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("Polls can only be published in a server.", ephemeral=True)
            return
        self.published = True
        author = await self.cog.bot.services.accounts.get_or_create_account(interaction.user.id)
        assert author.id is not None
        session_id = await self.cog.vote_service.start_generic_vote(
            author_account_id=author.id,
            guild_id=interaction.guild.id,
            question=self.draft.question,
            visibility=self.draft.visibility,
            deadline=Instant.now().add(seconds=parse_poll_duration(self.draft.duration)),
            options=self.options,
        )
        await interaction.response.defer(ephemeral=True)
        channel = cast(GuildMessageable, interaction.channel)
        message = await channel.send(view=text_layout("Publishing poll…"), allowed_mentions=no_mentions())
        await self.cog.bot.services.messages.track(
            to_tracked_message(message), purpose="vote", vote_session_id=session_id
        )
        session = await GenericVoteSession.from_id(self.cog.bot, session_id)
        assert session is not None
        await edit_layout(message, text_layout(session.render()), allowed_mentions=no_mentions())
        await session.add_reactions(message)
        await interaction.edit_original_response(
            view=text_layout(f"Published: {message.jump_url}"), allowed_mentions=no_mentions()
        )
        self.stop()

    @actions.button(label="Edit", style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button["PollConfirmation"]) -> None:
        await interaction.response.send_modal(PollModal(self.cog, self.draft))

    @actions.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button["PollConfirmation"]) -> None:
        self.stop()
        await edit_interaction_layout(interaction, text_layout("Poll cancelled."))
