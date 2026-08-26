"""Lower semantic author intent into finite target-shaped strategy candidates."""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from squid_ui.capabilities import Capability
from squid_ui.chrome import Chrome
from squid_ui.errors import LayoutInvariantError
from squid_ui.palette import DEFAULT_PALETTE, AccentDefault, Palette
from squid_ui.planning.cursors import CursorCoordinator
from squid_ui.planning.limits import DiscordLimits
from squid_ui.planning.search import DEFAULT_SEARCH_BUDGET
from squid_ui.planning.semantic_adaptation.common import (
    _resolve,
)
from squid_ui.planning.semantic_adaptation.controls import (
    _choices,
    _details,
    _entities,
    _form,
    _items,
    _navigation,
    _routed_choices,
    _toggle,
    _with_best_effort,
    _with_overflow,
)
from squid_ui.planning.semantic_adaptation.decisions import (
    branch_paths as _branch_paths,
)
from squid_ui.planning.semantic_adaptation.decisions import (
    fallback_rung as _fallback_rung,
)
from squid_ui.planning.semantic_adaptation.model import (
    LoweringContext as _Context,
)
from squid_ui.planning.semantic_adaptation.model import (
    SemanticLowering,
)
from squid_ui.planning.semantic_adaptation.regions import (
    _cards,
    _fold,
    _Fragment,
    _paged_region,
    _region,
    _settle,
)
from squid_ui.planning.semantic_adaptation.structures import (
    _actions,
    _grid,
    _media,
    _roster,
    _table,
)
from squid_ui.primitives.constraints import (
    Alt,
    Condense,
    Never,
    Overflow,
    Paginate,
    Spill,
    Truncate,
    alts,
)
from squid_ui.primitives.nodes import (
    ActionGroup as PrimitiveActionGroup,
)
from squid_ui.primitives.nodes import (
    Break,
    Budget,
    Button,
    Card,
    CardField,
    CardFooter,
    CardMedia,
    EntitySelect,
    Footer,
    Gallery,
    GalleryItem,
    Lines,
    LinkButton,
    Node,
    Panel,
    RoutedButton,
    RoutedSelect,
    Row,
    SelectMenu,
    Text,
    Thumbnail,
    Time,
    ZonedTime,
)
from squid_ui.primitives.nodes import (
    Code as PrimitiveCode,
)
from squid_ui.primitives.nodes import (
    File as PrimitiveFile,
)
from squid_ui.primitives.nodes import (
    Heading as PrimitiveHeading,
)
from squid_ui.primitives.nodes import (
    Section as PrimitiveSection,
)
from squid_ui.runtime.presentation import (
    PresentationSession,
)
from squid_ui.scene.model import PlanEvent, PlanSeverity
from squid_ui.semantic import (
    Actions,
    Article,
    Aside,
    BestEffort,
    Block,
    Budgeted,
    Choices,
    Cluster,
    Code,
    Details,
    Download,
    Emphasis,
    Entities,
    FallbackContent,
    Field,
    Fields,
    Figure,
    FormTrigger,
    Grid,
    Group,
    Heading,
    Items,
    KeepWithNext,
    LayoutNode,
    List,
    Measure,
    Media,
    Navigation,
    Note,
    OptionalContent,
    Paged,
    Paragraph,
    ProgressBar,
    Quote,
    Roster,
    RoutedChoices,
    Section,
    Spilled,
    Stack,
    Status,
    Table,
    Themed,
    Timestamp,
    Toggle,
    Tone,
    Truncated,
    Unbreakable,
    ZonedTimestamp,
)
from squid_ui.text import Localization


