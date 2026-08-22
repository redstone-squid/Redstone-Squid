"""Component-driven wizard for composing and publishing generic polls."""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast, override

import discord

import squid_layouts as sl
from squid.bot._types import GuildMessageable
from squid.bot.errors import ErrorHandledModal, ExpiringLayoutView
from squid.bot.ui import create_mount
from squid.bot.utils.components import edit_interaction_layout, no_mentions, text_layout
from squid.voting.domain import (
    MAX_POLL_DURATION_SECONDS,
    MIN_POLL_DURATION_SECONDS,
    PollScope,
    VoteChoice,
    VoteOption,
    VoteVisibility,
)
from squid.voting.errors import InvalidVoteConfigurationError
from squid_layouts.discord import SessionKey

if TYPE_CHECKING:
    from squid.bot.voting.publisher import PollPublisher

_DURATION = re.compile(r"^(\d+)\s*([mhd])$", re.IGNORECASE)
_DURATION_UNITS = {"m": 60, "h": 3600, "d": 86400}

DURATION_PRESETS: tuple[tuple[str, int], ...] = (
    ("1 hour", 3600),
    ("6 hours", 6 * 3600),
    ("12 hours", 12 * 3600),
    ("24 hours", 24 * 3600),
    ("3 days", 3 * 86400),
    ("7 days", 7 * 86400),
)
CUSTOM_DURATION = "custom"

VISIBILITY_CHOICES: tuple[tuple[VoteVisibility, str, str], ...] = (
    (
        VoteVisibility.ANONYMOUS_LIVE,
        "Live Anonymous",
        "Running totals are public; who voted for what is not.",
    ),
    (
        VoteVisibility.VISIBLE_LIVE,
        "Live Public",
        "Reactions stay on the message, so every ballot is attributable.",
    ),
    (
        VoteVisibility.ANONYMOUS_HIDDEN,
        "Hidden until Close",
        "No totals at all until the poll closes.",
    ),
)


def parse_poll_duration(value: str) -> int:
    """Parse a compact duration and return seconds within the supported range."""
    match = _DURATION.fullmatch(value.strip())
    if match is None:
        msg = "Duration must look like `30m`, `12h`, or `7d`."
        raise InvalidVoteConfigurationError(msg)
    seconds = int(match.group(1)) * _DURATION_UNITS[match.group(2).lower()]
    if not MIN_POLL_DURATION_SECONDS <= seconds <= MAX_POLL_DURATION_SECONDS:
        msg = "Poll duration must be between 1 minute and 30 days."
        raise InvalidVoteConfigurationError(msg)
    return seconds


def format_duration(seconds: int) -> str:
    """Render a duration the way the presets are labelled."""
    for label, preset in DURATION_PRESETS:
        if preset == seconds:
            return label
    if seconds % 86400 == 0:
        return f"{seconds // 86400} days"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} hours"
    return f"{seconds // 60} minutes"


def parse_option_lines(
    lines: Sequence[str],
    *,
    guild_id: int,
    palette: Sequence[VoteOption],
    emoji_is_usable: Callable[[str], bool] = lambda _emoji: True,
) -> tuple[VoteOption, ...]:
    """Validate `emoji | label` lines, filling missing aliases from the guild palette.

    Pure apart from the injected emoji check, so the parsing rules are testable
    without a Discord client.
    """
    cleaned = [line.strip() for line in lines if line.strip()]
    if not 2 <= len(cleaned) <= 10:
        msg = "Enter between 2 and 10 option lines."
        raise InvalidVoteConfigurationError(msg)
    options: list[VoteOption] = []
    for index, line in enumerate(cleaned):
        if "|" in line:
            emoji, label = (part.strip() for part in line.split("|", 1))
        else:
            if index >= len(palette):
                msg = "The configured generic emoji palette does not have enough entries for these options."
                raise InvalidVoteConfigurationError(msg)
            emoji, label = palette[index].emoji, line
        if not emoji or not label:
            msg = "Each option needs a non-empty emoji and label."
            raise InvalidVoteConfigurationError(msg)
        if not emoji_is_usable(emoji):
            msg = f"The custom emoji {emoji} is not accessible to this bot."
            raise InvalidVoteConfigurationError(msg)
        options.append(
            VoteOption(
                emoji,
                VoteChoice.GENERIC,
                identifier=str(index + 1),
                guild_id=guild_id,
                label=label,
                position=index,
            )
        )
    if len({option.emoji for option in options}) != len(options):
        msg = "Poll option emojis must be unique."
        raise InvalidVoteConfigurationError(msg)
    return tuple(options)


