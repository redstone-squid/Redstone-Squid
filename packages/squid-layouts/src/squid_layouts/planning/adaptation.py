"""Lower semantic author intent into finite target-shaped strategy candidates."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from squid_layouts.actions import ActionBinding, ActionEvent, EntitySelectionEvent, PressEvent, SelectionEvent
from squid_layouts.assets import Asset
from squid_layouts.chrome import Chrome
from squid_layouts.entities import EntityRef
from squid_layouts.errors import LayoutInvariantError, UnsolvableLayoutError
from squid_layouts.forms import FormBinding
from squid_layouts.palette import DEFAULT_PALETTE, AccentDefault, Palette
from squid_layouts.planning.breaking import BreakItem, balanced_breaks
from squid_layouts.planning.cursors import CursorCoordinator, MaterializedCursorRequest, content_fingerprint
from squid_layouts.planning.identity import stable_fingerprint
from squid_layouts.planning.limits import COMPONENTS, V2Limits
from squid_layouts.planning.measure import measure_nodes, split_text_node, text_total
from squid_layouts.planning.search import DEFAULT_SEARCH_BUDGET, StrategyAxis, StrategyCandidate, choose_strategy
from squid_layouts.primitives.constraints import (
    Alt,
    Condense,
    Drop,
    Never,
    Overflow,
    Paginate,
    Spill,
    Truncate,
    alts,
)
from squid_layouts.primitives.nodes import (
    ActionGroup as PrimitiveActionGroup,
)
from squid_layouts.primitives.nodes import (
    Break,
    Budget,
    Button,
    Card,
    CardField,
    CardFooter,
    CardMedia,
    EntitySelect,
    Fidelity,
    Footer,
    FormButton,
    Gallery,
    Lines,
    LinkButton,
    Node,
    Option,
    Panel,
    RoutedButton,
    RoutedSelect,
    Row,
    SelectMenu,
    Text,
    Thumbnail,
    Time,
    Variant,
    Variants,
    ZonedTime,
    card_text,
)
from squid_layouts.primitives.nodes import (
    Code as PrimitiveCode,
)
from squid_layouts.primitives.nodes import (
    File as PrimitiveFile,
)
from squid_layouts.primitives.nodes import (
    Heading as PrimitiveHeading,
)
from squid_layouts.primitives.nodes import (
    Section as PrimitiveSection,
)
from squid_layouts.primitives.styles import ActionStyle
from squid_layouts.runtime.presentation import (
    PresentationSession,
    SessionUpdate,
    StrategyState,
    StrategyUpdate,
)
from squid_layouts.scene.model import PlanEvent, PlanSeverity, ScenePager
from squid_layouts.semantic import (
    Action,
    ActionDisplay,
    ActionGroup,
    Actions,
    Article,
    Aside,
    BestEffort,
    Budgeted,
    Choice,
    ChoiceEvent,
    Choices,
    Cluster,
    Code,
    Controlled,
    Details,
    Download,
    Emphasis,
    Entities,
    EntityEvent,
    FallbackContent,
    Field,
    Fields,
    Figure,
    Flexibility,
    FormTrigger,
    Group,
    Heading,
    ItemDisplay,
    Items,
    KeepWithNext,
    LayoutNode,
    Link,
    List,
    Managed,
    Measure,
    Media,
    NavigateEvent,
    Navigation,
    NavigationDisplay,
    Note,
    OpenEvent,
    OptionalContent,
    Paged,
    Paragraph,
    Progress,
    Quote,
    RoutedAction,
    RoutedChoices,
    Section,
    Spilled,
    Stack,
    Status,
    Table,
    TableDisplay,
    Themed,
    Timestamp,
    Toggle,
    ToggleEvent,
    Tone,
    Truncated,
    Unbreakable,
    ZonedTimestamp,
)
from squid_layouts.sources import Position
from squid_layouts.text import Localization, TextLike, resolve_text

ACTIONS_ADAPTER_ID = "discord.actions"
ACTIONS_ADAPTER_VERSION = 1

ITEMS_ADAPTER_ID = "discord.items"
ITEMS_ADAPTER_VERSION = 1
MEDIA_ADAPTER_ID = "discord.media"
MEDIA_ADAPTER_VERSION = 1
NAVIGATION_ADAPTER_ID = "discord.navigation"
NAVIGATION_ADAPTER_VERSION = 1
TABLE_ADAPTER_ID = "discord.table"
TABLE_ADAPTER_VERSION = 1


@dataclass(frozen=True, slots=True)
class SemanticLowering:
    nodes: tuple[Node, ...]
    assets: tuple[Asset, ...] = ()
    events: tuple[PlanEvent, ...] = ()
    pagers: tuple[ScenePager, ...] = ()
    updates: tuple[SessionUpdate, ...] = ()
    states_explored: int = 0
    search_fallback: bool = False


@dataclass(slots=True)
class _Context:
    limits: V2Limits
    chrome: Chrome
    localization: Localization
    palette: Palette
    session: PresentationSession
    pages: CursorCoordinator
    capabilities: frozenset[str]
    assets: list[Asset]
    events: list[PlanEvent]
    updates: list[SessionUpdate]
    strategies: Mapping[str, str]
    fallbacks: Mapping[str, int]
    search_budget: int = DEFAULT_SEARCH_BUDGET
    states_explored: int = 0
    search_fallback: bool = False


@dataclass(frozen=True, slots=True)
class FallbackAxis:
    """One `FallbackContent` occurrence and how many branches it can offer."""

    path: str
    branches: int
    branch_paths: tuple[str, ...]
    """One stable path per branch; decisions inside a branch are named under it."""


@dataclass(frozen=True, slots=True)
class SemanticDecisions:
    """Every semantic choice reachable under one set of selected fallback branches."""

    strategies: tuple[StrategyAxis, ...] = ()
    fallbacks: tuple[FallbackAxis, ...] = ()


def nominate_decisions(
    nodes: Sequence[LayoutNode],
    *,
    limits: V2Limits,
    session: PresentationSession,
    fallbacks: Mapping[str, int] | None = None,
) -> SemanticDecisions:
    """Collect the semantic decisions reachable through the selected fallback branches.

    Decisions hidden behind an unselected branch are not returned, so a planner state cannot
    spend search on axes the reader will never see.
    """
    axes: list[StrategyAxis] = []
    occurrences: list[FallbackAxis] = []
    selected_rungs = {} if fallbacks is None else fallbacks

    def walk_children(children: Sequence[LayoutNode], path: str) -> None:
        for index, child in enumerate(children):
            walk(child, f"{path}.{index}")

    def walk(node: LayoutNode, path: str) -> None:
        match node:
            case (
                Truncated(node=child)
                | Spilled(node=child)
                | OptionalContent(node=child)
                | BestEffort(node=child)
                | Budgeted(node=child)
                | Unbreakable(node=child)
                | KeepWithNext(node=child)
                | Paged(node=child)
            ):
                walk(child, path)
            case FallbackContent(primary=primary, alternates=alternates):
                branches = (primary, *alternates)
                rung = _fallback_rung(path, len(branches), selected_rungs)
                occurrences.append(FallbackAxis(path, len(branches), _branch_paths(path, len(branches))))
                walk(branches[rung], _branch_paths(path, len(branches))[rung])
            case Actions():
                axes.append(_action_axis(node, path, limits, session))
            case Table():
                axes.append(_table_axis(node, path, session))
            case Media():
                axes.append(_media_axis(node, path, session))
            case Navigation():
                axes.append(_navigation_axis(node, path, limits, session))
            case Items(items=items):
                axes.append(_items_axis(node, path, limits, session))
                opened, fixed = _item_state(node, session)
                if opened is None and not fixed and items:
                    opened = items[0].key
                if opened is not None:
                    item = next((item for item in items if item.key == opened), None)
                    if item is not None:
                        walk_children(item.children, f"{path}.{item.key}")
            case Group(children=children) | Stack(children=children) | Cluster(children=children):
                walk_children(children, path)
            case (
                Section(children=children)
                | Article(children=children)
                | Aside(children=children)
                | Themed(children=children)
                | Panel(children=children)
            ):
                walk_children(children, path)
            case Details(children=children, open=ownership):
                match ownership:
                    case Controlled(value=open_):
                        pass
                    case Managed(initial=initial):
                        open_ = session.disclosure(node.key, initial=initial).open
                if open_:
                    walk_children(children, path)
            case _:
                return

    for index, node in enumerate(nodes):
        walk(node, f"$.{index}")
    paths = tuple(axis.path for axis in axes)
    if len(set(paths)) != len(paths):
        message = "semantic strategy paths must be unique"
        raise LayoutInvariantError(message)
    return SemanticDecisions(tuple(axes), tuple(occurrences))


def _branch_paths(path: str, branches: int) -> tuple[str, ...]:
    """Stable per-branch paths, so a decision inside a branch keeps its identity."""
    return (f"{path}.primary", *(f"{path}.alternate.{index}" for index in range(branches - 1)))


def _fallback_rung(path: str, branches: int, selected: Mapping[str, int]) -> int:
    rung = selected.get(path, 0)
    if not 0 <= rung < branches:
        message = f"{path}: planner selected unavailable fallback branch {rung}"
        raise ValueError(message)
    return rung


def lower_semantics(
    nodes: Sequence[LayoutNode],
    *,
    limits: V2Limits,
    chrome: Chrome,
    localization: Localization,
    session: PresentationSession,
    palette: Palette = DEFAULT_PALETTE,
    pages: CursorCoordinator | None = None,
    capabilities: frozenset[str] = frozenset(),
    search_budget: int = DEFAULT_SEARCH_BUDGET,
    strategies: Mapping[str, str] | None = None,
    fallbacks: Mapping[str, int] | None = None,
) -> SemanticLowering:
    """Lower one semantic decision set into the primitives `measure()` will price."""
    broker = pages if pages is not None else CursorCoordinator(session, chrome)
    context = _Context(
        limits,
        chrome,
        localization,
        palette,
        session,
        broker,
        capabilities,
        [],
        [],
        [],
        {} if strategies is None else strategies,
        {} if fallbacks is None else fallbacks,
        search_budget,
    )
    lowered: list[Node] = []
    for index, node in enumerate(nodes):
        lowered.extend(_node(node, f"$.{index}", context))
    if _cards(context):
        lowered = _settle(_fold(lowered, context), context)
    return SemanticLowering(
        tuple(lowered),
        tuple(context.assets),
        tuple(context.events),
        context.pages.pagers,
        tuple(context.updates),
        context.states_explored,
        context.search_fallback,
    )


def _node(node: LayoutNode, path: str, context: _Context) -> list[Node]:
    match node:
        case Truncated(node=child, keep=keep):
            return [_with_overflow(item, Truncate(keep)) for item in _node(child, path, context)]
        case Spilled(node=child):
            return [_with_overflow(item, Spill()) for item in _node(child, path, context)]
        case OptionalContent(node=child):
            return [_with_overflow(item, Drop()) for item in _node(child, path, context)]
        case BestEffort(node=child):
            policy: Overflow = Spill() if isinstance(child, List | Fields) else Truncate()
            return [_with_best_effort(_with_overflow(item, policy)) for item in _node(child, path, context)]
        case Budgeted(node=child, minimum=minimum, preferred=preferred, stretch=stretch):
            if isinstance(child, Paged):
                return _paged_region(
                    child,
                    path,
                    context,
                    minimum=minimum,
                    preferred=preferred,
                    stretch=stretch,
                )
            return [
                Budget(
                    tuple(_node(child, path, context)),
                    minimum,
                    preferred,
                    stretch,
                    best_effort=isinstance(child, BestEffort),
                )
            ]
        case Paged():
            return _paged_region(node, path, context, minimum=0, preferred=node.chars, stretch=0)
        case Unbreakable(node=child):
            return [Break(tuple(_node(child, path, context)), unbreakable=True)]
        case KeepWithNext(node=child):
            return [Break(tuple(_node(child, path, context)), keep_with_next=True)]
        case FallbackContent(primary=primary, alternates=alternates):
            # Only the selected branch is lowered. An unselected one must leave no trace:
            # no pagers, assets, events, bindings, or staged session writes.
            branches = (primary, *alternates)
            rung = _fallback_rung(path, len(branches), context.fallbacks)
            return _node(branches[rung], _branch_paths(path, len(branches))[rung], context)
        case Actions():
            return _actions(node, path, context)
        case Themed(children=children, palette=palette):
            previous = context.palette
            context.palette = palette
            try:
                return _children(children, path, context)
            finally:
                context.palette = previous
        case Group(children=children) | Stack(children=children) | Cluster(children=children):
            return _children(children, path, context)
        case (
            Section(children=children, heading=heading, accent=accent, thumbnail=thumbnail)
            | Article(children=children, heading=heading, accent=accent, thumbnail=thumbnail)
        ):
            resolved_accent = context.palette.brand if accent is AccentDefault.INHERIT else accent
            if _cards(context):
                # One semantic region, one card: its heading is the embed title, its accent is
                # the embed colour, and its lead image is the thumbnail. Nothing has to be
                # guessed, because the semantic node already said which was which.
                return [
                    _region(
                        Card(
                            title=None if heading is None else _resolve(heading, context),
                            thumbnail=None if not thumbnail else CardMedia(thumbnail),
                            accent=resolved_accent,
                        ),
                        _children(children, path, context),
                        context,
                    )
                ]
            contents: list[Node] = []
            if heading is not None:
                title = PrimitiveHeading(_resolve(heading, context), overflow=Never())
                # The lead image sits beside the title and nothing else: picking "the body"
                # out of an arbitrary children tuple would be a guess.
                contents.append(
                    PrimitiveSection(texts=(title,), accessory=Thumbnail(thumbnail)) if thumbnail else title
                )
            elif thumbnail:
                contents.append(Gallery((thumbnail,)))
            contents.extend(_children(children, path, context))
            return [Panel(tuple(contents), accent=resolved_accent)]
        case Aside(children=children, tone=tone):
            accent = context.palette.tone(tone)
            if _cards(context):
                return [_region(Card(accent=accent), _children(children, path, context), context)]
            return [Panel(tuple(_children(children, path, context)), accent=accent)]
        case Heading(content=content, level=level):
            return [PrimitiveHeading(_resolve(content, context), level=level, overflow=Never())]
        case Paragraph(content=content):
            return [Text(_resolve(content, context), overflow=Never())]
        case Note(content=content):
            return [Footer(_resolve(content, context), overflow=Never())]
        case List(items=items, key=key, ordered=ordered, page_size=page_size):
            marker = (lambda index: f"{index + 1}.") if ordered else (lambda _index: "•")
            lines = tuple(f"{marker(index)} {_resolve(item.content, context)}" for index, item in enumerate(items))
            return [Lines(lines, overflow=Paginate(key=key, per=page_size))]
        case Fields(fields=fields):
            if "layout.embed_fields" in context.capabilities:
                entries = _card_fields(fields, context)
                per_card = getattr(context.limits, "embed_fields", 25)
                # More fields than one embed holds continue into the next card. Lossless:
                # every field is still shown, in order, on an adjacent embed.
                return [
                    _Fragment(fields=tuple(entries[start : start + per_card]))
                    for start in range(0, len(entries), per_card)
                ]
            return [Lines(tuple(_field_entry(field, context) for field in fields), overflow=Condense())]
        case Quote(content=content, attribution=attribution):
            value = "> " + _resolve(content, context).replace("\n", "\n> ")
            if attribution is not None:
                value += f"\n— {_resolve(attribution, context)}"
            return [Text(value, overflow=Never())]
        case Code(content=content, language=language):
            return [PrimitiveCode(content, language, overflow=Never())]
        case Figure(media=media, caption=caption):
            if _cards(context):
                # The description rides along even where Discord will not show it: it is the
                # author's alternative text, and a scene that dropped it could not restore it.
                return [
                    Card(
                        image=CardMedia(media.url, media.description),
                        footer=None if caption is None else CardFooter(_resolve(caption, context)),
                    )
                ]
            children: list[Node] = [Gallery((media.url,))]
            if caption is not None:
                children.append(Footer(_resolve(caption, context)))
            return children
        case Media():
            return _media(node, path, context)
        case Details():
            return _details(node, path, context)
        case Toggle():
            return _toggle(node, context)
        case Download(label=label, asset=asset, description=description, emphasis=emphasis):
            context.assets.append(asset)
            resolved_label = _resolve(context.chrome.download if label is None else label, context)
            if emphasis is Emphasis.STRONG:
                resolved_label = f"**{resolved_label}**"
            elif emphasis is Emphasis.SUBTLE:
                resolved_label = f"-# {resolved_label}"
            text = resolved_label
            if description is not None:
                text += f"\n{_resolve(description, context)}"
            if _cards(context):
                # No file *component* exists outside Components V2. The asset still uploads and
                # the label and description still show; what is lost is the dedicated
                # affordance, which the report says out loud rather than leaving to be noticed.
                context.events.append(
                    PlanEvent(
                        code="download.attachment_only",
                        path=path,
                        message=f"{path}: a classic message shows {asset.name!r} as an attachment, not a file component",
                        severity=PlanSeverity.DEGRADATION,
                    )
                )
                return [Text(text, overflow=Never())]
            return [Text(text, overflow=Never()), PrimitiveFile(asset.key, asset.name, asset.media_type)]
        case Status(content=content, tone=tone):
            prefix = {
                Tone.INFO: "\N{INFORMATION SOURCE}\N{VARIATION SELECTOR-16} ",
                Tone.SUCCESS: "✅ ",
                Tone.WARNING: "⚠️ ",
                Tone.DANGER: "❌ ",
            }.get(tone, "")
            return [Text(prefix + _resolve(content, context), overflow=Never())]
        case Progress(value=value, maximum=maximum, label=label):
            ratio = 0.0 if maximum <= 0 else max(0.0, min(1.0, value / maximum))
            filled = round(ratio * 10)
            prefix = f"{_resolve(label, context)}: " if label is not None else ""
            return [Text(f"{prefix}{'█' * filled}{'░' * (10 - filled)} {ratio:.0%}", overflow=Never())]
        case Measure(value=value, label=label, unit=unit):
            suffix = f" {unit}" if unit else ""
            return [Text(f"**{_resolve(label, context)}:** {value}{suffix}", overflow=Never())]
        case Timestamp(instant=instant, style=style, label=label):
            prefix = f"**{_resolve(label, context)}:** " if label is not None else None
            return [Time(instant, style.value, prefix)]
        case ZonedTimestamp(value=value, label=label):
            prefix = f"**{_resolve(label, context)}:** " if label is not None else None
            return [ZonedTime(value, prefix)]
        case FormTrigger():
            return _form(node, context)
        case Choices():
            return _choices(node, path, context)
        case Entities():
            return _entities(node, path, context)
        case RoutedChoices():
            return _routed_choices(node, path, context)
        case Items():
            return _items(node, path, context)
        case Navigation():
            return _navigation(node, path, context)
        case Table():
            return _table(node, path, context)
        case Panel(children=children, accent=accent):
            # The exact primitive, not a semantic region: it stays a Container and the classic
            # dialect refuses it by name. Quietly turning an author's `Panel` into an embed
            # would be reinterpreting a shape they chose for its own sake.
            return [Panel(tuple(_children(children, path, context)), accent)]
        case _:
            return [_primitive(node, context)]


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


def _strategy_axis(
    *,
    path: str,
    key: str,
    adapter_id: str,
    adapter_version: int,
    flexibility: Flexibility,
    preferred: str,
    available: tuple[str, ...],
    order: tuple[str, ...],
    session: PresentationSession,
    active_pagers: frozenset[str] = frozenset(),
) -> StrategyAxis:
    baseline = session.strategy(key, adapter_id, adapter_version)
    if baseline not in available:
        baseline = None
    reference = baseline or preferred
    positions = {strategy: index for index, strategy in enumerate(order)}
    candidates = tuple(
        StrategyCandidate(
            strategy,
            active_pagers=int(strategy in active_pagers),
            transition_distance=abs(positions[strategy] - positions.get(reference, positions[strategy])),
        )
        for strategy in available
    )
    return StrategyAxis(
        path,
        key,
        adapter_id,
        adapter_version,
        flexibility,
        preferred,
        candidates,
        baseline,
    )


def _resolve(value: TextLike, context: _Context) -> str:
    return resolve_text(value, context.localization).content


@dataclass(frozen=True, slots=True)
class _Fragment(Card):
    """A card holding loose prose or fields rather than an authored region.

    Fragments fold into their neighbours; an `Article`, `Aside`, or `Figure` produces a plain
    `Card` and never does. Only this layer can tell the two apart — after lowering, both are
    just cards, and merging two authored regions into one embed would change the author's
    grouping rather than express it.
    """


def _fold(nodes: Sequence[Node], context: _Context) -> list[Node]:
    """Fold consecutive fragments into as few legal cards as possible.

    Loose prose has to live somewhere, and a classic message's only home for prose beside an
    explicit `Content` is an embed description. Consecutive prose therefore becomes one
    implicit card and the first suitable heading becomes its title — later headings stay in
    the description as formatted text, because an embed has one title.

    A fragment that would overflow a card's own shape opens a continuation card. Continuations
    are exact: everything the author wrote is still shown, in the same order.
    """
    limits = context.limits
    folded: list[Node] = []
    open_card: _Fragment | None = None

    def flush() -> None:
        nonlocal open_card
        if open_card is not None:
            folded.append(open_card)
            open_card = None

    for node in nodes:
        fragment = _as_fragment(node, open_card)
        if fragment is None:
            flush()
            folded.append(node)
            continue
        merged = None if open_card is None else _merge(open_card, fragment, limits)
        if merged is None:
            flush()
            open_card = fragment
        else:
            open_card = merged
    flush()
    return folded


def _as_fragment(node: Node, open_card: _Fragment | None) -> _Fragment | None:
    """This node as a foldable card fragment, or None if it stands on its own."""
    if isinstance(node, _Fragment):
        return node
    if isinstance(node, Footer):
        # A trailing note is what the footer slot is for. One anywhere else stays subtle
        # description text, because an embed has exactly one footer.
        if open_card is not None and open_card.footer is None:
            return _Fragment(footer=CardFooter(node.content))
        return _Fragment(children=(node,))
    if isinstance(node, PrimitiveHeading):
        # "Suitable" means it can actually be a title: it has to come before any body text,
        # or the description would read as though it began mid-sentence.
        leading = open_card is None or (open_card.title is None and not open_card.children)
        if leading:
            return _Fragment(title=Text(node.content, overflow=node.overflow))
        return _Fragment(children=(node,))
    if isinstance(node, Text | PrimitiveCode | Lines | Time | ZonedTime):
        return _Fragment(children=(node,))
    return None


def _merge(first: _Fragment, second: _Fragment, limits: V2Limits) -> _Fragment | None:
    """Fold `second` into `first`, or None when the result would not be one legal embed."""
    if len(first.fields) + len(second.fields) > getattr(limits, "embed_fields", 25):
        return None
    for slot in ("title", "url", "footer", "author", "image", "thumbnail", "timestamp"):
        if getattr(first, slot) is not None and getattr(second, slot) is not None:
            return None
    if first.accent is not None and second.accent is not None and first.accent != second.accent:
        return None
    return _Fragment(
        children=(*first.children, *second.children),
        title=first.title if first.title is not None else second.title,
        url=first.url if first.url is not None else second.url,
        fields=(*first.fields, *second.fields),
        footer=first.footer if first.footer is not None else second.footer,
        author=first.author if first.author is not None else second.author,
        accent=first.accent if first.accent is not None else second.accent,
        image=first.image if first.image is not None else second.image,
        thumbnail=first.thumbnail if first.thumbnail is not None else second.thumbnail,
        timestamp=first.timestamp if first.timestamp is not None else second.timestamp,
    )


def _settle(nodes: Sequence[Node], context: _Context) -> list[Node]:
    """Close every still-open fragment. Folding leaves them open so a parent can absorb them."""
    return [_close(node, context) if isinstance(node, _Fragment) else node for node in nodes]


def _close(card: _Fragment, context: _Context) -> Node:
    """Settle a folded card, offering the reformatted alternative where one exists."""
    plain = Card(
        children=card.children,
        title=card.title,
        url=card.url,
        fields=card.fields,
        footer=card.footer,
        author=card.author,
        accent=card.accent,
        image=card.image,
        thumbnail=card.thumbnail,
        timestamp=card.timestamp,
    )
    if not card.fields:
        return plain
    # Real embed fields are exact. Spelling the same values as description lines keeps every
    # one of them but reformats the block, so the solver is told which is which rather than
    # inferring loss from a rung number.
    lines = Lines(
        tuple(f"**{_slot_text(field.name)}:** {_slot_text(field.value)}" for field in card.fields),
        overflow=Condense(),
    )
    reformatted = replace(plain, fields=(), children=(*plain.children, lines))
    return Variants((Variant((plain,)), Variant((reformatted,), fidelity=Fidelity.REFORMATTED)))


def _slot_text(value: object) -> str:
    return card_text(value).content  # type: ignore[arg-type]


def _region(card: Card, body: Sequence[Node], context: _Context) -> Node:
    """One authored region as one card, absorbing whatever its body folded into.

    A region's body is already folded, so the common case is a single fragment that merges
    straight in. Anything the region cannot hold — a second image, a 26th field — stays a
    sibling card, which is a continuation rather than a loss.
    """
    fragment = _Fragment(
        title=card.title,
        url=card.url,
        footer=card.footer,
        author=card.author,
        accent=card.accent,
        image=card.image,
        thumbnail=card.thumbnail,
        timestamp=card.timestamp,
    )
    folded = _settle(_fold([fragment, *body], context), context)
    if len(folded) == 1:
        return folded[0]
    # A region that needs continuation cards is still one region: keeping them together stops
    # root pagination cutting between a heading and the rest of what it introduced.
    return Break(tuple(folded), keep_with_next=True)


def _individual_fits(controls: int, limits: V2Limits) -> bool:
    """Whether this many controls could be drawn one per button, in this target's units."""
    rows = (controls + limits.row_buttons - 1) // limits.row_buttons
    return limits.fits_controls(controls, rows)


