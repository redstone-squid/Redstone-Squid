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

import discord
from discord.ui.select import BaseSelect

from squid_layouts.errors import ExistingLayoutError, LimitViolationError
from squid_layouts.planning.limits import LIMITS, V2Limits
from squid_layouts.planning.target import ResourceCost

type Path = tuple[int, ...]


class ViolationCode(StrEnum):
    """What a violation is, independent of how it is worded."""

    TOTAL_COMPONENTS = "total_components"
    TOTAL_TEXT = "total_text"
    ATTACHMENTS = "attachments"
    CUSTOM_ID_LENGTH = "custom_id_length"
    CUSTOM_ID_DUPLICATE = "custom_id_duplicate"
    BUTTON_LABEL = "button_label"
    SELECT_PLACEHOLDER = "select_placeholder"
    SELECT_OPTIONS = "select_options"
    OPTION_LABEL = "option_label"
    OPTION_VALUE = "option_value"
    OPTION_DESCRIPTION = "option_description"
    GALLERY_ITEMS = "gallery_items"
    GALLERY_ITEM_DESCRIPTION = "gallery_item_description"
    SECTION_TEXTS = "section_texts"
    ROW_BUTTONS = "row_buttons"


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
    """What a host-owned view already spends, as an immutable planning input."""

    cost: ResourceCost
    custom_ids: tuple[CustomIdSite, ...]
    components_v2: bool
    report: AuditReport

    @property
    def fingerprint(self) -> str:
        """A digest of the measurement, for detecting a host that changed since planning."""
        canonical = repr(
            (
                sorted(self.cost.values.items()),
                [(site.custom_id, site.path) for site in self.custom_ids],
                self.components_v2,
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
    view: discord.ui.LayoutView,
    *,
    attachments: int = 0,
    limits: V2Limits = LIMITS,
) -> DiscordReservation:
    """Measure what `view` already spends, without mutating or repairing it."""
    if not isinstance(view, discord.ui.LayoutView):
        message = f"measure expects a LayoutView, not {type(view).__name__}; classic views are plan 36"
        raise TypeError(message)

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

    return DiscordReservation(
        cost=ResourceCost({"components": components, "display_text": text, "attachments": attachments}),
        custom_ids=tuple(custom_ids),
        components_v2=True,
        report=audit(view, attachments=attachments, limits=limits),
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
        description = media_item.description
        if description is not None and len(description) > limits.gallery_item_description:
            violations.append(
                Violation(
                    ViolationCode.GALLERY_ITEM_DESCRIPTION,
                    f"gallery item description {len(description)} > {limits.gallery_item_description}",
                    repairable=True,
                    path=path,
                    item=gallery,
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
