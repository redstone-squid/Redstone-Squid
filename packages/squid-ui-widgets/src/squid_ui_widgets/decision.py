"""One-way decisions and confirmation sugar over the shared machine shells."""

from collections.abc import Awaitable, Callable, Collection, Iterable
from dataclasses import dataclass

from squid_ui.document import DocumentLike
from squid_ui.factories import action_controls, stack, status
from squid_ui.semantic import ControlDisplay, Emphasis, Tone
from squid_ui.target_types import RenderTarget
from squid_ui.text import TextLike
from squid_ui_widgets._content import ContentLike, normalize_content, require_key
from squid_ui_widgets.drivers import ComponentDriver, FormValues, MachineControls, TransitionEvent


@dataclass(frozen=True, slots=True)
class DecisionOption:
    key: str
    label: TextLike
    tone: Tone = Tone.NEUTRAL
    emphasis: Emphasis = Emphasis.NORMAL


@dataclass(frozen=True, slots=True)
class DecisionState:
    decided: str | None = None


type DecisionHandler = Callable[[TransitionEvent[DecisionState], str], Awaitable[None]]


class Decision[RenderTargetT: RenderTarget = RenderTarget]:
    """A pending prompt that becomes immutable after one valid choice."""

    def __init__(
        self,
        prompt: ContentLike[RenderTargetT],
        options: Iterable[DecisionOption],
        *,
        key: str = "decision",
    ) -> None:
        self.key = require_key(key, name="Decision.key")
        self.prompt = normalize_content(prompt, name="Decision.prompt")
        self.options = tuple(options)
        keys = [option.key for option in self.options]
        if not self.options:
            message = "Decision needs at least one option"
            raise ValueError(message)
        if any(not key for key in keys) or len(set(keys)) != len(keys):
            message = f"Decision option keys must be non-empty and unique: {keys!r}"
            raise ValueError(message)

    @property
    def initial_state(self) -> DecisionState:
        return DecisionState()

    def finish_actions(self) -> frozenset[str]:
        return frozenset(f"choose:{option.key}" for option in self.options)

    def build_component(
        self,
        *,
        on_decide: DecisionHandler | None = None,
        finish_on: Collection[str] = (),
    ) -> ComponentDriver[DecisionState, RenderTargetT]:
        handlers = {}
        if on_decide is not None:
            handlers = {f"choose:{option.key}": self._handler(on_decide, option.key) for option in self.options}
        requested = frozenset(item if item.startswith("choose:") else f"choose:{item}" for item in finish_on)
        finish_actions = self.finish_actions() & requested
        return ComponentDriver(self, handlers=handlers, finish_actions=finish_actions)

    @staticmethod
    def _handler(handler: DecisionHandler, key: str):
        async def decided(event: TransitionEvent[DecisionState]) -> None:
            await handler(event, key)

        return decided

    def transition(
        self,
        state: DecisionState,
        action: str,
        *,
        values: tuple[str, ...] = (),
        submitted: FormValues | None = None,
    ) -> DecisionState:
        del values, submitted
        if state.decided is not None or not action.startswith("choose:"):
            return state
        key = action.removeprefix("choose:")
        return DecisionState(key) if key in {option.key for option in self.options} else state

    def render(
        self, state: DecisionState, controls: MachineControls[DecisionState, RenderTargetT]
    ) -> DocumentLike[RenderTargetT]:
        options = self._options(controls)
        selected = next((option for option in options if option.key == state.decided), None)
        return stack(
            *controls.content(self.prompt, prefix=f"{self.key}.prompt"),
            action_controls(
                *(
                    controls.action_control(
                        option.label,
                        f"choose:{option.key}",
                        key=f"{self.key}.{option.key}",
                        tone=option.tone,
                        emphasis=option.emphasis,
                        available=state.decided is None,
                    )
                    for option in options
                ),
                key=f"{self.key}.options",
                display=ControlDisplay.INDIVIDUAL,
            ),
            status(controls.chrome.decided(selected.label), tone=selected.tone) if selected is not None else None,
        )

    def _options(self, controls: MachineControls[DecisionState, RenderTargetT]) -> tuple[DecisionOption, ...]:
        del controls
        return self.options


class _Confirmation[RenderTargetT: RenderTarget = RenderTarget](Decision[RenderTargetT]):
    def __init__(
        self,
        prompt: ContentLike[RenderTargetT],
        *,
        key: str,
        confirm_label: TextLike | None,
        cancel_label: TextLike | None,
        tone: Tone,
    ) -> None:
        super().__init__(
            prompt,
            (
                DecisionOption("confirm", confirm_label or "Confirm", tone, Emphasis.STRONG),
                DecisionOption("cancel", cancel_label or "Cancel"),
            ),
            key=key,
        )
        self.confirm_label = confirm_label
        self.cancel_label = cancel_label

    def _options(self, controls: MachineControls[DecisionState, RenderTargetT]) -> tuple[DecisionOption, ...]:
        return (
            DecisionOption(
                "confirm",
                self.confirm_label or controls.chrome.confirm,
                self.options[0].tone,
                self.options[0].emphasis,
            ),
            DecisionOption("cancel", self.cancel_label or controls.chrome.cancel),
        )


def confirm[RenderTargetT: RenderTarget](
    prompt: ContentLike[RenderTargetT],
    *,
    key: str = "confirm",
    on_confirm: Callable[[TransitionEvent[DecisionState]], Awaitable[None]],
    on_cancel: Callable[[TransitionEvent[DecisionState]], Awaitable[None]] | None = None,
    confirm_label: TextLike | None = None,
    cancel_label: TextLike | None = None,
    tone: Tone = Tone.DANGER,
) -> ComponentDriver[DecisionState, RenderTargetT]:
    """Build a ready two-option decision shell."""
    machine = _Confirmation(
        prompt,
        key=key,
        confirm_label=confirm_label,
        cancel_label=cancel_label,
        tone=tone,
    )
    handlers = {"choose:confirm": on_confirm}
    if on_cancel is not None:
        handlers["choose:cancel"] = on_cancel
    return ComponentDriver(machine, handlers=handlers)
