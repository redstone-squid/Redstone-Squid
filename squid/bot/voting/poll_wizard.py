"""Component-driven wizard for composing and publishing generic polls."""

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import cast

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
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

_DURATION = re.compile(r"^(\d+)\s*([mhd])$", re.IGNORECASE)
_DURATION_UNITS = {"m": 60, "h": 3600, "d": 86400}

DURATION_PRESETS: tuple[tuple[sl.TextLike, int], ...] = (
    (L(t"1 hour"), 3600),
    (L(t"6 hours"), 6 * 3600),
    (L(t"12 hours"), 12 * 3600),
    (L(t"24 hours"), 24 * 3600),
    (L(t"3 days"), 3 * 86400),
    (L(t"7 days"), 7 * 86400),
)
CUSTOM_DURATION = "custom"

VISIBILITY_CHOICES: tuple[tuple[VoteVisibility, sl.TextLike, sl.TextLike], ...] = (
    (
        VoteVisibility.ANONYMOUS_LIVE,
        L(t"Live Anonymous"),
        L(t"Running totals are public; who voted for what is not."),
    ),
    (
        VoteVisibility.VISIBLE_LIVE,
        L(t"Live Public"),
        L(t"Reactions stay on the message, so every ballot is attributable."),
    ),
    (
        VoteVisibility.ANONYMOUS_HIDDEN,
        L(t"Hidden until Close"),
        L(t"No totals at all until the poll closes."),
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
    (PollScope.GUILD, L(t"This server"), L(t"Card the poll in this channel only.")),
    (PollScope.NETWORK, L(t"Every server"), L(t"Card the poll in every server's vote channel.")),
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
        L(t"Create a poll"),
        (
            sl.forms.TextField(
                key="question",
                label=L(t"Question"),
                default="" if draft is None else draft.question,
                maximum=300,
            ),
            sl.forms.TextAreaField(
                key="options",
                label=L(t"Options (one per line)"),
                default="" if draft is None else draft.options_text,
                placeholder=L(t"emoji | label (emoji may be omitted)"),
                minimum=3,
                maximum=1000,
            ),
        ),
    )


type ResolveOptions = Callable[[tuple[str, ...]], Awaitable[tuple[VoteOption, ...]]]
type PublishPoll = Callable[[PollDraft, tuple[VoteOption, ...]], Awaitable[str]]


def _settings_form(allow_network: bool) -> sl.forms.FormSpec:
    fields: list[sl.forms.FormField[object]] = [
        cast(
            sl.forms.FormField[object],
            sl.forms.ChoiceField(
                key="visibility",
                label=L(t"Visibility"),
                default=VoteVisibility.ANONYMOUS_LIVE,
                options=tuple(
                    sl.forms.ChoiceOption(value.value, label, value, description)
                    for value, label, description in VISIBILITY_CHOICES
                ),
            ),
        ),
        cast(
            sl.forms.FormField[object],
            sl.forms.DurationField(
                key="duration",
                label=L(t"Duration"),
                default=24 * 3600,
                placeholder=L(t"30m, 12h, 7d"),
                maximum=MAX_POLL_DURATION_SECONDS,
                minimum=MIN_POLL_DURATION_SECONDS,
                parser=parse_poll_duration,
            ),
        ),
    ]
    if allow_network:
        fields.append(
            cast(
                sl.forms.FormField[object],
                sl.forms.ChoiceField(
                    key="scope",
                    label=L(t"Reach"),
                    default=PollScope.GUILD,
                    options=tuple(
                        sl.forms.ChoiceOption(value.value, label, value, description)
                        for value, label, description in SCOPE_CHOICES
                    ),
                ),
            )
        )
    return sl.forms.FormSpec(L(t"Poll settings"), tuple(fields))


def _poll_steps(allow_network: bool) -> tuple[sp.WizardStep[sl.ComponentsV2Target], ...]:
    return (
        sp.WizardStep("content", L(t"Question and options"), poll_form()),
        sp.WizardStep("settings", L(t"Visibility and duration"), _settings_form(allow_network)),
    )


def _draft(answers: sp.WizardAnswers) -> PollDraft:
    content = answers["content"]
    settings = answers["settings"]
    return PollDraft(
        question=cast(str, content["question"]),
        options_text=cast(str, content["options"]),
        visibility=cast(VoteVisibility, settings["visibility"]),
        duration_seconds=cast(int, settings["duration"]),
        scope=cast(PollScope, settings.get("scope", PollScope.GUILD)),
    )


def _review(answers: sp.WizardAnswers) -> sl.LayoutNode[sl.ComponentsV2Target]:
    draft = _draft(answers)
    options = "\n".join(f"- {line}" for line in draft.option_lines)
    fields = [
        sl.field(L(t"Visibility"), next(label for value, label, _ in VISIBILITY_CHOICES if value is draft.visibility)),
        sl.field(L(t"Closes after"), format_duration(draft.duration_seconds)),
    ]
    if draft.scope is PollScope.NETWORK:
        fields.append(sl.field(L(t"Reaches"), L(t"Every server")))
    return sl.section(sl.heading(draft.question), sl.paragraph(options), sl.fields(*fields))


class PollScreen(sd.UserGuildSessionScreen):
    """A poll wizard that ends when published, cancelled, replaced, or timed out."""

    session_name = "poll-wizard"
    timeout = 900
    expiry = sd.RenewEphemeral()
    follow_topics = True

    published_url: str | None = sl.state(None)
    cancelled: bool = sl.state(default=False)

    def __init__(
        self,
        resolve_options: ResolveOptions,
        publish: PublishPoll,
        *,
        allow_network: bool = False,
    ) -> None:
        self._resolve_options = resolve_options
        self._publish = publish
        wizard = sp.Wizard[sl.ComponentsV2Target](
            L(t"Create a poll"),
            _poll_steps(allow_network),
            key="poll",
            review=sp.WizardReview(label=L(t"Review poll"), summarize=_review),
        )
        self.wizard = wizard
        self.driver = wizard.build_component(on_finish=self._finish)

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self.published_url is not None:
            published_url = self.published_url
            return (sl.status(L(t"Poll published: {published_url}"), tone=sl.Tone.SUCCESS),)
        if self.cancelled:
            return (sl.status(L(t"Poll cancelled.")),)
        return (
            self.boundary(self.driver, key="wizard"),
            sl.action_controls(
                sl.action_control(L(t"Cancel"), self._cancel, key="cancel", tone=sl.Tone.DANGER),
                key="poll-actions",
            ),
        )

    async def _finish(
        self,
        event: sp.TransitionEvent[sp.WizardState],
        answers: sp.WizardAnswers,
    ) -> None:
        draft = _draft(answers)
        try:
            options = await self._resolve_options(draft.option_lines)
            self.published_url = await self._publish(draft, options)
        except InvalidVoteConfigurationError as error:
            self.driver.machine_state = replace(event.state, complete=False)
            await event.source.notice(sl.text.Message(error.message, error.message_params))
            return
        await event.source.finish()

    async def _cancel(self, event: sl.PressEvent) -> None:
        self.cancelled = True
        await event.finish()
