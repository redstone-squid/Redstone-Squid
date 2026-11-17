"""Component-driven wizard for composing and publishing generic polls."""

import csv
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, cast, override

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.edit_history import history_actions, track_local_edits
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
from squid_i18n import Text, md_tr

_DURATION = re.compile(r"(\d+)\s*(days?|d|hours?|h|minutes?|mins?|m|seconds?|secs?|s)", re.IGNORECASE)
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

DURATION_PRESETS: tuple[tuple[sl.TextLike, int], ...] = (
    (tr(t"bot-voting-poll-wizard-hour = 1 hour"), 3600),
    (tr(t"bot-voting-poll-wizard-hours = 6 hours"), 6 * 3600),
    (tr(t"bot-voting-poll-wizard-hours-2 = 12 hours"), 12 * 3600),
    (tr(t"bot-voting-poll-wizard-hours-3 = 24 hours"), 24 * 3600),
    (tr(t"bot-voting-poll-wizard-days = 3 days"), 3 * 86400),
    (tr(t"bot-voting-poll-wizard-days-2 = 7 days"), 7 * 86400),
)
CUSTOM_DURATION = "custom"
DEFAULT_POLL_CHOICES = "👍 | Yes\n👎 | No"

VISIBILITY_CHOICES: tuple[tuple[VoteVisibility, Text, Text], ...] = (
    (
        VoteVisibility.ANONYMOUS_LIVE,
        tr(t"bot-voting-poll-wizard-live-anonymous = Live Anonymous"),
        tr(t"poll-setup-vote-privacy-notice = Running totals are public; who voted for what is not."),
    ),
    (
        VoteVisibility.VISIBLE_LIVE,
        tr(t"bot-voting-poll-wizard-live-public = Live Public"),
        tr(t"poll-setup-ballot-attribution-notice = Reactions stay on the message, so every ballot is attributable."),
    ),
    (
        VoteVisibility.ANONYMOUS_HIDDEN,
        tr(t"bot-voting-poll-wizard-hidden-until-close = Hidden until Close"),
        tr(t"poll-setup-results-hidden-until-close = No totals at all until the poll closes."),
    ),
)


def parse_poll_duration(value: str) -> int:
    """Parse durations such as `2h15m` or `2 days 3 hours`, in seconds."""
    value = value.strip()
    seconds = 0
    end = 0
    for match in _DURATION.finditer(value):
        if value[end : match.start()].strip():
            break
        seconds += int(match.group(1)) * _DURATION_UNITS[match.group(2)[0].lower()]
        end = match.end()
    if not value or end != len(value):
        raise InvalidVoteConfigurationError(
            md_tr(t"poll-setup-duration-format-help = Use a duration like `30m`, `2h15m`, or `2 days 3 hours`.")
        )
    if not MIN_POLL_DURATION_SECONDS <= seconds <= MAX_POLL_DURATION_SECONDS:
        raise InvalidVoteConfigurationError(
            tr(t"poll-setup-duration-out-of-range = Poll duration must be between 1 minute and 30 days.")
        )
    return seconds


def format_duration(seconds: int) -> sl.TextLike:
    """Render a duration the way the presets are labelled."""
    for label, preset in DURATION_PRESETS:
        if preset == seconds:
            return label
    if seconds % 86400 == 0:
        count = seconds // 86400
        return md_tr(
            t"""
            bot-voting-poll-wizard-day = {{ $count ->
                [one] {count} day
               *[other] {count} days
            }}
            """,
        )
    if seconds % 3600 == 0:
        count = seconds // 3600
        return md_tr(
            t"""
            bot-voting-poll-wizard-hour-2 = {{ $count ->
                [one] {count} hour
               *[other] {count} hours
            }}
            """,
        )
    if seconds % 60:
        return f"{seconds // 60}m {seconds % 60}s"
    count = seconds // 60
    return md_tr(
        t"""
        bot-voting-poll-wizard-minute = {{ $count ->
            [one] {count} minute
           *[other] {count} minutes
        }}
        """,
    )


