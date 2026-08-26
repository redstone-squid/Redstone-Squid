"""Shared mechanics used by semantic lowering families."""

from collections.abc import Callable, Sequence

from squid_ui.planning.cursors import MaterializedCursorRequest, content_fingerprint
from squid_ui.planning.search import StrategyAxis, choose_strategy
from squid_ui.planning.semantic_adaptation.model import LoweringContext
from squid_ui.primitives.styles import ActionStyle
from squid_ui.runtime.presentation import StrategyState, StrategyUpdate
from squid_ui.semantic import Emphasis, Tone
from squid_ui.text import TextLike, resolve_text

_Context = LoweringContext


def _remember(key: str, adapter_id: str, version: int, strategy: str, context: _Context) -> None:
    """Stage an adapter's sticky choice. Lowering reads the session and writes nothing."""
    context.updates.append(StrategyUpdate(key, StrategyState(key, adapter_id, version, strategy)))


def _select_strategy(axis: StrategyAxis, context: _Context) -> str:
    selected = context.strategies.get(axis.path)
    if selected is None:
        choice = choose_strategy(
            axis.candidates,
            path=axis.path,
            flexibility=axis.flexibility,
            preferred=axis.preferred,
            baseline=axis.baseline,
        )
        context.states_explored += choice.states_explored
        selected = choice.candidate.strategy_id
    elif selected not in {candidate.strategy_id for candidate in axis.candidates}:
        message = f"{axis.path}: assignment selected unavailable strategy {selected!r}"
        raise ValueError(message)
    _remember(
        axis.key,
        axis.adapter_id,
        axis.adapter_version,
        selected,
        context,
    )
    return selected


def _resolve(value: TextLike, context: _Context) -> str:
    return resolve_text(value, context.localization).content


def _page_items[T](
    items: Sequence[T],
    pager_key: str,
    context: _Context,
    *,
    identity: Callable[[T], str],
) -> tuple[tuple[T, ...], int, int]:
    """Window a list of options 25 at a time, following the item the reader was on."""
    per = context.limits.components.select_options
    keys = [identity(item) for item in items]
    anchors: dict[str, int] = {}
    for position, key in enumerate(keys):
        anchors.setdefault(key, position // per)
    request = MaterializedCursorRequest(
        key=pager_key,
        extent=max(1, (len(items) + per - 1) // per),
        fingerprint=content_fingerprint(keys),
        anchors=anchors,
    )
    grant = context.pages.grant(request)
    index = grant.position.offset
    visible = tuple(items[index * per : (index + 1) * per])
    context.pages.record(request, grant.position, anchor=identity(visible[0]) if visible else None)
    return visible, index, grant.extent


def _button_style(tone: Tone, emphasis: Emphasis) -> ActionStyle:
    return {
        Tone.SUCCESS: ActionStyle.SUCCESS,
        Tone.DANGER: ActionStyle.DANGER,
        Tone.INFO: ActionStyle.PRIMARY,
    }.get(tone, ActionStyle.PRIMARY if emphasis is Emphasis.STRONG else ActionStyle.SECONDARY)