def _cards(context: _Context) -> bool:
    """Whether this target draws regions as embeds rather than as container components."""
    return "layout.embed" in context.capabilities


def _card_fields(fields: Sequence[Field], context: _Context) -> list[CardField]:
    """Real embed fields, keeping the name/value split the semantic node already made.

    This is the whole reason lowering has to know the target. `_field_entry` flattens a field
    into one line of text, and a target that decided embed structure downstream of lowering
    would have nothing left to decide with.
    """
    entries: list[CardField] = []
    for field in fields:
        value = _resolve(field.value, context)
        # A field's own ladder is its overflow policy, so the solver steps it down its
        # author-written rungs before anything is trimmed mid-string.
        rungs = [rung for fallback in field.fallbacks if len(rung := _resolve(fallback, context)) <= len(value)]
        policy: Overflow = alts(*rungs) if rungs else Never()
        entries.append(CardField(name=_resolve(field.label, context), value=Text(value, overflow=policy)))
    return entries


def _primitive(node: Node, context: _Context) -> Node:
    """Resolve deferred author text on exact primitive nodes."""
    match node:
        case (
            Text(content=content)
            | PrimitiveHeading(content=content)
            | Footer(content=content)
            | PrimitiveCode(content=content)
        ):
            return replace(node, content=_resolve(content, context))
        case Lines(lines=lines, overflow=overflow):
            resolved_lines = tuple(line if isinstance(line, Alt) else _resolve(line, context) for line in lines)
            if isinstance(overflow, Paginate) and overflow.footer is not None:
                footer = overflow.footer
                localized = lambda page, pages: _resolve(footer(page, pages), context)
                overflow = replace(overflow, footer=localized)
            return replace(node, lines=resolved_lines, overflow=overflow)
        case Button(label=label) | LinkButton(label=label) | RoutedButton(label=label):
            return replace(node, label=_resolve(label, context))
        case Row(items=items) | PrimitiveActionGroup(items=items):
            return replace(node, items=tuple(_primitive(item, context) for item in items))
        case (
            SelectMenu(options=options, placeholder=placeholder)
            | RoutedSelect(options=options, placeholder=placeholder)
        ):
            return replace(
                node,
                options=tuple(
                    replace(
                        option,
                        label=_resolve(option.label, context),
                        description=_resolve(option.description, context) if option.description is not None else None,
                    )
                    for option in options
                ),
                placeholder=_resolve(placeholder, context) if placeholder is not None else None,
            )
        case EntitySelect(placeholder=placeholder):
            return replace(node, placeholder=_resolve(placeholder, context) if placeholder is not None else None)
        case Thumbnail(description=description):
            return replace(node, description=_resolve(description, context) if description is not None else None)
        case PrimitiveSection(texts=texts, accessory=accessory):
            return replace(
                node,
                texts=tuple(_primitive(text, context) for text in texts),
                accessory=_primitive(accessory, context),
            )
        case Budget(children=children):
            return replace(node, children=tuple(_primitive(child, context) for child in children))
        case Break(children=children):
            return replace(node, children=tuple(_primitive(child, context) for child in children))
        case _:
            return node