SCOPE_CHOICES: tuple[tuple[PollScope, str, str], ...] = (
    (PollScope.GUILD, "This server", "Card the poll in this channel only."),
    (PollScope.NETWORK, "Every server", "Card the poll in every server's vote channel."),
)


@dataclass(frozen=True, slots=True)
class PollDraft:
    """The wizard's editable state between the modal and publication."""

    question: str
    options_text: str
    visibility: VoteVisibility = VoteVisibility.ANONYMOUS_LIVE
    duration_seconds: int = 24 * 3600
    scope: PollScope = PollScope.GUILD

    @property
    def option_lines(self) -> tuple[str, ...]:
        return tuple(self.options_text.splitlines())


class PollModal(ErrorHandledModal):
    """Collect the free-text half of a poll: its question and its option lines."""

    def __init__(
        self,
        publisher: PollPublisher,
        author_account_id: int,
        draft: PollDraft | None = None,
        *,
        allow_network: bool = False,
    ):
        super().__init__(title="Create a poll")
        self.publisher = publisher
        self.allow_network = allow_network
        self.author_account_id = author_account_id
        self.draft = draft
        self.question = discord.ui.TextInput(default=draft.question if draft else "", max_length=300)
        self.options = discord.ui.TextInput(
            default=draft.options_text if draft else "",
            style=discord.TextStyle.paragraph,
            placeholder="emoji | label (emoji may be omitted)",
            min_length=3,
            max_length=1000,
        )
        self.add_item(discord.ui.Label(text="Question", component=self.question))
        self.add_item(discord.ui.Label(text="Options (one per line)", component=self.options))

    @override
    async def on_submit(self, interaction: discord.Interaction[Any]) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Polls can only be created in a server.", ephemeral=True)
            return
        base = self.draft or PollDraft(question="", options_text="")
        draft = replace(
            base,
            question=self.question.value.strip(),
            options_text=self.options.value.strip(),
        )
        try:
            options = await self.publisher.resolve_options(interaction.guild.id, draft.option_lines)
        except InvalidVoteConfigurationError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        component = PollConfirmationComponent(
            self.publisher,
            interaction.user.id,
            self.author_account_id,
            draft,
            options,
            allow_network=self.allow_network,
        )
        # REPLACE rather than REJECT: the wizard's own Edit button re-opens this modal, so
        # turning a second wizard away would turn away the edit. One live wizard per user per
        # guild, and it is always the one they last submitted.
        await interaction.client.mounts.open(
            component.mount(),
            sl.discord.respond_to(interaction, ephemeral=True, wait=True),
            key=SessionKey.user_guild("poll-wizard", interaction.user.id, interaction.guild.id),
            actor_id=interaction.user.id,
        )


class CustomDurationModal(ErrorHandledModal):
    """Accept a duration outside the presets."""

    def __init__(self, confirmation: PollConfirmation):
        super().__init__(title="Custom poll duration")
        self.confirmation = confirmation
        self.duration = discord.ui.TextInput(default="24h", max_length=8, placeholder="30m, 12h, 7d")
        self.add_item(discord.ui.Label(text="Duration", component=self.duration))

    @override
    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            seconds = parse_poll_duration(self.duration.value)
        except InvalidVoteConfigurationError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await self.confirmation.set_duration(interaction, seconds)


class VisibilitySelect(discord.ui.Select["PollConfirmation"]):
    """Choose how much of an open poll is disclosed."""

    def __init__(self, confirmation: PollConfirmation) -> None:
        self.confirmation = confirmation
        super().__init__(
            placeholder="Who can see what, and when",
            options=[
                discord.SelectOption(label=label, value=value.value, description=description)
                for value, label, description in VISIBILITY_CHOICES
            ],
        )
        self.mark_selected(confirmation.draft.visibility)

    def mark_selected(self, visibility: VoteVisibility) -> None:
        for option in self.options:
            option.default = option.value == visibility.value

    @override
    async def callback(self, interaction: discord.Interaction) -> None:
        await self.confirmation.set_visibility(interaction, VoteVisibility(self.values[0]))


