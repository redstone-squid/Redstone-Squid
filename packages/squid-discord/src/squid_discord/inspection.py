"""Read-only measurement and validation of a host-owned Discord layout.

Fragment composition needs to know what a view already spends without changing it, so every
function here walks public item structure and returns values. `conform` is the repair adapter
over the same measurements; nothing in this module mutates a view.
"""

import hashlib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

import discord
from discord.ui.select import BaseSelect

from squid_discord.presentation import DiscordMode, DiscordPresentation
from squid_layouts.errors import ExistingLayoutError, LimitViolationError
from squid_layouts.planning.limits import (
    ATTACHMENTS,
    CLASSIC_LIMITS,
    COMPONENTS,
    CONTENT_TEXT,
    CONTROLS,
    DISPLAY_TEXT,
    EMBED_TEXT,
    EMBEDS,
    LIMITS,
    ROWS,
    ClassicLimits,
    DiscordLimits,
    V2Limits,
)
from squid_layouts.planning.target import ResourceCost

type Path = tuple[int, ...]

ALLOWED_SCHEMES = frozenset({"http", "https", "attachment"})
"""What Discord accepts in an embed URL. Anything else is rejected before it is sent."""


class ViolationCode(StrEnum):
    """What a violation is, independent of how it is worded."""

    TOTAL_COMPONENTS = "total_components"
    TOTAL_TEXT = "total_text"
    ATTACHMENTS = "attachments"
    CUSTOM_ID_LENGTH = "custom_id_length"
    CUSTOM_ID_DUPLICATE = "custom_id_duplicate"
    BUTTON_LABEL = "button_label"
    BUTTON_SHAPE = "button_shape"
    LINK_URL = "link_url"
    SELECT_PLACEHOLDER = "select_placeholder"
    SELECT_OPTIONS = "select_options"
    OPTION_LABEL = "option_label"
    OPTION_VALUE = "option_value"
    OPTION_DESCRIPTION = "option_description"
    GALLERY_ITEMS = "gallery_items"
    GALLERY_ITEM_DESCRIPTION = "gallery_item_description"
    SECTION_TEXTS = "section_texts"
    ROW_BUTTONS = "row_buttons"
    CLASSIC_PAYLOAD = "classic_payload"


@dataclass(frozen=True, slots=True)
class Violation:
    """One limit a view breaks, located and classified."""

    code: ViolationCode
    message: str
    repairable: bool
    path: Path = ()
    item: object | None = None


@dataclass(frozen=True, slots=True)
class CustomIdSite:
    """A non-null custom id and where in the view it lives."""

    custom_id: str
    path: Path


