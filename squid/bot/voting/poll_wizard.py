"""Component-driven wizard for composing and publishing generic polls."""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

import discord

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot._types import GuildMessageable
from squid.bot.ui import L
from squid.core.i18n import _
from squid.voting.domain import (
    MAX_POLL_DURATION_SECONDS,
    MIN_POLL_DURATION_SECONDS,
    PollScope,
    VoteChoice,
    VoteOption,
    VoteVisibility,
)
from squid.voting.errors import InvalidVoteConfigurationError

if TYPE_CHECKING:
    from squid.bot.voting.publisher import PollPublisher

_DURATION = re.compile(r"^(\d+)\s*([mhd])$", re.IGNORECASE)
_DURATION_UNITS = {"m": 60, "h": 3600, "d": 86400}

DURATION_PRESETS: tuple[tuple[sl.TextLike, int], ...] = (
    (L("1 hour"), 3600),
    (L("6 hours"), 6 * 3600),
    (L("12 hours"), 12 * 3600),
    (L("24 hours"), 24 * 3600),
    (L("3 days"), 3 * 86400),
    (L("7 days"), 7 * 86400),
)
CUSTOM_DURATION = "custom"

VISIBILITY_CHOICES: tuple[tuple[VoteVisibility, sl.TextLike, sl.TextLike], ...] = (
    (
        VoteVisibility.ANONYMOUS_LIVE,
        L("Live Anonymous"),
        L("Running totals are public; who voted for what is not."),
    ),
    (
        VoteVisibility.VISIBLE_LIVE,
        L("Live Public"),
        L("Reactions stay on the message, so every ballot is attributable."),
    ),
    (
        VoteVisibility.ANONYMOUS_HIDDEN,
        L("Hidden until Close"),
        L("No totals at all until the poll closes."),
    ),
)


def parse_poll_duration(value: str) -> int:
    """Parse a compact duration and return seconds within the supported range."""
    match = _DURATION.fullmatch(value.strip())
    if match is None:
        msg = _("Duration must look like `30m`, `12h`, or `7d`.")
        raise InvalidVoteConfigurationError(msg)
    seconds = int(match.group(1)) * _DURATION_UNITS[match.group(2).lower()]
    if not MIN_POLL_DURATION_SECONDS <= seconds <= MAX_POLL_DURATION_SECONDS:
        msg = _("Poll duration must be between 1 minute and 30 days.")
        raise InvalidVoteConfigurationError(msg)
    return seconds


def format_duration(seconds: int) -> sl.TextLike:
    """Render a duration the way the presets are labelled."""
    for label, preset in DURATION_PRESETS:
        if preset == seconds:
            return label
    if seconds % 86400 == 0:
        count = seconds // 86400
        return sl.text.Message("{count} day", {"count": count}, plural="{count} days")
    if seconds % 3600 == 0:
        count = seconds // 3600
        return sl.text.Message("{count} hour", {"count": count}, plural="{count} hours")
    count = seconds // 60
    return sl.text.Message("{count} minute", {"count": count}, plural="{count} minutes")


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
        msg = _("Enter between 2 and 10 option lines.")
        raise InvalidVoteConfigurationError(msg)
    options: list[VoteOption] = []
    for index, line in enumerate(cleaned):
        if "|" in line:
            emoji, label = (part.strip() for part in line.split("|", 1))
        else:
            if index >= len(palette):
                msg = _("The configured generic emoji palette does not have enough entries for these options.")
                raise InvalidVoteConfigurationError(msg)
            emoji, label = palette[index].emoji, line
        if not emoji or not label:
            msg = _("Each option needs a non-empty emoji and label.")
            raise InvalidVoteConfigurationError(msg)
        if not emoji_is_usable(emoji):
            raise InvalidVoteConfigurationError(
                _("The custom emoji {emoji} is not accessible to this bot."),
                message_params={"emoji": emoji},
            )
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
        msg = _("Poll option emojis must be unique.")
        raise InvalidVoteConfigurationError(msg)
    return tuple(options)


