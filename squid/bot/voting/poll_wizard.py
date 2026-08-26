"""Component-driven wizard for composing and publishing generic polls."""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

import discord

import squid_discord as sd
import squid_layouts as sl
from squid.bot._types import GuildMessageable
from squid.bot.ui import create_mount
from squid.voting.domain import (
    MAX_POLL_DURATION_SECONDS,
    MIN_POLL_DURATION_SECONDS,
    PollScope,
    VoteChoice,
    VoteOption,
    VoteVisibility,
)
from squid.voting.errors import InvalidVoteConfigurationError
from squid_discord import SessionKey

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
    """Validate ``emoji | label`` lines, filling missing aliases from the guild palette."""
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
    """The wizard's editable state between the form and publication."""

    question: str
    options_text: str
    visibility: VoteVisibility = VoteVisibility.ANONYMOUS_LIVE
    duration_seconds: int = 24 * 3600
    scope: PollScope = PollScope.GUILD

    @property
    def option_lines(self) -> tuple[str, ...]:
        return tuple(self.options_text.splitlines())


def poll_form(draft: PollDraft | None = None) -> sl.forms.FormSpec:
    """Describe the poll's free-text input through the portable form API."""
    return sl.forms.FormSpec(
        "Create a poll",
        (
            sl.forms.TextField(
                key="question",
                label="Question",
                default="" if draft is None else draft.question,
                maximum=300,
            ),
            sl.forms.TextAreaField(
                key="options",
                label="Options (one per line)",
                default="" if draft is None else draft.options_text,
                placeholder="emoji | label (emoji may be omitted)",
                minimum=3,
                maximum=1000,
            ),
        ),
    )


async def present_poll_form(
    interaction: discord.Interaction[Any],
    publisher: PollPublisher,
    author_account_id: int,
    *,
    allow_network: bool = False,
    draft: PollDraft | None = None,
) -> None:
    """Present the poll's initial form and open its semantic mount on submit."""

    async def submitted(form_interaction: discord.Interaction[Any], values: dict[str, object]) -> None:
        if form_interaction.guild is None:
            await sd.delivery.respond_text(form_interaction, "Polls can only be created in a server.")
            return
        current = draft or PollDraft(question="", options_text="")
        edited = replace(
            current,
            question=cast(str, values["question"]),
            options_text=cast(str, values["options"]),
        )
        try:
            options = await publisher.resolve_options(form_interaction.guild.id, edited.option_lines)
        except InvalidVoteConfigurationError as error:
            await sd.delivery.respond_text(form_interaction, str(error))
            return
        component = PollConfirmationComponent(
            publisher,
            form_interaction.user.id,
            author_account_id,
            edited,
            options,
            allow_network=allow_network,
        )
        await form_interaction.client.mounts.open(
            component.mount(
                source=form_interaction, scheduler=getattr(form_interaction.client, "layout_reactor", None)
            ),
            sd.respond_to(form_interaction, ephemeral=True, wait=True),
            key=SessionKey.user_guild("poll-wizard", form_interaction.user.id, form_interaction.guild.id),
            actor_id=form_interaction.user.id,
        )

    modal = sd.modal.build_form_modal(poll_form(draft), on_submit=submitted)
    await interaction.response.send_modal(modal)


class PollConfirmationComponent(sl.Component):
    """A semantic poll preview and publication workspace."""

    published: bool = sl.state(default=False)
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
        self._mount: sd.Mount | None = None

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
            sl.section(sl.heading(preview), sl.fields(*fields)),
            sl.semantic.Choices(
                key="visibility",
                choices=tuple(
                    sl.semantic.Choice(value.value, label, description)
                    for value, label, description in VISIBILITY_CHOICES
                ),
                selection=sl.controlled((self.draft.visibility.value,), self._visibility_changed),
            ),
            sl.semantic.Choices(
                key="duration",
                choices=tuple(
                    sl.semantic.Choice(str(seconds), label)
                    for label, seconds in (*DURATION_PRESETS, ("Custom", CUSTOM_DURATION))
                ),
                selection=sl.controlled((str(self.draft.duration_seconds),), self._duration_changed),
            ),
        ]
        if self.allow_network:
            nodes.append(
                sl.semantic.Choices(
                    key="scope",
                    choices=tuple(
                        sl.semantic.Choice(value.value, label, description)
                        for value, label, description in SCOPE_CHOICES
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
                sl.forms.FormSpec(
                    "Custom poll duration",
                    (
                        sl.forms.DurationField(
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
        interaction = sd.native(event)
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
        await event.present_form(poll_form(self.draft), key="poll-edit", on_submit=self._edited)

    async def _edited(self, event: sl.SubmitEvent) -> None:
        """Apply the portable poll form back to this mounted wizard."""
        draft = replace(
            self.draft,
            question=cast(str, event.values["question"]),
            options_text=cast(str, event.values["options"]),
        )
        interaction = sd.native(event)
        if interaction.guild is None:
            await event.notice("Polls can only be edited in a server.")
            return
        try:
            options = await self.publisher.resolve_options(interaction.guild.id, draft.option_lines)
        except InvalidVoteConfigurationError as error:
            await event.notice(str(error))
            return
        self.draft = draft
        self.options = options

    async def _cancel(self, event: sl.PressEvent) -> None:
        await event.notice("Poll cancelled.")
        await event.finish()

    def _visibility_label(self) -> str:
        return next(label for value, label, _ in VISIBILITY_CHOICES if value is self.draft.visibility)

    def _scope_label(self) -> str:
        return next(label for value, label, _ in SCOPE_CHOICES if value is self.draft.scope)

    def mount(self, *, source: sd.host.HostSource, scheduler: sd.MountScheduler | None = None) -> sd.Mount:
        self._mount = create_mount(
            self,
            source=source,
            access=sd.Owner(self.owner_id),
            timeout=self._timeout,
            scheduler=scheduler,
            expiry=sd.RenewEphemeral() if scheduler is not None else sd.PauseUpdates(),
        )
        return self._mount