def _field_entry(field: Field, context: _Context) -> str | Alt:
    """One `Fields` line, carrying the field's own degradation ladder when it has one.

    Rungs come from caller data assembled by formatting and escaping, so one that came out
    empty or longer than what precedes it is skipped rather than rejected — direct `Alt`
    construction stays strict.
    """
    label = _resolve(field.label, context)
    primary = f"**{label}:** {_resolve(field.value, context)}"
    kept: list[str] = []
    ceiling = len(primary)
    for fallback in field.fallbacks:
        rung = f"**{label}:** {_resolve(fallback, context)}"
        if len(rung) <= ceiling:
            kept.append(rung)
            ceiling = len(rung)
    return Alt(primary, tuple(kept)) if kept else primary


def _children(children: Sequence[LayoutNode], path: str, context: _Context) -> list[Node]:
    lowered: list[Node] = []
    for index, child in enumerate(children):
        lowered.extend(_node(child, f"{path}.{index}", context))
    return _fold(lowered, context) if _cards(context) else lowered


@dataclass(frozen=True, slots=True)
class _RegionItem:
    nodes: tuple[Node, ...]
    keep_with_next: bool = False
    unbreakable: bool = False


def _region_items(nodes: Sequence[Node], *, keep_heading: bool) -> list[_RegionItem]:
    items: list[_RegionItem] = []
    for node in nodes:
        if isinstance(node, Break):
            items.append(_RegionItem(node.children, node.keep_with_next, node.unbreakable))
        else:
            items.append(_RegionItem((node,)))
    if keep_heading and len(items) > 1 and isinstance(items[0].nodes[0], PrimitiveHeading):
        items[0] = replace(items[0], keep_with_next=True)
    return items