SCOPE_CHOICES: tuple[tuple[PollScope, sl.TextLike, sl.TextLike], ...] = (
    (PollScope.GUILD, L("This server"), L("Card the poll in this channel only.")),
    (PollScope.NETWORK, L("Every server"), L("Card the poll in every server's vote channel.")),
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
        L("Create a poll"),
        (
            sl.forms.TextField(
                key="question",
                label=L("Question"),
                default="" if draft is None else draft.question,
                maximum=300,
            ),
            sl.forms.TextAreaField(
                key="options",
                label=L("Options (one per line)"),
                default="" if draft is None else draft.options_text,
                placeholder=L("emoji | label (emoji may be omitted)"),
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
        invocation = await sd.Invocation.of(form_interaction)
        if form_interaction.guild is None:
            await sd.delivery.respond_text(form_interaction, invocation.t(L("Polls can only be created in a server.")))
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
            await sd.delivery.respond_text(form_interaction, invocation.t(L(error.message, **error.message_params)))
            return
        await PollConfirmationComponent(
            publisher,
            author_account_id,
            edited,
            options,
            allow_network=allow_network,
        ).show(form_interaction, wait=True)

    invocation = await sd.Invocation.of(interaction)
    modal = sd.modal.build_form_modal(
        poll_form(draft),
        on_submit=submitted,
        localization=invocation.localization,
    )
    await interaction.response.send_modal(modal)


class PollConfirmationComponent(sd.Screen):
    """A semantic poll preview and publication workspace."""

    session = "poll-wizard"
    scope = sd.ScopeKind.USER_GUILD
    timeout = 900
    expiry = sd.RenewEphemeral()
    follow_topics = True

    published: bool = sl.state(default=False)
    draft: PollDraft = sl.state(persist=False)

    def __init__(
        self,
        publisher: PollPublisher,
        author_account_id: int,
        draft: PollDraft,
        options: tuple[VoteOption, ...],
        *,
        allow_network: bool = False,
    ) -> None:
        self.publisher = publisher
        self.author_account_id = author_account_id
        self.draft = draft
        self.vote_options = options
        self.allow_network = allow_network

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self.published:
            return (sl.status(L("Poll published.")),)
        preview = "\n".join(
            [
                f"## {self.draft.question}",
                *(f"{option.emoji} {option.label}" for option in self.vote_options),
            ]
        )
        fields = [
            sl.field(L("Visibility"), self._visibility_label()),
            sl.field(L("Closes after"), format_duration(self.draft.duration_seconds)),
        ]
        if self.allow_network:
            fields.append(sl.field(L("Reaches"), self._scope_label()))
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = [
            sl.section(sl.heading(preview), sl.fields(*fields)),
            sl.choices(
                *(
                    sl.choice(label, key=value.value, description=description)
                    for value, label, description in VISIBILITY_CHOICES
                ),
                key="visibility",
                selection=sl.controlled((self.draft.visibility.value,), self._visibility_changed),
            ),
            sl.choices(
                *(
                    sl.choice(label, key=str(seconds))
                    for label, seconds in (*DURATION_PRESETS, (L("Custom"), CUSTOM_DURATION))
                ),
                key="duration",
                selection=sl.controlled((str(self.draft.duration_seconds),), self._duration_changed),
            ),
        ]
        if self.allow_network:
            nodes.append(
                sl.choices(
                    *(
                        sl.choice(label, key=value.value, description=description)
                        for value, label, description in SCOPE_CHOICES
                    ),
                    key="scope",
                    selection=sl.controlled((self.draft.scope.value,), self._scope_changed),
                )
            )
        nodes.append(
            sl.action_controls(
                sl.action_control(
                    L("Publish"),
                    self._publish,
                    key="publish",
                    tone=sl.Tone.SUCCESS,
                ),
                sl.action_control(L("Edit"), self._edit, key="edit"),
                sl.action_control(
                    L("Cancel"),
                    self._cancel,
                    key="cancel",
                    tone=sl.Tone.DANGER,
                ),
                key="poll-actions",
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
                    L("Custom poll duration"),
                    (
                        sl.forms.DurationField(
                            key="duration",
                            label=L("Duration"),
                            placeholder=L("30m, 12h, 7d"),
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
            await event.notice(L("Polls can only be published in a server."))
            return
        if self.draft.scope is PollScope.NETWORK and not (
            isinstance(interaction.user, discord.Member) and await self.publisher.may_create_network(interaction.user)
        ):
            await event.notice(L("You may no longer publish a poll to every server."))
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
                options=self.vote_options,
                scope=self.draft.scope,
            )
        except InvalidVoteConfigurationError as error:
            self.published = False
            invocation = await sd.Invocation.of(interaction)
            await event.notice(invocation.t(L(error.message, **error.message_params)))
            return
        await event.notice(L("Published: {url}", url=message.jump_url))
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
            await event.notice(L("Polls can only be edited in a server."))
            return
        try:
            options = await self.publisher.resolve_options(interaction.guild.id, draft.option_lines)
        except InvalidVoteConfigurationError as error:
            invocation = await sd.Invocation.of(interaction)
            await event.notice(invocation.t(L(error.message, **error.message_params)))
            return
        self.draft = draft
        self.vote_options = options

    async def _cancel(self, event: sl.PressEvent) -> None:
        await event.notice(L("Poll cancelled."))
        await event.finish()

    def _visibility_label(self) -> sl.TextLike:
        return next(label for value, label, _ in VISIBILITY_CHOICES if value is self.draft.visibility)

    def _scope_label(self) -> sl.TextLike:
        return next(label for value, label, _ in SCOPE_CHOICES if value is self.draft.scope)