class ScopeSelect(discord.ui.Select["PollConfirmation"]):
    """Choose whether a poll stays here or reaches every server."""

    def __init__(self, confirmation: PollConfirmation) -> None:
        self.confirmation = confirmation
        super().__init__(
            placeholder="Where the poll appears",
            options=[
                discord.SelectOption(label=label, value=value.value, description=description)
                for value, label, description in SCOPE_CHOICES
            ],
        )
        self.mark_selected(confirmation.draft.scope)

    def mark_selected(self, scope: PollScope) -> None:
        for option in self.options:
            option.default = option.value == scope.value

    @override
    async def callback(self, interaction: discord.Interaction) -> None:
        await self.confirmation.set_scope(interaction, PollScope(self.values[0]))


class DurationSelect(discord.ui.Select["PollConfirmation"]):
    """Choose how long a poll stays open, with an escape hatch for odd durations."""

    def __init__(self, confirmation: PollConfirmation) -> None:
        self.confirmation = confirmation
        super().__init__(
            placeholder="How long the poll stays open",
            options=[
                *(discord.SelectOption(label=label, value=str(seconds)) for label, seconds in DURATION_PRESETS),
                discord.SelectOption(label="Custom…", value=CUSTOM_DURATION),
            ],
        )
        self.mark_selected(confirmation.draft.duration_seconds)

    def mark_selected(self, seconds: int) -> None:
        for option in self.options:
            option.default = option.value == str(seconds)

    @override
    async def callback(self, interaction: discord.Interaction) -> None:
        chosen = self.values[0]
        if chosen == CUSTOM_DURATION:
            await interaction.response.send_modal(CustomDurationModal(self.confirmation))  # pyrefly: ignore[no-matching-overload]
            return
        await self.confirmation.set_duration(interaction, int(chosen))


