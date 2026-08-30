"""Component-driven wizard for composing and publishing generic polls."""

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import cast

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.ui import tr
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
    (tr(t"1 hour"), 3600),
    (tr(t"6 hours"), 6 * 3600),
    (tr(t"12 hours"), 12 * 3600),
    (tr(t"24 hours"), 24 * 3600),
    (tr(t"3 days"), 3 * 86400),
    (tr(t"7 days"), 7 * 86400),
)
CUSTOM_DURATION = "custom"

VISIBILITY_CHOICES: tuple[tuple[VoteVisibility, sl.TextLike, sl.TextLike], ...] = (
    (
        VoteVisibility.ANONYMOUS_LIVE,
        tr(t"Live Anonymous"),
        tr(t"Running totals are public; who voted for what is not."),
    ),
    (
        VoteVisibility.VISIBLE_LIVE,
        tr(t"Live Public"),
        tr(t"Reactions stay on the message, so every ballot is attributable."),
    ),
    (
        VoteVisibility.ANONYMOUS_HIDDEN,
        tr(t"Hidden until Close"),
        tr(t"No totals at all until the poll closes."),
    ),
)


def parse_poll_duration(value: str) -> int:
    """Parse a compact duration and return seconds within the supported range."""
    match = _DURATION.fullmatch(value.strip())
    if match is None:
        raise InvalidVoteConfigurationError(tr(t"Duration must look like `30m`, `12h`, or `7d`."))
    seconds = int(match.group(1)) * _DURATION_UNITS[match.group(2).lower()]
    if not MIN_POLL_DURATION_SECONDS <= seconds <= MAX_POLL_DURATION_SECONDS:
        raise InvalidVoteConfigurationError(tr(t"Poll duration must be between 1 minute and 30 days."))
    return seconds


def format_duration(seconds: int) -> sl.TextLike:
    """Render a duration the way the presets are labelled."""
    for label, preset in DURATION_PRESETS:
        if preset == seconds:
            return label
    if seconds % 86400 == 0:
        count = seconds // 86400
        return tr(t"{count} day", plural=t"{count} days")
    if seconds % 3600 == 0:
        count = seconds // 3600
        return tr(t"{count} hour", plural=t"{count} hours")
    count = seconds // 60
    return tr(t"{count} minute", plural=t"{count} minutes")


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
        raise InvalidVoteConfigurationError(tr(t"Enter between 2 and 10 option lines."))
    options: list[VoteOption] = []
    for index, line in enumerate(cleaned):
        if "|" in line:
            emoji, label = (part.strip() for part in line.split("|", 1))
        else:
            if index >= len(palette):
                raise InvalidVoteConfigurationError(
                    tr(t"The configured generic emoji palette does not have enough entries for these options.")
                )
            emoji, label = palette[index].emoji, line
        if not emoji or not label:
            raise InvalidVoteConfigurationError(tr(t"Each option needs a non-empty emoji and label."))
        if not emoji_is_usable(emoji):
            raise InvalidVoteConfigurationError(tr(t"The custom emoji {emoji} is not accessible to this bot."))
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
        raise InvalidVoteConfigurationError(tr(t"Poll option emojis must be unique."))
    return tuple(options)


SCOPE_CHOICES: tuple[tuple[PollScope, sl.TextLike, sl.TextLike], ...] = (
    (PollScope.GUILD, tr(t"This server"), tr(t"Card the poll in this channel only.")),
    (PollScope.NETWORK, tr(t"Every server"), tr(t"Card the poll in every server's vote channel.")),
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
        tr(t"Create a poll"),
        (
            sl.forms.TextField(
                key="question",
                label=tr(t"Question"),
                default="" if draft is None else draft.question,
                maximum=300,
            ),
            sl.forms.TextAreaField(
                key="options",
                label=tr(t"Options (one per line)"),
                default="" if draft is None else draft.options_text,
                placeholder=tr(t"emoji | label (emoji may be omitted)"),
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
                label=tr(t"Visibility"),
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
                label=tr(t"Duration"),
                default=24 * 3600,
                placeholder=tr(t"30m, 12h, 7d"),
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
                    label=tr(t"Reach"),
                    default=PollScope.GUILD,
                    options=tuple(
                        sl.forms.ChoiceOption(value.value, label, value, description)
                        for value, label, description in SCOPE_CHOICES
                    ),
                ),
            )
        )
    return sl.forms.FormSpec(tr(t"Poll settings"), tuple(fields))


def _poll_steps(allow_network: bool) -> tuple[sp.WizardStep[sl.ComponentsV2Target], ...]:
    return (
        sp.WizardStep("content", tr(t"Question and options"), poll_form()),
        sp.WizardStep("settings", tr(t"Visibility and duration"), _settings_form(allow_network)),
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
        sl.field(tr(t"Visibility"), next(label for value, label, _ in VISIBILITY_CHOICES if value is draft.visibility)),
        sl.field(tr(t"Closes after"), format_duration(draft.duration_seconds)),
    ]
    if draft.scope is PollScope.NETWORK:
        fields.append(sl.field(tr(t"Reaches"), tr(t"Every server")))
    return sl.section(sl.heading(draft.question), sl.paragraph(options), sl.fields(*fields))


class PollScreen(sd.Screen):
    """A poll wizard that ends when published, cancelled, replaced, or timed out."""

    session = sd.SessionSpec("poll-wizard", scope=sd.ScopeKind.USER_GUILD)
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
            tr(t"Create a poll"),
            _poll_steps(allow_network),
            key="poll",
            review=sp.WizardReview(label=tr(t"Review poll"), summarize=_review),
        )
        self.wizard = wizard
        self.driver = wizard.build_component(on_finish=self._finish)

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self.published_url is not None:
            published_url = self.published_url
            return (sl.status(tr(t"Poll published: {published_url}"), tone=sl.Tone.SUCCESS),)
        if self.cancelled:
            return (sl.status(tr(t"Poll cancelled.")),)
        return (
            self.boundary(self.driver, key="wizard"),
            sl.action_controls(
                sl.action_control(tr(t"Cancel"), self._cancel, key="cancel", tone=sl.Tone.DANGER),
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
            await event.source.notice(error.message)
            return
        await event.source.finish()

    async def _cancel(self, event: sl.PressEvent) -> None:
        self.cancelled = True
        await event.finish()