def _split_oversized_region_items(
    items: Sequence[_RegionItem],
    *,
    chars: int,
    min_fill: int,
    widows: int,
    limits: V2Limits,
    path: str,
) -> list[_RegionItem]:
    result: list[_RegionItem] = []
    for item in items:
        cost = measure_nodes(item.nodes, limits=limits)
        if text_total(cost) <= chars and cost.get(COMPONENTS) <= limits.total_components:
            result.append(item)
            continue
        fragments = (
            None
            if item.unbreakable or len(item.nodes) != 1
            else split_text_node(item.nodes[0], chars, min_fill=min_fill, widows=widows)
        )
        if fragments is None or len(fragments) <= 1:
            message = (
                f"{path}: unbreakable region child {type(item.nodes[0]).__name__} needs "
                f"{text_total(cost)} characters and {cost.get(COMPONENTS)} components; "
                f"page limit is {chars} characters"
            )
            raise UnsolvableLayoutError(message)
        result.extend(
            _RegionItem((fragment,), keep_with_next=item.keep_with_next and index == len(fragments) - 1)
            for index, fragment in enumerate(fragments)
        )
    return result


def _break_region(
    items: Sequence[_RegionItem],
    *,
    chars: int,
    min_fill: int,
    widows: int,
    limits: V2Limits,
    path: str,
) -> list[tuple[_RegionItem, ...]]:
    if not items:
        return [()]
    costs = [measure_nodes(item.nodes, limits=limits) for item in items]
    try:
        cuts = balanced_breaks(
            [
                BreakItem(
                    text_total(cost),
                    cost.get(COMPONENTS),
                    break_after=not item.keep_with_next,
                )
                for item, cost in zip(items, costs, strict=True)
            ],
            max_chars=chars,
            max_components=limits.total_components,
            min_fill=min_fill,
            widows=widows,
            ideal_total=sum(text_total(cost) for cost in costs),
        )
    except ValueError as error:
        message = f"{path}: region has no feasible break set within its {chars}-character page budget"
        raise UnsolvableLayoutError(message) from error
    pages: list[tuple[_RegionItem, ...]] = []
    start = 0
    for end in cuts:
        pages.append(tuple(items[start:end]))
        start = end
    return pages