class PollConfirmation(ExpiringLayoutView):
    """Preview the draft and collect visibility and duration before publishing."""

    controls = discord.ui.ActionRow()

    def __init__(
        self,
        publisher: PollPublisher,
        owner_id: int,
        author_account_id: int,
        draft: PollDraft,
        options: tuple[VoteOption, ...],
        *,
        allow_network: bool = False,
    ):
        super().__init__(timeout=900)
        self.publisher = publisher
        self.owner_id = owner_id
        self.author_account_id = author_account_id
        self.draft = draft
        self.options = options
        self.published = False
        self.allow_network = allow_network
        self.visibility_select = VisibilitySelect(self)
        self.duration_select = DurationSelect(self)
        self.scope_select = ScopeSelect(self) if allow_network else None
        self._render()

    def _render(self) -> None:
        """Rebuild the ephemeral preview so the selects show their current state."""
        controls = self.controls
        self.clear_items()
        preview = "\n".join(
            [
                f"## {self.draft.question}",
                *(f"{option.emoji} {option.label}" for option in self.options),
                "",
                f"**Visibility:** {self._visibility_label()}",
                f"**Closes after:** {format_duration(self.draft.duration_seconds)}",
                *([f"**Reaches:** {self._scope_label()}"] if self.allow_network else []),
            ]
        )
        self.add_item(discord.ui.TextDisplay(preview))
        self.add_item(discord.ui.ActionRow(self.visibility_select))
        self.add_item(discord.ui.ActionRow(self.duration_select))
        if self.scope_select is not None:
            self.add_item(discord.ui.ActionRow(self.scope_select))
        self.add_item(controls)

    def _scope_label(self) -> str:
        return next(label for value, label, _ in SCOPE_CHOICES if value is self.draft.scope)

    async def set_scope(self, interaction: discord.Interaction, scope: PollScope) -> None:
        """Apply a publication reach chosen from the select."""
        self.draft = replace(self.draft, scope=scope)
        if self.scope_select is not None:
            self.scope_select.mark_selected(scope)
        self._render()
        await edit_interaction_layout(interaction, self)

    def _visibility_label(self) -> str:
        return next(label for value, label, _ in VISIBILITY_CHOICES if value is self.draft.visibility)

    @override
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("This poll draft belongs to another member.", ephemeral=True)
        return False

    async def set_visibility(self, interaction: discord.Interaction, visibility: VoteVisibility) -> None:
        """Apply a disclosure mode chosen from the select."""
        self.draft = replace(self.draft, visibility=visibility)
        self.visibility_select.mark_selected(visibility)
        self._render()
        await edit_interaction_layout(interaction, self)

    async def set_duration(self, interaction: discord.Interaction, seconds: int) -> None:
        """Apply a duration chosen from a preset or typed into the custom modal."""
        self.draft = replace(self.draft, duration_seconds=seconds)
        self.duration_select.mark_selected(seconds)
        self._render()
        await edit_interaction_layout(interaction, self)

    @controls.button(label="Publish", style=discord.ButtonStyle.success)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button[PollConfirmation]) -> None:
        del button
        if self.published:
            await interaction.response.send_message("This poll has already been published.", ephemeral=True)
            return
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("Polls can only be published in a server.", ephemeral=True)
            return
        if self.draft.scope is PollScope.NETWORK and not (
            isinstance(interaction.user, discord.Member) and await self.publisher.may_create_network(interaction.user)
        ):
            # Re-checked here rather than trusted from when the wizard opened, since
            # the grant can be revoked while a draft sits open.
            await interaction.response.send_message("You may no longer publish a poll to every server.", ephemeral=True)
            return
        self.published = True
        await interaction.response.defer(ephemeral=True)
        try:
            message = await self.publisher.create_and_publish(
                author_account_id=self.author_account_id,
                channel=cast(GuildMessageable, interaction.channel),
                question=self.draft.question,
                visibility=self.draft.visibility,
                duration_seconds=self.draft.duration_seconds,
                options=self.options,
                scope=self.draft.scope,
            )
        except InvalidVoteConfigurationError as error:
            self.published = False
            await interaction.edit_original_response(view=text_layout(str(error)), allowed_mentions=no_mentions())
            return
        await interaction.edit_original_response(
            view=text_layout(f"Published: {message.jump_url}"), allowed_mentions=no_mentions()
        )
        self.stop()

    @controls.button(label="Edit", style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button[PollConfirmation]) -> None:
        del button
        await interaction.response.send_modal(  # pyrefly: ignore[no-matching-overload]
            PollModal(self.publisher, self.author_account_id, self.draft)
        )

    @controls.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button[PollConfirmation]) -> None:
        del button
        self.stop()
        await edit_interaction_layout(interaction, text_layout("Poll cancelled."))