@dataclass(frozen=True, slots=True)
class AuditReport:
    """Every violation found in one view, with no repair applied."""

    violations: tuple[Violation, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def messages(self) -> tuple[str, ...]:
        return tuple(violation.message for violation in self.violations)

    def raise_if_invalid(self) -> None:
        """Raise `LimitViolationError` when anything at all was found."""
        if self.violations:
            raise LimitViolationError(list(self.messages))


@dataclass(frozen=True, slots=True)
class DiscordReservation:
    """What a host-owned message already spends, as an immutable planning input."""

    usage: ResourceCost
    """What the host actually spends right now."""
    reserved: ResourceCost
    """What must be withheld from planning, which is not always the same thing.

    They differ wherever a field is all-or-nothing. A classic message has one `content`
    field: if the host set it at all, Squid cannot add to it, so the whole 2,000-character
    slot is withheld however few characters the host actually wrote. Reporting only the
    2,000 would misstate the message's size; reporting only the actual length would let
    planning allocate content Squid has no way to deliver.
    """
    custom_ids: tuple[CustomIdSite, ...]
    components_v2: bool
    report: AuditReport
    mode: DiscordMode = DiscordMode.COMPONENTS_V2

    @property
    def cost(self) -> ResourceCost:
        """What planning must withhold. The reservation *is* the smaller target."""
        return self.reserved

    @property
    def fingerprint(self) -> str:
        """A digest of the measurement, for detecting a host that changed since planning."""
        canonical = repr(
            (
                sorted(self.usage.values.items()),
                sorted(self.reserved.values.items()),
                [(site.custom_id, site.path) for site in self.custom_ids],
                self.components_v2,
                self.mode.value,
            )
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def raise_if_invalid(self) -> None:
        """Raise `ExistingLayoutError` when the host was already broken."""
        if not self.report.ok:
            raise ExistingLayoutError(list(self.report.messages))


def _direct_children(item: object) -> Sequence[Any] | None:
    """Public direct children of one item, matching `walk_children` order."""
    children = getattr(item, "children", None)
    if children is None:
        return None
    if isinstance(item, discord.ui.Section):
        # `Section.walk_children` yields the accessory last, and its component count includes it.
        return (*children, item.accessory)
    return children


def _walk(items: Sequence[Any], prefix: Path = ()) -> Iterator[tuple[Any, Path]]:
    for index, item in enumerate(items):
        path = (*prefix, index)
        yield item, path
        children = _direct_children(item)
        if children is not None:
            yield from _walk(children, path)


def cost(*items: discord.ui.Item[Any], limits: V2Limits = LIMITS) -> ResourceCost:
    """Cost items a view does not contain yet, so a caller can reserve for them.

    The single definition of what a component costs: the item, every descendant, and the
    display text they carry. `Target`'s native-item adapter measures through here too.
    """
    del limits  # Costing is limit-independent; the parameter keeps call sites uniform.
    components = 0
    text = 0
    for item in items:
        for child, _ in _walk((item,)):
            components += 1
            if isinstance(child, discord.ui.TextDisplay):
                text += len(child.content)
    return ResourceCost({"components": components, "display_text": text})


def measure(
    host: DiscordPresentation | discord.ui.LayoutView | discord.ui.View,
    *,
    attachments: int = 0,
    limits: DiscordLimits | None = None,
) -> DiscordReservation:
    """Measure what a host message or view already spends, mutating and repairing nothing.

    One function over three shapes because a caller reserving room does not care which one
    it holds — it cares what is left. What it holds decides the axes, not the API.
    """
    if isinstance(host, DiscordPresentation):
        if host.mode is DiscordMode.CLASSIC:
            return measure_classic(host, attachments=attachments, limits=_classic(limits))
        return _measure_v2(host.layout, attachments=attachments + len(host.assets), limits=_v2(limits))
    if isinstance(host, discord.ui.LayoutView):
        return _measure_v2(host, attachments=attachments, limits=_v2(limits))
    if isinstance(host, discord.ui.View):
        # A bare classic view is controls and nothing else: it says nothing about the
        # content or embeds the same message may also carry.
        return measure_classic(DiscordPresentation.classic(view=host), attachments=attachments, limits=_classic(limits))
    message = f"measure expects a DiscordPresentation, LayoutView, or View, not {type(host).__name__}"
    raise TypeError(message)


def _v2(limits: DiscordLimits | None) -> V2Limits:
    return limits if isinstance(limits, V2Limits) else LIMITS


def _classic(limits: DiscordLimits | None) -> ClassicLimits:
    return limits if isinstance(limits, ClassicLimits) else CLASSIC_LIMITS


def effective_rows(view: discord.ui.View) -> tuple[int, ...]:
    """The row index discord.py actually assigned each child, in child order.

    No public API exposes this: `Item.row` is what the author *asked* for and is `None`
    whenever they did not ask, while `_ViewWeights` decides where the item really lands.
    Contributing after a host's controls needs the real answer, so this reads the private
    state and immediately cross-checks it against the serialized payload — if the number of
    distinct assigned rows ever stops matching the number of action rows discord.py emits,
    that assumption has broken and this raises instead of placing items wrongly.
    """
    assigned = tuple(getattr(item, "_rendered_row", None) for item in view.children)
    if any(row is None for row in assigned):
        message = "discord.py left a view child without an assigned row; its row weighting has changed"
        raise LimitViolationError([message])
    serialized = sum(1 for component in view.to_components() if component.get("type") == 1)
    if len({*assigned}) != serialized:
        message = (
            f"discord.py assigned {len({*assigned})} distinct rows but serialized {serialized} action rows; "
            "the private row state this reads no longer matches the payload"
        )
        raise LimitViolationError([message])
    return tuple(row for row in assigned if row is not None)


def measure_classic(
    host: DiscordPresentation,
    *,
    attachments: int = 0,
    limits: ClassicLimits = CLASSIC_LIMITS,
) -> DiscordReservation:
    """Measure a complete classic host message across the axes a classic target budgets."""
    if host.mode is not DiscordMode.CLASSIC:
        message = f"measure_classic expects a classic presentation, not {host.mode.value}"
        raise TypeError(message)
    view = host.view if isinstance(host.view, discord.ui.View) else None

    custom_ids: list[CustomIdSite] = []
    controls = 0
    rows: tuple[int, ...] = ()
    if view is not None:
        rows = effective_rows(view)
        for index, item in enumerate(view.children):
            controls += 1
            custom_id = getattr(item, "custom_id", None)
            if isinstance(custom_id, str):
                custom_ids.append(CustomIdSite(custom_id, (index,)))

    embed_text = sum(len(embed) for embed in host.embeds)
    files = attachments + len(host.assets)
    common = {
        EMBED_TEXT: embed_text,
        EMBEDS: len(host.embeds),
        ROWS: len({*rows}),
        CONTROLS: controls,
        ATTACHMENTS: files,
    }
    content = host.content
    usage = ResourceCost({**common, CONTENT_TEXT: len(content or "")})
    # All or nothing: a host that set `content` at all owns the whole slot, because a
    # message has one content field and Squid cannot append to someone else's.
    reserved = ResourceCost({**common, CONTENT_TEXT: limits.content if content is not None else 0})

    return DiscordReservation(
        usage=usage,
        reserved=reserved,
        custom_ids=tuple(custom_ids),
        components_v2=False,
        report=AuditReport(
            tuple(
                Violation(ViolationCode.CLASSIC_PAYLOAD, problem, repairable=False)
                for problem in audit_classic_payload(
                    content=content, embeds=host.embeds, view=view, attachments=files, limits=limits
                )
            )
        ),
        mode=DiscordMode.CLASSIC,
    )


def _measure_v2(
    view: discord.ui.LayoutView,
    *,
    attachments: int = 0,
    limits: V2Limits = LIMITS,
) -> DiscordReservation:
    """Measure what `view` already spends, without mutating or repairing it."""

    custom_ids: list[CustomIdSite] = []
    components = 0
    text = 0
    for item, path in _walk(view.children):
        components += 1
        if isinstance(item, discord.ui.TextDisplay):
            text += len(item.content)
        custom_id = getattr(item, "custom_id", None)
        if isinstance(custom_id, str):
            custom_ids.append(CustomIdSite(custom_id, path))

    spent = ResourceCost({COMPONENTS: components, DISPLAY_TEXT: text, ATTACHMENTS: attachments})
    return DiscordReservation(
        # Identical for Components V2: every axis it budgets is additive, so what the host
        # spends and what it withholds are the same number.
        usage=spent,
        reserved=spent,
        custom_ids=tuple(custom_ids),
        components_v2=True,
        report=audit(view, attachments=attachments, limits=limits),
        mode=DiscordMode.COMPONENTS_V2,
    )


def audit(
    view: discord.ui.LayoutView,
    *,
    attachments: int = 0,
    limits: V2Limits = LIMITS,
) -> AuditReport:
    """Find every limit `view` breaks, repairing nothing."""
    violations: list[Violation] = []
    text_total = 0
    seen_ids: dict[str, Path] = {}
    components = 0

    for item, path in _walk(view.children):
        components += 1
        _audit_custom_id(item, path, limits, seen_ids, violations)
        match item:
            case discord.ui.TextDisplay():
                text_total += len(item.content)
            case discord.ui.Button():
                _audit_button(item, path, limits, violations)
            case BaseSelect():
                _audit_select(item, path, limits, violations)
            case discord.ui.MediaGallery():
                _audit_gallery(item, path, limits, violations)
            case discord.ui.Thumbnail():
                _audit_media_description(item.description, item, path, limits, violations)
            case discord.ui.Section():
                _audit_section(item, path, limits, violations)
            case discord.ui.ActionRow():
                _audit_row(item, path, limits, violations)
            case _:
                pass

    # Message-wide budgets are reported after the walk so their counts are final.
    if components > limits.total_components:
        violations.insert(
            0,
            Violation(
                ViolationCode.TOTAL_COMPONENTS,
                f"{components} components exceed {limits.total_components} (not clampable)",
                repairable=False,
            ),
        )
    if text_total > limits.total_text:
        violations.append(
            Violation(
                ViolationCode.TOTAL_TEXT,
                f"total display text {text_total} > {limits.total_text}",
                repairable=True,
            )
        )
    if attachments > limits.attachments:
        violations.append(
            Violation(
                ViolationCode.ATTACHMENTS,
                f"{attachments} attachments exceed {limits.attachments} (not clampable)",
                repairable=False,
            )
        )
    return AuditReport(tuple(violations))


def _audit_custom_id(
    item: object,
    path: Path,
    limits: V2Limits,
    seen: dict[str, Path],
    violations: list[Violation],
) -> None:
    custom_id = getattr(item, "custom_id", None)
    if not isinstance(custom_id, str):
        return
    if len(custom_id) > limits.custom_id:
        violations.append(
            Violation(
                ViolationCode.CUSTOM_ID_LENGTH,
                f"custom id {len(custom_id)} > {limits.custom_id} (not clampable): {custom_id[:32]!r}...",
                repairable=False,
                path=path,
                item=item,
            )
        )
    if custom_id in seen:
        violations.append(
            Violation(
                ViolationCode.CUSTOM_ID_DUPLICATE,
                f"duplicate custom id {custom_id!r} at {path} and {seen[custom_id]} (not clampable)",
                repairable=False,
                path=path,
                item=item,
            )
        )
    else:
        seen[custom_id] = path


def _audit_button(button: discord.ui.Button, path: Path, limits: V2Limits, violations: list[Violation]) -> None:
    if button.label is not None and len(button.label) > limits.button_label:
        violations.append(
            Violation(
                ViolationCode.BUTTON_LABEL,
                f"button label {len(button.label)} > {limits.button_label}",
                repairable=True,
                path=path,
                item=button,
            )
        )
    if button.style is not discord.ButtonStyle.premium and button.label is None and button.emoji is None:
        violations.append(
            Violation(
                ViolationCode.BUTTON_SHAPE,
                "button needs a label or emoji (not clampable)",
                repairable=False,
                path=path,
                item=button,
            )
        )
    if button.style is discord.ButtonStyle.link and button.url is not None and len(button.url) > limits.link_url:
        violations.append(
            Violation(
                ViolationCode.LINK_URL,
                f"link URL {len(button.url)} > {limits.link_url} (not clampable)",
                repairable=False,
                path=path,
                item=button,
            )
        )


def _audit_select(select: BaseSelect, path: Path, limits: V2Limits, violations: list[Violation]) -> None:
    if select.placeholder is not None and len(select.placeholder) > limits.select_placeholder:
        violations.append(
            Violation(
                ViolationCode.SELECT_PLACEHOLDER,
                f"select placeholder {len(select.placeholder)} > {limits.select_placeholder}",
                repairable=True,
                path=path,
                item=select,
            )
        )
    if not isinstance(select, discord.ui.Select):
        return
    options = select.options
    if len(options) > limits.select_options:
        violations.append(
            Violation(
                ViolationCode.SELECT_OPTIONS,
                f"{len(options)} select options exceed {limits.select_options}",
                repairable=True,
                path=path,
                item=select,
            )
        )
    for option in options[: limits.select_options]:
        _audit_option(option, path, limits, violations, select)


def _audit_option(
    option: discord.SelectOption,
    path: Path,
    limits: V2Limits,
    violations: list[Violation],
    owner: object,
) -> None:
    if len(option.label) > limits.option_label:
        violations.append(
            Violation(
                ViolationCode.OPTION_LABEL,
                f"option label {len(option.label)} > {limits.option_label}",
                repairable=True,
                path=path,
                item=owner,
            )
        )
    if len(option.value) > limits.option_value:
        violations.append(
            Violation(
                ViolationCode.OPTION_VALUE,
                f"option value {len(option.value)} > {limits.option_value}",
                repairable=True,
                path=path,
                item=owner,
            )
        )
    if option.description is not None and len(option.description) > limits.option_description:
        violations.append(
            Violation(
                ViolationCode.OPTION_DESCRIPTION,
                f"option description {len(option.description)} > {limits.option_description}",
                repairable=True,
                path=path,
                item=owner,
            )
        )


def _audit_gallery(gallery: discord.ui.MediaGallery, path: Path, limits: V2Limits, violations: list[Violation]) -> None:
    items = gallery.items
    if len(items) > limits.gallery_items:
        violations.append(
            Violation(
                ViolationCode.GALLERY_ITEMS,
                f"{len(items)} gallery items exceed {limits.gallery_items}",
                repairable=True,
                path=path,
                item=gallery,
            )
        )
    for media_item in items[: limits.gallery_items]:
        _audit_media_description(media_item.description, gallery, path, limits, violations)


def _audit_media_description(
    description: str | None,
    owner: object,
    path: Path,
    limits: V2Limits,
    violations: list[Violation],
) -> None:
    if description is not None and len(description) > limits.gallery_item_description:
        violations.append(
            Violation(
                ViolationCode.GALLERY_ITEM_DESCRIPTION,
                f"media description {len(description)} > {limits.gallery_item_description}",
                repairable=isinstance(owner, discord.ui.MediaGallery),
                path=path,
                item=owner,
            )
        )


def _audit_section(section: discord.ui.Section, path: Path, limits: V2Limits, violations: list[Violation]) -> None:
    # discord.py rejects a fourth child at `add_item`, so this only fires on a view built
    # some other way — `from_message`, or direct list mutation.
    if len(section.children) > limits.section_texts:
        violations.append(
            Violation(
                ViolationCode.SECTION_TEXTS,
                f"{len(section.children)} section children exceed {limits.section_texts} (not clampable)",
                repairable=False,
                path=path,
                item=section,
            )
        )


def _audit_row(row: discord.ui.ActionRow, path: Path, limits: V2Limits, violations: list[Violation]) -> None:
    buttons = [child for child in row.children if isinstance(child, discord.ui.Button)]
    if len(buttons) > limits.row_buttons:
        violations.append(
            Violation(
                ViolationCode.ROW_BUTTONS,
                f"{len(buttons)} buttons in one row exceed {limits.row_buttons} (not clampable)",
                repairable=False,
                path=path,
                item=row,
            )
        )


def audit_classic_payload(
    *,
    content: str | None,
    embeds: Sequence[discord.Embed],
    view: discord.ui.View | None,
    attachments: int = 0,
    limits: ClassicLimits = CLASSIC_LIMITS,
) -> list[str]:
    """Every way this payload would be rejected, found before it is sent.

    Walks `Embed.to_dict()` because that is the wire shape, and cross-checks the aggregate
    against `Embed.__len__`, which already computes exactly Discord's definition — title,
    description, field names and values, footer text, author name. Re-deriving that sum here
    would be a second, drifting definition of the same rule.
    """
    problems: list[str] = []

    if content is not None and len(content) > limits.content:
        problems.append(f"content is {len(content)} characters; the limit is {limits.content}")
    if len(embeds) > limits.embeds:
        problems.append(f"{len(embeds)} embeds exceed {limits.embeds}")

    seen_urls: set[str] = set()
    for index, embed in enumerate(embeds):
        problems.extend(_audit_embed(embed, index, limits))
        url = embed.url
        if url is None:
            continue
        if url in seen_urls:
            # Discord renders only the first embed of a repeated URL, so a second one is
            # silently invisible rather than an error — which is worse than an error.
            problems.append(f"embed {index} repeats the URL {url!r}; Discord shows only the first")
        seen_urls.add(url)

    aggregate = sum(len(embed) for embed in embeds)
    if aggregate > limits.embed_text:
        problems.append(f"embed text totals {aggregate} characters; the limit is {limits.embed_text}")

    if view is not None:
        problems.extend(_audit_view(view, limits))
    if attachments > limits.attachments:
        problems.append(f"{attachments} attachments exceed {limits.attachments}")
    return problems


def _audit_embed(embed: discord.Embed, index: int, limits: ClassicLimits) -> list[str]:
    payload = embed.to_dict()
    problems: list[str] = []

    def check(value: object, cap: int, what: str) -> None:
        if isinstance(value, str) and len(value) > cap:
            problems.append(f"embed {index} {what} is {len(value)} characters; the limit is {cap}")

    check(payload.get("title"), limits.embed_title, "title")
    check(payload.get("description"), limits.embed_description, "description")
    check((payload.get("footer") or {}).get("text"), limits.embed_footer, "footer")
    check((payload.get("author") or {}).get("name"), limits.embed_author, "author")

    fields = payload.get("fields") or []
    if len(fields) > limits.embed_fields:
        problems.append(f"embed {index} has {len(fields)} fields; the limit is {limits.embed_fields}")
    for position, field in enumerate(fields):
        check(field.get("name"), limits.field_name, f"field {position} name")
        check(field.get("value"), limits.field_value, f"field {position} value")
        if not (field.get("name") or "").strip():
            problems.append(f"embed {index} field {position} has an empty name")
        if not (field.get("value") or "").strip():
            problems.append(f"embed {index} field {position} has an empty value")

    for key in ("url", "image", "thumbnail", "footer", "author"):
        raw = payload.get(key)
        candidate = raw if isinstance(raw, str) else (raw or {}).get("url") or (raw or {}).get("icon_url")
        if isinstance(candidate, str) and candidate:
            scheme = urlsplit(candidate).scheme
            if scheme not in ALLOWED_SCHEMES:
                problems.append(f"embed {index} {key} uses the unsupported URL scheme {scheme or '(none)'!r}")
    return problems


def _audit_view(view: discord.ui.View, limits: ClassicLimits) -> list[str]:
    problems: list[str] = []
    children = list(view.children)
    if len(children) > limits.controls:
        problems.append(f"{len(children)} view children exceed {limits.controls}")

    rows: dict[int, list[discord.ui.Item[Any]]] = {}
    for item in children:
        rows.setdefault(item.row if item.row is not None else -1, []).append(item)
    if len(rows) > limits.rows:
        problems.append(f"{len(rows)} action rows exceed {limits.rows}")
    for index, items in sorted(rows.items()):
        selects = sum(isinstance(item, BaseSelect) for item in items)
        if selects and len(items) > 1:
            problems.append(f"row {index} mixes a select with {len(items) - 1} other controls")
        if selects > 1:
            problems.append(f"row {index} holds {selects} selects; a row holds one")
        if not selects and len(items) > limits.row_buttons:
            problems.append(f"row {index} holds {len(items)} buttons; the limit is {limits.row_buttons}")

    seen: set[str] = set()
    for item in children:
        custom_id = getattr(item, "custom_id", None)
        if not isinstance(custom_id, str):
            continue
        if len(custom_id) > limits.custom_id:
            problems.append(f"custom id {custom_id!r} is {len(custom_id)} characters; the limit is {limits.custom_id}")
        if custom_id in seen:
            problems.append(f"custom id {custom_id!r} appears twice in one message")
        seen.add(custom_id)
    return problems