def _paged_region(
    node: Paged,
    path: str,
    context: _Context,
    *,
    minimum: int,
    preferred: int,
    stretch: int,
) -> list[Node]:
    lowered = _node(node.node, path, context)
    shell: Panel | None = lowered[0] if len(lowered) == 1 and isinstance(lowered[0], Panel) else None
    children = shell.children if shell is not None else tuple(lowered)
    items = _region_items(children, keep_heading=shell is not None)
    items = _split_oversized_region_items(
        items,
        chars=node.chars,
        min_fill=node.min_fill,
        widows=node.widows,
        limits=context.limits,
        path=path,
    )
    pages = _break_region(
        items,
        chars=node.chars,
        min_fill=node.min_fill,
        widows=node.widows,
        limits=context.limits,
        path=path,
    )
    request = MaterializedCursorRequest(
        key=node.key,
        extent=len(pages),
        fingerprint=stable_fingerprint(children),
        initial=node.initial,
    )
    grant = context.pages.grant(request)
    context.pages.record(request, grant.position)
    selected = [primitive for item in pages[grant.position.offset] for primitive in item.nodes]
    budgeted = Budget(
        tuple(selected),
        minimum,
        preferred,
        stretch,
        best_effort=isinstance(node.node, BestEffort),
    )
    controls = context.pages.controls(node.key, grant.position, grant.extent)
    if controls and node.footer is not None:
        controls[0] = Footer(_resolve(node.footer(grant.position.offset + 1, grant.extent), context), overflow=Never())
    if grant.extent > 1:
        context.events.append(
            PlanEvent(
                code="pagination.region",
                path=path,
                message=f"Region {node.key!r} uses {grant.extent} balanced pages",
                severity=PlanSeverity.ADAPTATION,
                after={"pages": grant.extent},
            )
        )
    if shell is not None:
        return [replace(shell, children=(budgeted, *controls))]
    return [budgeted, *controls]


def _form(node: FormTrigger, context: _Context) -> list[Node]:
    if not {"forms.modal", "forms.inline"} & context.capabilities:
        message = "target does not support forms"
        raise LayoutInvariantError(message)
    maximum = context.limits.modal_components if "forms.modal" in context.capabilities else None
    spec = node.spec.adapt(context.capabilities, maximum_fields=maximum)

    async def present(event: PressEvent) -> None:
        await event.present_form(spec, key=node.key, on_submit=node.on_submit, policy=node.policy)

    return [
        PrimitiveActionGroup(
            (
                FormButton(
                    _resolve(node.label, context),
                    present,
                    node.key,
                    style=_button_style(node.tone, node.emphasis),
                    policy=node.policy,
                    # Guarding the press that opens the modal, not the submission: a stateful
                    # guard checked twice would deny the reader's own filled-in form.
                    guard=node.guard,
                    # The adapted spec, not `node.spec`: it is what the reader will actually
                    # be shown, and so what a late submission must be parsed against.
                    form=FormBinding(node.key, spec, node.on_submit, node.policy),
                ),
            )
        )
    ]


def _with_overflow(node: Node, overflow: Overflow) -> Node:
    if isinstance(node, Text | PrimitiveHeading | Footer | PrimitiveCode | Lines):
        return replace(node, overflow=overflow)
    if isinstance(node, Panel):
        return replace(node, children=tuple(_with_overflow(child, overflow) for child in node.children))
    if isinstance(node, Budget | Break):
        return replace(node, children=tuple(_with_overflow(child, overflow) for child in node.children))
    return node


def _with_best_effort(node: Node) -> Node:
    if isinstance(node, Budget):
        return replace(
            node,
            children=tuple(_with_best_effort(child) for child in node.children),
            best_effort=True,
        )
    if isinstance(node, Panel | Break):
        return replace(node, children=tuple(_with_best_effort(child) for child in node.children))
    return node