def parse_option_lines(
    lines: Sequence[str],
    *,
    guild_id: int,
    palette: Sequence[VoteOption],
    emoji_is_usable: Callable[[str], bool] = lambda _emoji: True,
) -> tuple[VoteOption, ...]:
    """Parse two to ten nonblank `emoji | label` or plain-label option lines.

    Plain labels use the palette entry at that line's position, including earlier
    lines with explicit emojis. IDs are one-based strings in input order. Invalid
    counts, empty fields, unusable emojis, or duplicate emojis raise
    `InvalidVoteConfigurationError`.
    """
    cleaned = [line.strip() for line in lines if line.strip()]
    if not 2 <= len(cleaned) <= 10:
        raise InvalidVoteConfigurationError(
            tr(t"poll-setup-option-count-invalid = Enter between 2 and 10 option lines.")
        )
    options: list[VoteOption] = []
    for index, line in enumerate(cleaned):
        if "|" in line:
            emoji, label = (part.strip() for part in line.split("|", 1))
        else:
            if index >= len(palette):
                raise InvalidVoteConfigurationError(
                    tr(
                        t"poll-setup-emoji-palette-too-small = The "
                        t"configured generic emoji palette does not have enough entries for these options."
                    )
                )
            emoji, label = palette[index].emoji, line
        if not emoji or not label:
            raise InvalidVoteConfigurationError(
                tr(t"poll-setup-option-emoji-required = Each option needs a non-empty emoji and label.")
            )
        if not emoji_is_usable(emoji):
            raise InvalidVoteConfigurationError(
                md_tr(
                    t"poll-setup-custom-emoji-unavailable = The custom emoji {emoji} is not accessible to this bot.",
                )
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
        raise InvalidVoteConfigurationError(tr(t"poll-setup-option-emojis-unique = Poll option emojis must be unique."))
    return tuple(options)


SCOPE_CHOICES: tuple[tuple[PollScope, Text, Text], ...] = (
    (
        PollScope.GUILD,
        tr(t"bot-voting-poll-wizard-this-server = This server"),
        tr(t"poll-setup-distribution-channel-only = Card the poll in this channel only."),
    ),
    (
        PollScope.NETWORK,
        tr(t"bot-voting-poll-wizard-every-server = Every server"),
        tr(t"poll-setup-distribution-all-servers = Card the poll in every server's vote channel."),
    ),
)


@dataclass(frozen=True, slots=True)
class PollDraft:
    """The wizard's editable state between the form and publication."""

    question: str
    options_text: str = DEFAULT_POLL_CHOICES
    visibility: VoteVisibility = VoteVisibility.ANONYMOUS_LIVE
    duration_seconds: int = 24 * 3600
    scope: PollScope = PollScope.GUILD

    def __post_init__(self) -> None:
        object.__setattr__(self, "question", self.question.replace("\\n", "\n").strip())

    @classmethod
    def from_answers(cls, answers: sp.WizardAnswers) -> PollDraft:
        """Construct the draft from the completed wizard steps."""
        content = answers["content"]
        settings = answers["settings"]
        return cls(
            question=cast(str, content["question"]),
            options_text=cast(str, content["options"]),
            visibility=cast(VoteVisibility, settings["visibility"]),
            duration_seconds=cast(int, settings["duration"]),
            scope=cast(PollScope, settings.get("scope", PollScope.GUILD)),
        )

    @property
    def option_lines(self) -> tuple[str, ...]:
        return parse_poll_choices(self.options_text)

    def validate(self) -> None:
        """Apply the same content limits to slash commands and the editor."""
        if not self.question.strip() or len(self.question) > 300:
            raise InvalidVoteConfigurationError(
                tr(t"poll-question-length = Enter a question between 1 and 300 characters.")
            )
        if len(self.options_text) > 1000:
            raise InvalidVoteConfigurationError(tr(t"poll-choices-length = Keep the choices within 1000 characters."))
        if not MIN_POLL_DURATION_SECONDS <= self.duration_seconds <= MAX_POLL_DURATION_SECONDS:
            raise InvalidVoteConfigurationError(
                tr(t"poll-setup-duration-out-of-range = Poll duration must be between 1 minute and 30 days.")
            )


def parse_poll_choices(value: str) -> tuple[str, ...]:
    """Accept lines or CSV choices; line-separated choices may contain commas.

    Literal backslash-n sequences become line breaks. For one-line CSV input,
    quote a choice containing a comma, for example `"Red, white", Blue`.
    """
    value = value.replace("\\n", "\n").strip()
    if "\n" in value:
        return tuple(line.strip() for line in value.splitlines() if line.strip())
    try:
        choices = next(csv.reader([value], skipinitialspace=True, strict=True))
    except csv.Error as error:
        raise InvalidVoteConfigurationError(
            tr(t"poll-choices-format = Separate choices with commas or line breaks; quote choices containing commas.")
        ) from error
    if any(not choice.strip() for choice in choices):
        raise InvalidVoteConfigurationError(tr(t"poll-choices-empty = Choices cannot be empty."))
    return tuple(choice.strip() for choice in choices)


def poll_form(draft: PollDraft | None = None) -> sl.forms.FormSpec[sd.Request[Any, Any]]:
    """Describe the poll's free-text input through the portable form API."""
    return sl.forms.FormSpec[sd.Request[Any, Any]](
        tr(t"bot-voting-poll-wizard-create-a-poll = Create a poll"),
        (
            sl.forms.TextAreaField(
                key="question",
                label=tr(t"bot-voting-poll-wizard-question = Question"),
                default="" if draft is None else draft.question,
                maximum=300,
            ),
            sl.forms.TextAreaField(
                key="options",
                label=tr(t"bot-voting-poll-wizard-options-one-per-line = Choices (commas or one per line)"),
                default=DEFAULT_POLL_CHOICES if draft is None else draft.options_text,
                placeholder=tr(t"poll-setup-option-format-help = Red, Blue or 🔴 | Red, 🔵 | Blue"),
                minimum=3,
                maximum=1000,
            ),
        ),
    )


type ResolveOptions = Callable[[tuple[str, ...]], Awaitable[tuple[VoteOption, ...]]]
type PublishPoll = Callable[[PollDraft, tuple[VoteOption, ...]], Awaitable[str]]


def _settings_form(allow_network: bool) -> sl.forms.FormSpec[sd.Request[Any, Any]]:
    fields: list[sl.forms.FormField[object]] = [
        cast(
            sl.forms.FormField[object],
            sl.forms.ChoiceField(
                key="visibility",
                label=tr(t"bot-voting-poll-wizard-visibility = Visibility"),
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
                label=tr(t"bot-voting-poll-wizard-duration = Duration"),
                default=24 * 3600,
                placeholder=tr(t"bot-voting-poll-wizard-m-h-d = 30m, 2h15m, 2 days"),
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
                    label=tr(t"bot-voting-poll-wizard-reach = Reach"),
                    default=PollScope.GUILD,
                    options=tuple(
                        sl.forms.ChoiceOption(value.value, label, value, description)
                        for value, label, description in SCOPE_CHOICES
                    ),
                ),
            )
        )
    return sl.forms.FormSpec[sd.Request[Any, Any]](
        tr(t"bot-voting-poll-wizard-poll-settings = Poll settings"), tuple(fields)
    )


def _poll_steps(
    allow_network: bool, draft: PollDraft | None
) -> tuple[sp.WizardStep[sl.ComponentsV2Target, sd.Request], ...]:
    return (
        sp.WizardStep(
            "content", tr(t"bot-voting-poll-wizard-question-and-options = Question and options"), poll_form(draft)
        ),
        sp.WizardStep(
            "settings",
            tr(t"bot-voting-poll-wizard-visibility-and-duration = Visibility and duration"),
            _settings_form(allow_network),
        ),
    )


def _review(answers: sp.WizardAnswers) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
    draft = PollDraft.from_answers(answers)
    try:
        draft.validate()
        options = "\n".join(f"- {line}" for line in draft.option_lines)
    except InvalidVoteConfigurationError as error:
        return sl.status(error.message, tone=sl.Tone.DANGER)
    fields = [
        sl.field(
            tr(t"bot-voting-poll-wizard-visibility = Visibility"),
            next(label for value, label, _ in VISIBILITY_CHOICES if value is draft.visibility),
        ),
        sl.field(tr(t"bot-voting-poll-wizard-closes-after = Closes after"), format_duration(draft.duration_seconds)),
    ]
    if draft.scope is PollScope.NETWORK:
        fields.append(
            sl.field(
                tr(t"bot-voting-poll-wizard-reaches = Reaches"),
                tr(t"bot-voting-poll-wizard-every-server = Every server"),
            )
        )
    return sl.section(sl.heading(draft.question), sl.paragraph(options), sl.fields(*fields))


class PollScreen(sd.Screen[str]):
    """Compose a poll and complete with its published message URL."""

    policy = sd.ResponseSpec(
        timeout=900,
        expiry=sd.RenewEphemeral(),
        live_updates=True,
        session=sd.SessionSpec("poll-wizard", scope=sd.ScopeKind.USER_GUILD),
    )

    history: sl.runtime.History = sl.runtime.history(limit=20)

    published_url: str | None = sl.state(None)
    cancelled: bool = sl.state(False)
    editing_options: bool = sl.state(False)

    def __init__(
        self,
        resolve_options: ResolveOptions,
        publish: PublishPoll,
        *,
        allow_network: bool = False,
        draft: PollDraft | None = None,
    ) -> None:
        self.option_editor = sp.CollectionEditor(
            tr(t"poll-choice-rows-title = Poll choices"),
            create=sl.forms.FormSpec(
                tr(t"poll-choice-row-title = Poll choice"),
                (
                    sl.forms.TextField(key="label", label=tr(t"poll-choice-row-label = Label"), maximum=100),
                    sl.forms.TextField(
                        key="emoji", label=tr(t"poll-choice-row-emoji = Emoji (optional)"), required=False, maximum=100
                    ),
                ),
            ),
            label=lambda value: f"{value.get('emoji') or ''} {value['label']}",
            minimum=2,
            maximum=25,
        )
        self.option_driver = self.option_editor.build_component()
        self._resolve_options = resolve_options
        self._publish = publish
        initial_state = None
        if draft is not None:
            answers = [
                sp.WizardAnswer(
                    "settings",
                    (("visibility", draft.visibility), ("duration", draft.duration_seconds), ("scope", draft.scope)),
                ),
            ]
            if draft.question:
                answers.insert(
                    0, sp.WizardAnswer("content", (("question", draft.question), ("options", draft.options_text)))
                )
            initial_state = sp.WizardState(
                current=sp.REVIEW_STEP if draft.question else "content",
                answers=tuple(answers),
                reviewing=bool(draft.question),
            )
        wizard = sp.Wizard[sl.ComponentsV2Target, sd.Request](
            tr(t"bot-voting-poll-wizard-create-a-poll = Create a poll"),
            _poll_steps(allow_network, draft),
            key="poll",
            initial_state=initial_state,
            review=sp.WizardReview(
                label=tr(t"bot-voting-poll-wizard-review-poll = Review poll"),
                summarize=_review,
                note=tr(
                    t"poll-publish-final = One vote per person; voting again changes your choice. "
                    t"Publishing is final and cannot be undone here."
                ),
            ),
            # Undo, Redo, and Cancel share the wizard's last row instead of adding rows below it.
            actions=self._wizard_actions,
            on_finish=self._finish,
        )
        self.wizard = wizard.machine
        self.driver = wizard
        track_local_edits(
            self.history,
            wizard,
            tr(t"poll-history-edit = Edit poll"),
            select=lambda state: state.answers,
            restore=lambda state, answers: replace(state, answers=answers),
            enabled=lambda: self.published_url is None and not self.cancelled,
        )

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        if self.published_url is not None:
            published_url = sl.text_link(self.published_url, self.published_url)
            return (
                sl.status(
                    md_tr(t"bot-voting-poll-wizard-poll-published = Poll published: {published_url}"),
                    tone=sl.Tone.SUCCESS,
                ),
            )
        if self.cancelled:
            return (sl.status(tr(t"bot-voting-poll-wizard-poll-cancelled = Poll cancelled.")),)
        if self.editing_options:
            return (
                self.option_driver,
                sl.action_controls(
                    sl.action_control(
                        tr(t"poll-choice-rows-save = Use choices"),
                        self._save_options,
                        key="save-options",
                        available=not self.option_editor.errors(self.option_driver.machine_state),
                    ),
                    sl.action_control(
                        tr(t"poll-choice-rows-cancel = Cancel"), self._cancel_options, key="cancel-options"
                    ),
                    key="option-actions",
                ),
            )
        return (self.driver,)

    def _wizard_actions(self) -> tuple[sl.semantic.ActionControl[sd.Request], ...]:
        return (
            *history_actions(self.history),
            sl.action_control(
                tr(t"poll-choice-rows-edit = Edit choice rows"),
                self._edit_options,
                key="option-rows",
                available=any(answer.step == "content" for answer in self.driver.machine_state.answers),
            ),
            sl.action_control(
                tr(t"bot-voting-poll-wizard-cancel = Cancel"), self._cancel, key="cancel", tone=sl.Tone.DANGER
            ),
        )

    async def _edit_options(self, _event: sd.PressEvent) -> None:
        content = next(answer for answer in self.driver.machine_state.answers if answer.step == "content")
        lines = parse_poll_choices(str(dict(content.values)["options"]))
        rows = []
        for line in lines:
            emoji, separator, label = line.partition("|")
            rows.append({"label": label.strip() if separator else line, "emoji": emoji.strip() if separator else ""})
        self.option_driver.machine_state = self.option_editor.initial_from(rows)
        self.editing_options = True

    async def _cancel_options(self, _event: sd.PressEvent) -> None:
        self.editing_options = False

    async def _save_options(self, _event: sd.PressEvent) -> None:
        if self.option_editor.errors(self.option_driver.machine_state):
            return
        lines = []
        for row in self.option_editor.values(self.option_driver.machine_state):
            label = str(row["label"])
            emoji = str(row.get("emoji") or "").strip()
            lines.append(f"{emoji} | {label}" if emoji else label)
        options = "\n".join(lines)
        answers = tuple(
            replace(answer, values=tuple((key, options if key == "options" else value) for key, value in answer.values))
            if answer.step == "content"
            else answer
            for answer in self.driver.machine_state.answers
        )
        self.driver.machine_state = replace(self.driver.machine_state, answers=answers)
        self.editing_options = False

    async def _finish(
        self,
        event: sp.TransitionEvent[sp.WizardState],
        answers: sp.WizardAnswers,
    ) -> None:
        draft = PollDraft.from_answers(answers)
        try:
            draft.validate()
            options = await self._resolve_options(draft.option_lines)
            self.published_url = await self._publish(draft, options)
        except InvalidVoteConfigurationError as error:
            self.driver.machine_state = replace(event.state, complete=False)
            await event.source.notice(error.message)
            return
        # Widget transitions erase the context type; this driver belongs to a Discord screen.
        self.history.clear()
        self.complete(cast(sd.ActionEvent, event.source), self.published_url)

    async def _cancel(self, event: sd.PressEvent) -> None:
        self.cancelled = True
        self.cancel(event)