class PollConfirmationComponent(sl.Component):
    """A semantic poll preview and publication workspace."""

    published: bool = sl.state(default=False)
    # Supplied by the caller on every open, so a snapshot would only restore it stale.
    draft: PollDraft = sl.state(persist=False)

    def __init__(
        self,
        publisher: PollPublisher,
        owner_id: int,
        author_account_id: int,
        draft: PollDraft,
        options: tuple[VoteOption, ...],
        *,
        allow_network: bool = False,
        timeout: float = 900,
    ) -> None:
        self.publisher = publisher
        self.owner_id = owner_id
        self.author_account_id = author_account_id
        self.draft = draft
        self.options = options
        self.allow_network = allow_network
        self._timeout = timeout
        self._mount: sl.discord.Mount | None = None

    def render(self) -> tuple[sl.LayoutNode, ...]:
        if self.published:
            return (sl.status("Poll published."),)
        preview = "\n".join(
            [
                f"## {self.draft.question}",
                *(f"{option.emoji} {option.label}" for option in self.options),
            ]
        )
        fields = [
            sl.field("Visibility", self._visibility_label()),
            sl.field("Closes after", format_duration(self.draft.duration_seconds)),
        ]
        if self.allow_network:
            fields.append(sl.field("Reaches", self._scope_label()))
        nodes: list[sl.LayoutNode] = [
            # `preview` already opens with its own "## question" line, so this renders a
            # double "##". Pre-existing, and fixing rendering is out of scope here.
            sl.section(sl.fields(*fields), heading=preview),
            sl.Choices(
                key="visibility",
                choices=tuple(
                    sl.Choice(value.value, label, description) for value, label, description in VISIBILITY_CHOICES
                ),
                selection=sl.controlled((self.draft.visibility.value,), self._visibility_changed),
            ),
            sl.Choices(
                key="duration",
                choices=tuple(
                    sl.Choice(str(seconds), label)
                    for label, seconds in (*DURATION_PRESETS, ("Custom…", CUSTOM_DURATION))
                ),
                selection=sl.controlled((str(self.draft.duration_seconds),), self._duration_changed),
            ),
        ]
        if self.allow_network:
            nodes.append(
                sl.Choices(
                    key="scope",
                    choices=tuple(
                        sl.Choice(value.value, label, description) for value, label, description in SCOPE_CHOICES
                    ),
                    selection=sl.controlled((self.draft.scope.value,), self._scope_changed),
                )
            )
        nodes.append(
            sl.primitives.Row(
                (
                    sl.primitives.Button(
                        "Publish",
                        self._publish,
                        "publish",
                        style=sl.primitives.ActionStyle.SUCCESS,
                    ),
                    sl.primitives.Button("Edit", self._edit, "edit"),
                    sl.primitives.Button(
                        "Cancel",
                        self._cancel,
                        "cancel",
                        style=sl.primitives.ActionStyle.DANGER,
                    ),
                )
            )
        )
        return tuple(nodes)

    async def _visibility_changed(self, event: sl.ChoiceEvent) -> None:
        self.draft = replace(self.draft, visibility=VoteVisibility(event.selected[0]))

    async def _duration_changed(self, event: sl.ChoiceEvent) -> None:
        chosen = event.selected[0]
        if chosen == CUSTOM_DURATION:
            await event.present_form(
                sl.FormSpec(
                    "Custom poll duration",
                    (
                        sl.DurationField(
                            key="duration",
                            label="Duration",
                            placeholder="30m, 12h, 7d",
                            maximum=MAX_POLL_DURATION_SECONDS,
                            minimum=MIN_POLL_DURATION_SECONDS,
                            parser=parse_poll_duration,
                        ),
                    ),
                    prefill={"duration": self.draft.duration_seconds},
                ),
                key="custom-duration",
                on_submit=self._custom_duration_submitted,
            )
            return
        self.draft = replace(self.draft, duration_seconds=int(chosen))

    async def _custom_duration_submitted(self, event: sl.SubmitEvent) -> None:
        self.draft = replace(self.draft, duration_seconds=cast(int, event.values["duration"]))

    async def _scope_changed(self, event: sl.ChoiceEvent) -> None:
        self.draft = replace(self.draft, scope=PollScope(event.selected[0]))

    async def _publish(self, event: sl.PressEvent) -> None:
        interaction = sl.discord.native(event)
        if interaction.guild is None or interaction.channel is None:
            await event.notice("Polls can only be published in a server.")
            return
        if self.draft.scope is PollScope.NETWORK and not (
            isinstance(interaction.user, discord.Member) and await self.publisher.may_create_network(interaction.user)
        ):
            await event.notice("You may no longer publish a poll to every server.")
            return
        self.published = True
        await event.acknowledge()
        try:
            message = await self.publisher.create_and_publish(
                author_account_id=self.author_account_id,
                channel=cast(GuildMessageable, interaction.channel),
                question=self.draft.question,
                visibility=self.draft.visibility,
                duration_seconds=self.draft.duration_seconds,
                options=self.options,
                scope=self.draft.scope,
            )
        except InvalidVoteConfigurationError as error:
            self.published = False
            await event.notice(str(error))
            return
        await event.notice(f"Published: {message.jump_url}")
        await event.finish()

    async def _edit(self, event: sl.PressEvent) -> None:
        await sl.discord.responder(event).send_modal(
            PollModal(
                self.publisher,
                self.author_account_id,
                self.draft,
                allow_network=self.allow_network,
            )
        )

    async def _cancel(self, event: sl.PressEvent) -> None:
        await event.notice("Poll cancelled.")
        await event.finish()

    def _visibility_label(self) -> str:
        return next(label for value, label, _ in VISIBILITY_CHOICES if value is self.draft.visibility)

    def _scope_label(self) -> str:
        return next(label for value, label, _ in SCOPE_CHOICES if value is self.draft.scope)

    def mount(self) -> sl.discord.Mount:
        self._mount = create_mount(self, access=sl.discord.Owner(self.owner_id), timeout=self._timeout)
        return self._mount