def _choices(node: Choices, path: str, context: _Context) -> list[Node]:
    available = tuple(choice for choice in node.choices if choice.available)
    match node.selection:
        case Controlled(value=value):
            previous = tuple(value)
        case Managed(initial=initial):
            previous = context.session.selection(node.key, initial=tuple(initial)).selected

    async def commit(event: ActionEvent, selected: tuple[str, ...]) -> None:
        match node.selection:
            case Controlled(on_change=on_change):
                await on_change(
                    ChoiceEvent(
                        event.actor,
                        event.responder,
                        event.locale,
                        event.context,
                        selected,
                        tuple(key for key in selected if key not in previous),
                        tuple(key for key in previous if key not in selected),
                    )
                )
            case Managed():
                await event.acknowledge()
                context.session.select(node.key, selected)
                event.invalidate()

    if node.maximum == 1 and 2 <= len(available) <= 5:
        buttons: list[Button] = []
        for choice in available:

            async def choose(event: PressEvent, key: str = choice.key) -> None:
                await commit(event, (key,))

            buttons.append(
                Button(
                    _resolve(choice.label, context),
                    choose,
                    f"{node.key}.{choice.key}",
                    style=ActionStyle.PRIMARY if choice.key in previous else ActionStyle.SECONDARY,
                )
            )
        return [PrimitiveActionGroup(tuple(buttons))]
    page_key = f"{node.key}.choices"
    if len(available) > context.limits.select_options and node.maximum != 1:
        message = (
            f"{path}: Choices has {len(available)} options and selects up to {node.maximum}; "
            "cross-page multi-selection is ambiguous, so group the choices or use Items"
        )
        raise LayoutInvariantError(message)
    visible, page, pages = _page_items(available, page_key, context, identity=lambda choice: choice.key)

    async def choose_values(event: SelectionEvent) -> None:
        await commit(event, tuple(event.values))

    options = tuple(
        Option(
            _resolve(choice.label, context),
            choice.key,
            _resolve(choice.description, context) if choice.description is not None else None,
            choice.key in previous,
        )
        for choice in visible
    )
    result: list[Node] = [
        SelectMenu(
            options,
            choose_values,
            node.key,
            min_values=node.minimum,
            max_values=min(node.maximum, len(options)),
        )
    ]
    result.extend(context.pages.controls(page_key, Position(offset=page), pages))
    return result


def _entity_key(ref: EntityRef) -> str:
    return f"{ref.kind.value}:{ref.id}"


def _entities(node: Entities, path: str, context: _Context) -> list[Node]:
    match node.selection:
        case Controlled(value=value):
            previous = tuple(value)
        case Managed(initial=initial):
            initial_keys = tuple(_entity_key(value) for value in initial)
            stored = context.session.selection(node.key, initial=initial_keys).selected
            by_key = {_entity_key(choice.ref): choice.ref for choice in node.choices}
            by_key.update({_entity_key(value): value for value in initial})
            previous = tuple(by_key[key] for key in stored if key in by_key)

    async def commit(event: ActionEvent, selected: tuple[EntityRef, ...]) -> None:
        match node.selection:
            case Controlled(on_change=on_change):
                await on_change(
                    EntityEvent(
                        event.actor,
                        event.responder,
                        event.locale,
                        event.context,
                        selected,
                        tuple(value for value in selected if value not in previous),
                        tuple(value for value in previous if value not in selected),
                    )
                )
            case Managed():
                await event.acknowledge()
                context.session.select(node.key, tuple(_entity_key(value) for value in selected))
                event.invalidate()

    if "actions.discord.entity" in context.capabilities:

        async def select_entities(event: EntitySelectionEvent) -> None:
            await commit(event, event.values)

        return [
            EntitySelect(
                node.entity_type,
                select_entities,
                node.key,
                placeholder=_resolve(node.placeholder, context) if node.placeholder is not None else None,
                default_values=previous,
                channel_types=node.channel_types,
                min_values=node.minimum,
                max_values=node.maximum,
            )
        ]
    if not node.choices:
        message = f"{path}: Entities requires actions.discord.entity or enumerated fallback choices"
        raise LayoutInvariantError(message)

    available = tuple(choice for choice in node.choices if choice.available)
    by_key = {_entity_key(choice.ref): choice.ref for choice in available}

    async def choose_fallback(event: ChoiceEvent) -> None:
        await commit(event, tuple(by_key[key] for key in event.selected if key in by_key))

    fallback = Choices(
        key=node.key,
        choices=tuple(
            Choice(_entity_key(choice.ref), choice.label, choice.description, choice.available) for choice in node.choices
        ),
        selection=Controlled(tuple(_entity_key(value) for value in previous), choose_fallback),
        minimum=node.minimum,
        maximum=node.maximum,
        flexibility=node.flexibility,
    )
    return _choices(fallback, path, context)


def _routed_choices(node: RoutedChoices, path: str, context: _Context) -> list[Node]:
    """Lower an explicitly stateless picker without inventing mount-owned pagination."""
    available = tuple(choice for choice in node.choices if choice.available)
    if not available:
        message = f"{path}: RoutedChoices needs at least one available choice"
        raise LayoutInvariantError(message)
    return [
        RoutedSelect(
            options=tuple(
                Option(
                    _resolve(choice.label, context),
                    choice.key,
                    _resolve(choice.description, context) if choice.description is not None else None,
                )
                for choice in available
            ),
            route_id=node.route_id,
            placeholder=_resolve(node.placeholder, context) if node.placeholder is not None else None,
            min_values=node.minimum,
            max_values=min(node.maximum, len(available)),
            disabled=not node.available,
        )
    ]


def _item_state(node: Items, session: PresentationSession) -> tuple[str | None, bool]:
    # An entry the author named but no longer supplies means the list, not a crash.
    keys = {item.key for item in node.items}
    match node.opened:
        case Controlled(value=value):
            return (value if value in keys else None), True
        case Managed(initial=initial):
            seed = () if initial is None else (initial,)
            remembered = session.selection(node.key, initial=seed).selected
            opened = remembered[0] if remembered and remembered[0] in keys else None
            return opened, node.key in session.selections or initial is not None


def _items_axis(
    node: Items,
    path: str,
    limits: V2Limits,
    session: PresentationSession,
) -> StrategyAxis:
    opened, fixed = _item_state(node, session)
    if fixed:
        available = ("opened",) if opened is not None else ("overview",)
    elif node.items:
        available = ("overview", "opened")
    else:
        available = ("overview",)
    preferred = (
        "opened"
        if opened is not None or (not fixed and node.display is ItemDisplay.OPENED and node.items)
        else "overview"
    )
    axis = _strategy_axis(
        path=path,
        key=node.key,
        adapter_id=ITEMS_ADAPTER_ID,
        adapter_version=ITEMS_ADAPTER_VERSION,
        flexibility=node.flexibility,
        preferred=preferred,
        available=available,
        order=("overview", "opened"),
        session=session,
        active_pagers=frozenset({"overview"}) if len(node.items) > limits.select_options else frozenset(),
    )
    if opened is None and node.display is not ItemDisplay.OPENED:
        axis = replace(axis, baseline=None)
    return axis


def _items(node: Items, path: str, context: _Context) -> list[Node]:
    opened, _fixed = _item_state(node, context.session)
    strategy = _select_strategy(_items_axis(node, path, context.limits, context.session), context)
    if strategy == "opened" and opened is None and node.items:
        opened = node.items[0].key

    async def open_(event: ActionEvent, entry: str | None) -> None:
        match node.opened:
            case Controlled(on_change=on_change):
                await on_change(OpenEvent(event.actor, event.responder, event.locale, event.context, opened=entry))
            case Managed():
                await event.acknowledge()
                context.session.select(node.key, () if entry is None else (entry,))
                event.invalidate()

    if opened is not None:
        item = next(item for item in node.items if item.key == opened)

        async def back(event: PressEvent) -> None:
            await open_(event, None)

        return [
            PrimitiveHeading(_resolve(item.label, context), level=3, overflow=Never()),
            *_children(item.children, f"{path}.{item.key}", context),
            Row((Button(context.chrome.back, back, f"{node.key}.back"),)),
        ]

    async def focus(event: SelectionEvent) -> None:
        await open_(event, event.values[0] if event.values else None)

    page_key = f"{node.key}.items"
    visible, page, pages = _page_items(node.items, page_key, context, identity=lambda item: item.key)
    summaries = tuple(
        f"**{_resolve(item.label, context)}**"
        + (f" — {_resolve(item.summary, context)}" if item.summary is not None else "")
        for item in visible
    )
    result: list[Node] = [
        Lines(summaries, overflow=Never()),
        SelectMenu(
            tuple(Option(_resolve(item.label, context), item.key) for item in visible),
            focus,
            f"{node.key}.focus",
            placeholder="Choose an item",
        ),
    ]
    result.extend(context.pages.controls(page_key, Position(offset=page), pages))
    return result