def lower_semantics(
    nodes: Sequence[LayoutNode],
    *,
    limits: DiscordLimits,
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


def _panel_children(children: Sequence[LayoutNode], path: str, context: _Context) -> list[Node]:
    """Lower children while recording that Discord already has their enclosing container."""
    context.panel_depth += 1
    try:
        return _children(children, path, context)
    finally:
        context.panel_depth -= 1


def _node(node: LayoutNode, path: str, context: _Context) -> list[Node]:
    match node:
        case Truncated(node=child, keep=keep):
            return [_with_overflow(item, Truncate(keep)) for item in _node(child, path, context)]
        case Spilled(node=child):
            return [_with_overflow(item, Spill()) for item in _node(child, path, context)]
        case OptionalContent(node=child):
            return _node(child, f"{path}.primary", context) if _fallback_rung(path, 2, context.fallbacks) == 0 else []
        case BestEffort(node=child):
            policy: Overflow = Spill() if isinstance(child, List | Fields) else Truncate()
            return [_with_best_effort(_with_overflow(item, policy)) for item in _node(child, path, context)]
        case Budgeted(node=child, minimum=minimum, preferred=preferred, stretch=stretch):
            if isinstance(child, Paged):
                return _paged_region(
                    child,
                    path,
                    context,
                    _node,
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
            return _paged_region(node, path, context, _node, minimum=0, preferred=node.chars, stretch=0)
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
        case Block(children=children, accent=accent):
            resolved_accent = context.palette.brand if accent is AccentDefault.INHERIT else accent
            if _cards(context):
                return [_region(Card(accent=resolved_accent), _children(children, path, context), context)]
            nested = context.panel_depth > 0
            contents = _panel_children(children, path, context)
            return contents if nested else [Panel(tuple(contents), accent=resolved_accent)]
        case (
            Section(children=children, heading=heading, accent=accent, thumbnail=thumbnail)
            | Article(children=children, heading=heading, accent=accent, thumbnail=thumbnail)
        ):
            resolved_heading = _resolve(heading.content, context)
            resolved_accent = context.palette.brand if accent is AccentDefault.INHERIT else accent
            if _cards(context):
                # One semantic region, one card: its heading is the embed title, its accent is
                # the embed colour, and its lead image is the thumbnail. Nothing has to be
                # guessed, because the semantic node already said which was which.
                return [
                    _region(
                        Card(
                            title=Text(resolved_heading, overflow=Never(), priority=int(heading.importance)),
                            thumbnail=None if not thumbnail else CardMedia(thumbnail),
                            accent=resolved_accent,
                        ),
                        _children(children, path, context),
                        context,
                    )
                ]
            nested = context.panel_depth > 0
            contents: list[Node] = []
            title = PrimitiveHeading(
                resolved_heading,
                level=heading.level,
                overflow=Never(),
                priority=int(heading.importance),
            )
            # The lead image sits beside the title and nothing else: picking "the body"
            # out of an arbitrary children tuple would be a guess.
            contents.append(PrimitiveSection(texts=(title,), accessory=Thumbnail(thumbnail)) if thumbnail else title)
            contents.extend(_panel_children(children, path, context))
            return contents if nested else [Panel(tuple(contents), accent=resolved_accent)]
        case Aside(children=children, tone=tone):
            accent = context.palette.tone(tone)
            if _cards(context):
                return [_region(Card(accent=accent), _children(children, path, context), context)]
            nested = context.panel_depth > 0
            contents = _panel_children(children, path, context)
            return contents if nested else [Panel(tuple(contents), accent=accent)]
        case Heading(content=content, level=level, importance=importance):
            return [
                PrimitiveHeading(_resolve(content, context), level=level, overflow=Never(), priority=int(importance))
            ]
        case Paragraph(content=content, importance=importance):
            return [Text(_resolve(content, context), overflow=Never(), priority=int(importance))]
        case Note(content=content, importance=importance):
            return [Footer(_resolve(content, context), overflow=Never(), priority=int(importance))]
        case List(items=items, key=key, ordered=ordered, page_size=page_size):
            marker = (lambda index: f"{index + 1}.") if ordered else (lambda _index: "•")
            lines = tuple(
                Alt(f"{marker(index)} {_resolve(item.content, context)}", priority=int(item.importance))
                for index, item in enumerate(items)
            )
            return [Lines(lines, overflow=Paginate(key=key, per=page_size))]
        case Fields(fields=fields):
            embeds = context.limits.embeds
            if Capability.LAYOUT_EMBED_FIELDS in context.capabilities and embeds is not None:
                entries = _card_fields(fields, context)
                per_card = embeds.fields
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
            if media.spoiler and _cards(context):
                message = (
                    f"{path}: classic targets cannot preserve media spoilers; provide an explicit Variants fallback"
                )
                raise LayoutInvariantError(message)
            if _cards(context):
                # The description rides along even where Discord will not show it: it is the
                # author's alternative text, and a scene that dropped it could not restore it.
                return [
                    Card(
                        image=CardMedia(media.url, media.description),
                        footer=None if caption is None else CardFooter(_resolve(caption, context)),
                    )
                ]
            children: list[Node] = [Gallery((GalleryItem(media.url, media.description, media.spoiler),))]
            if caption is not None:
                children.append(Footer(_resolve(caption, context)))
            return children
        case Media():
            return _media(node, path, context)
        case Details():
            return _details(node, path, context, _children)
        case Toggle():
            return _toggle(node, context)
        case Download(label=label, asset=asset, description=description, emphasis=emphasis, spoiler=spoiler):
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
                if spoiler:
                    message = (
                        f"{path}: classic targets cannot preserve file spoilers; provide an explicit Variants fallback"
                    )
                    raise LayoutInvariantError(message)
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
            return [Text(text, overflow=Never()), PrimitiveFile(asset.key, asset.name, asset.media_type, spoiler)]
        case Status(content=content, tone=tone):
            prefix = {
                Tone.INFO: "\N{INFORMATION SOURCE}\N{VARIATION SELECTOR-16} ",
                Tone.SUCCESS: "✅ ",
                Tone.WARNING: "⚠️ ",
                Tone.DANGER: "❌ ",
            }.get(tone, "")
            return [Text(prefix + _resolve(content, context), overflow=Never())]
        case ProgressBar(value=value, maximum=maximum, label=label):
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
            return _items(node, path, context, _children)
        case Navigation():
            return _navigation(node, path, context)
        case Table():
            return _table(node, path, context)
        case Grid():
            return _grid(node, path, context)
        case Roster():
            return _roster(node, context)
        case Panel(children=children, accent=accent):
            # The exact primitive, not a semantic region: it stays a Container and the classic
            # dialect refuses it by name. Quietly turning an author's `Panel` into an embed
            # would be reinterpreting a shape they chose for its own sake.
            return [Panel(tuple(_children(children, path, context)), accent)]
        case _:
            return [_primitive(node, context)]


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
        entries.append(
            CardField(
                name=_resolve(field.label, context),
                value=Text(value, overflow=policy, priority=int(field.importance)),
            )
        )
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
            return replace(node, label=None if label is None else _resolve(label, context))
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
    return Alt(primary, tuple(kept), priority=int(field.importance))


def _children(children: Sequence[LayoutNode], path: str, context: _Context) -> list[Node]:
    lowered: list[Node] = []
    for index, child in enumerate(children):
        lowered.extend(_node(child, f"{path}.{index}", context))
    return _fold(lowered, context) if _cards(context) else lowered