def _navigation_axis(
    node: Navigation,
    path: str,
    limits: V2Limits,
    session: PresentationSession,
) -> StrategyAxis:
    available = tuple(destination for destination in node.destinations if destination.available)
    strategies = ["individual"]
    if not _individual_fits(len(available), limits):
        strategies.remove("individual")
    if available:
        strategies.append("grouped")
    preferred = {
        NavigationDisplay.INDIVIDUAL: "individual",
        NavigationDisplay.GROUPED: "grouped",
        NavigationDisplay.AUTO: "individual" if len(available) <= 5 else "grouped",
    }[node.display]
    if preferred not in strategies:
        preferred = strategies[-1]
    return _strategy_axis(
        path=path,
        key=node.key,
        adapter_id=NAVIGATION_ADAPTER_ID,
        adapter_version=NAVIGATION_ADAPTER_VERSION,
        flexibility=node.flexibility,
        preferred=preferred,
        available=tuple(strategies),
        order=("individual", "grouped"),
        session=session,
        active_pagers=frozenset({"grouped"}) if len(available) > limits.select_options else frozenset(),
    )


def _navigation(node: Navigation, path: str, context: _Context) -> list[Node]:
    available = tuple(destination for destination in node.destinations if destination.available)
    strategy = _select_strategy(_navigation_axis(node, path, context.limits, context.session), context)
    grouped = strategy == "grouped"

    match node.current:
        case Controlled(value=value):
            current = value
        case Managed(initial=initial):
            # A remembered destination that has since gone unavailable is the engine's own
            # stale data, so drop it. An author's value is theirs to be wrong about.
            keys = {destination.key for destination in available}
            seed = () if initial is None else (initial,)
            remembered = context.session.selection(node.key, initial=seed).selected
            current = remembered[0] if remembered and remembered[0] in keys else None
    if current is None and available:
        current = available[0].key

    async def navigate(event: ActionEvent, destination: str) -> None:
        match node.current:
            case Controlled(on_change=on_change):
                await on_change(NavigateEvent(event.actor, event.responder, event.locale, event.context, destination))
            case Managed():
                await event.acknowledge()
                context.session.select(node.key, (destination,))
                event.invalidate()

    if grouped:
        page_key = f"{node.key}.destinations"
        visible, page, pages = _page_items(available, page_key, context, identity=lambda item: item.key)

        async def select_destination(event: SelectionEvent) -> None:
            if event.values:
                await navigate(event, event.values[0])

        result: list[Node] = [
            SelectMenu(
                tuple(
                    Option(
                        _resolve(destination.label, context),
                        destination.key,
                        default=destination.key == current,
                    )
                    for destination in visible
                ),
                select_destination,
                node.key,
            )
        ]
        result.extend(context.pages.controls(page_key, Position(offset=page), pages))
        return result
    buttons: list[Button] = []
    for destination in available:

        async def go(event: PressEvent, key: str = destination.key) -> None:
            await navigate(event, key)

        buttons.append(
            Button(
                _resolve(destination.label, context),
                go,
                f"{node.key}.{destination.key}",
                style=ActionStyle.PRIMARY if destination.key == current else ActionStyle.SECONDARY,
            )
        )
    return [PrimitiveActionGroup(tuple(buttons))]


def _details(node: Details, path: str, context: _Context) -> list[Node]:
    match node.open:
        case Controlled(value=value):
            open_ = value
        case Managed(initial=initial):
            open_ = context.session.disclosure(node.key, initial=initial).open

    async def toggle(event: PressEvent) -> None:
        match node.open:
            case Controlled(on_change=on_change):
                await on_change(OpenEvent(event.actor, event.responder, event.locale, event.context, opened=not open_))
            case Managed(initial=seed):
                await event.acknowledge()
                context.session.disclose(node.key, not context.session.disclosure(node.key, initial=seed).open)
                event.invalidate()

    result: list[Node] = [Row((Button(_resolve(node.summary, context), toggle, f"{node.key}.toggle"),))]
    if open_:
        result.extend(_children(node.children, path, context))
    return result


def _toggle(node: Toggle, context: _Context) -> list[Node]:
    match node.on:
        case Controlled(value=value):
            on = value
        case Managed(initial=initial):
            on = context.session.toggle(node.key, initial=initial).on

    async def flip(event: PressEvent) -> None:
        match node.on:
            case Controlled(on_change=on_change):
                await on_change(ToggleEvent(event.actor, event.responder, event.locale, event.context, not on))
            case Managed(initial=initial):
                await event.acknowledge()
                current = context.session.toggle(node.key, initial=initial).on
                context.session.set_toggle(node.key, on=not current)
                event.invalidate()

    state_label = node.on_label if on else node.off_label
    if state_label is None:
        state_label = context.chrome.on if on else context.chrome.off
    label = f"{_resolve(node.label, context)}: {_resolve(state_label, context)}"
    button = Button(
        label,
        flip,
        node.key,
        style=_button_style(node.tone, Emphasis.NORMAL),
        disabled=not node.available,
    )
    return [Row((button,))]


def _table_axis(node: Table, path: str, session: PresentationSession) -> StrategyAxis:
    preferred = "tabular" if node.display is not TableDisplay.RECORDS and len(node.columns) <= 4 else "records"
    return _strategy_axis(
        path=path,
        key=node.key,
        adapter_id=TABLE_ADAPTER_ID,
        adapter_version=TABLE_ADAPTER_VERSION,
        flexibility=node.flexibility,
        preferred=preferred,
        available=("tabular", "records"),
        order=("tabular", "records"),
        session=session,
    )


def _table(node: Table, path: str, context: _Context) -> list[Node]:
    strategy = _select_strategy(_table_axis(node, path, context.session), context)
    if strategy == "tabular":
        headings = [_resolve(column.heading, context) for column in node.columns]
        widths = [
            max([len(heading), *(len(_resolve(row.cells[index], context)) for row in node.rows)])
            for index, heading in enumerate(headings)
        ]
        lines = [" | ".join(heading.ljust(widths[index]) for index, heading in enumerate(headings))]
        lines.append("-+-".join("-" * width for width in widths))
        lines.extend(
            " | ".join(_resolve(cell, context).ljust(widths[index]) for index, cell in enumerate(row.cells))
            for row in node.rows
        )
        return [PrimitiveCode("\n".join(lines), overflow=Never())]
    records = tuple(
        "\n".join(
            f"**{_resolve(column.heading, context)}:** {_resolve(cell, context)}"
            for column, cell in zip(node.columns, row.cells, strict=True)
        )
        for row in node.rows
    )
    return [Lines(records, join="\n\n", overflow=Paginate(key=node.key))]


def _media_axis(node: Media, path: str, session: PresentationSession) -> StrategyAxis:
    preferred = "featured" if node.display.value == "featured" else "collection"
    return _strategy_axis(
        path=path,
        key=node.key,
        adapter_id=MEDIA_ADAPTER_ID,
        adapter_version=MEDIA_ADAPTER_VERSION,
        flexibility=node.flexibility,
        preferred=preferred,
        available=("collection", "featured") if node.items else ("collection",),
        order=("collection", "featured"),
        session=session,
    )


def _media(node: Media, path: str, context: _Context) -> list[Node]:
    strategy = _select_strategy(_media_axis(node, path, context.session), context)
    if not node.items:
        return []
    if strategy == "featured":
        first = node.items[0]
        result: list[Node] = [Gallery((first.url,))]
        if first.description is not None:
            result.append(Footer(_resolve(first.description, context), overflow=Never()))
        return result
    return [
        Gallery(tuple(item.url for item in node.items[start : start + context.limits.gallery_items]))
        for start in range(0, len(node.items), context.limits.gallery_items)
    ]


def _actions(node: Actions, path: str, context: _Context) -> list[Node]:
    strategy = _select_strategy(_action_axis(node, path, context.limits, context.session), context)
    groups: list[tuple[str, tuple[Action, ...], str | None]] = []
    # Links and routed controls carry no binding, so they can never be folded into a select
    # menu the way a group of session actions can: they stay individual buttons.
    direct: list[Action | LinkButton | RoutedButton] = []
    implicit: list[Action] = []

    def flush_implicit() -> None:
        if implicit:
            groups.append(("default", tuple(implicit), None))
            implicit.clear()

    for item in node.items:
        if isinstance(item, ActionGroup):
            flush_implicit()
            group_actions: list[Action] = []
            for action in item.actions:
                if isinstance(action, Action):
                    group_actions.append(action)
                else:
                    direct.append(_unbound_button(action, context))
            groups.append((item.key, tuple(group_actions), _resolve(item.label, context) if item.label else None))
        elif isinstance(item, Action):
            implicit.append(item)
        else:
            flush_implicit()
            direct.append(_unbound_button(item, context))
    flush_implicit()

    result: list[Node] = []
    if strategy == "individual":
        for group_key, actions, _label in groups:
            result.extend(_individual(actions, f"{node.key}.{group_key}", context))
    else:
        for group_key, actions, label in groups:
            result.extend(_grouped(actions, f"{node.key}.{group_key}", label, path, context))
    if direct:
        controls = tuple(_button(action, context) if isinstance(action, Action) else action for action in direct)
        result.append(PrimitiveActionGroup(controls))
    context.events.append(
        PlanEvent(
            code=f"actions.{strategy}",
            path=path,
            message=f"Actions {node.key!r} uses the {strategy} strategy",
            severity=PlanSeverity.ADAPTATION,
            after={"adapter_version": ACTIONS_ADAPTER_VERSION},
        )
    )
    return result


def _action_axis(
    node: Actions,
    path: str,
    limits: V2Limits,
    session: PresentationSession,
) -> StrategyAxis:
    actions = [action for item in node.items for action in _contained_actions(item)]
    forced_pager = any(
        len(tuple(_contained_actions(item))) > 75 for item in node.items if isinstance(item, ActionGroup)
    )
    if not any(isinstance(item, ActionGroup) for item in node.items):
        forced_pager = len(actions) > 75
    available = ["grouped", "paged"] if forced_pager else ["individual", "grouped"]
    if not _individual_fits(len(actions), limits) and "individual" in available:
        available.remove("individual")
    preferred = {
        ActionDisplay.INDIVIDUAL: "individual",
        ActionDisplay.GROUPED: "grouped",
        ActionDisplay.AUTO: "individual" if len(actions) <= 5 else "grouped",
    }[node.display]
    if forced_pager:
        preferred = "paged"
    return _strategy_axis(
        path=path,
        key=node.key,
        adapter_id=ACTIONS_ADAPTER_ID,
        adapter_version=ACTIONS_ADAPTER_VERSION,
        flexibility=node.flexibility,
        preferred=preferred,
        available=tuple(available),
        order=("individual", "grouped", "paged"),
        session=session,
        active_pagers=frozenset(available) if forced_pager else frozenset(),
    )


def _contained_actions(item: Action | object) -> Sequence[Action]:
    if isinstance(item, Action):
        return (item,)
    if isinstance(item, ActionGroup):
        return tuple(action for action in item.actions if isinstance(action, Action))
    return ()


def _individual(actions: Sequence[Action], key: str, context: _Context) -> list[Node]:
    controls = tuple(_button(action, context) for action in actions)
    return [PrimitiveActionGroup(controls)] if controls else []


def _grouped(actions: Sequence[Action], key: str, label: str | None, path: str, context: _Context) -> list[Node]:
    eligible: list[Action] = []
    direct: list[Action] = []
    for action in actions:
        default_grouping = action.emphasis.value != "strong" and action.tone in {Tone.NEUTRAL, Tone.INFO}
        if action.allow_grouping if action.allow_grouping is not None else default_grouping:
            eligible.append(action)
        else:
            direct.append(action)

    result: list[Node] = []
    if len(eligible) > 75:
        result.extend(_paged_picker(eligible, key, label, context))
    else:
        result.extend(
            _picker(
                tuple(eligible[start : start + context.limits.select_options]),
                f"{key}.{start // 25}",
                label,
                context,
            )
            for start in range(0, len(eligible), context.limits.select_options)
        )
    if direct:
        result.extend(_individual(direct, f"{key}.direct", context))
    return result


def _paged_picker(actions: Sequence[Action], key: str, label: str | None, context: _Context) -> list[Node]:
    chunk, index, pages = _page_items(actions, key, context, identity=lambda action: action.key)
    return [
        _picker(chunk, f"{key}.page", label, context),
        *context.pages.controls(key, Position(offset=index), pages),
    ]


def _page_items[T](
    items: Sequence[T],
    pager_key: str,
    context: _Context,
    *,
    identity: Callable[[T], str],
) -> tuple[tuple[T, ...], int, int]:
    """Window a list of options 25 at a time, following the item the reader was on."""
    per = context.limits.select_options
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


def _picker(actions: Sequence[Action], key: str, label: str | None, context: _Context) -> SelectMenu:
    routes = {
        action.key: ActionBinding(
            action.key,
            action.on_trigger,
            action.policy,
            guard=action.guard,
            label=_resolve(action.label, context),
            record=action.record,
        )
        for action in actions
    }

    async def route(event: SelectionEvent) -> None:
        binding = routes.get(event.values[0]) if len(event.values) == 1 else None
        if binding is not None:
            await binding.handler(event)

    return SelectMenu(
        tuple(Option(_resolve(action.label, context), action.key) for action in actions),
        route,
        key,
        placeholder=label or "Choose an action",
        routes=routes,
    )


def _unbound_button(item: Link | RoutedAction, context: _Context) -> LinkButton | RoutedButton:
    """Lower a control the mount never dispatches: a URL, or a router's own custom id."""
    label = _resolve(item.label, context)
    if isinstance(item, Link):
        return LinkButton(label, item.url)
    return RoutedButton(
        label, item.route_id, style=_button_style(item.tone, item.emphasis), disabled=not item.available
    )


def _button_style(tone: Tone, emphasis: Emphasis) -> ActionStyle:
    return {
        Tone.SUCCESS: ActionStyle.SUCCESS,
        Tone.DANGER: ActionStyle.DANGER,
        Tone.INFO: ActionStyle.PRIMARY,
    }.get(tone, ActionStyle.PRIMARY if emphasis is Emphasis.STRONG else ActionStyle.SECONDARY)


def _button(action: Action, context: _Context) -> Button:
    return Button(
        _resolve(action.label, context),
        action.on_trigger,
        action.key,
        style=_button_style(action.tone, action.emphasis),
        disabled=not action.available,
        policy=action.policy,
        guard=action.guard,
        feedback=action.feedback,
        record=action.record,
    )
