"""Portable semantic planning for Slack Block Kit surfaces."""

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass, replace
from datetime import datetime
from typing import Any, cast

from squid_ui import forms, scene
from squid_ui import semantic as sem
from squid_ui.document import DocumentLike, as_document
from squid_ui.entity import ConversationType, EntityType, decode_entity_ref
from squid_ui.errors import LayoutDegradedError, LayoutInvariantError
from squid_ui.factories import is_portable_node
from squid_ui.planning.cache import CachedPlan, PlanCache, PlanMemo
from squid_ui.planning.html_planner import HTML_PLANNER
from squid_ui.planning.identity import stable_fingerprint
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.resources import EMPTY_COST
from squid_ui.planning.target import Target
from squid_ui.scene.model import PlanEvent, PlanMetrics, PlanReport, PlanResult, PlanReuse, PlanSeverity
from squid_ui.slack.target import SlackHomeLimits, SlackMessageLimits, SlackModalLimits
from squid_ui.target_types import SlackTarget
from squid_ui.text import Markup, Message, ResolvedText, resolve_text

type SlackLimits = SlackMessageLimits | SlackModalLimits | SlackHomeLimits
type SlackBody = scene.SlackMessage | scene.SlackModalView | scene.SlackHomeView
type SlackTargetT = Target[Any, Any, SlackTarget, Any]

_HEADINGS = frozenset(
    {
        scene.HtmlTag.H1,
        scene.HtmlTag.H2,
        scene.HtmlTag.H3,
        scene.HtmlTag.H4,
        scene.HtmlTag.H5,
        scene.HtmlTag.H6,
    }
)
_SLACK_CONVERSATIONS = frozenset(
    {
        ConversationType.WORKSPACE_PUBLIC,
        ConversationType.WORKSPACE_PRIVATE,
        ConversationType.DIRECT,
        ConversationType.GROUP_DIRECT,
    }
)
_LINK = re.compile(r"\[([^]]+)]\((https?://[^)]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_TIMESTAMP_STYLES = {
    "t": "{time}",
    "T": "{time_secs}",
    "d": "{date_num}",
    "D": "{date_long}",
    "f": "{date_short_pretty} at {time}",
    "F": "{date_long_pretty} at {time}",
    "R": "{ago}",
}


def _attribute(element: scene.HtmlElement, name: scene.HtmlAttributeName) -> scene.HtmlAttributeValue | None:
    return next((attribute.value for attribute in element.attributes if attribute.name is name), None)


def _has_attribute(element: scene.HtmlElement, name: scene.HtmlAttributeName) -> bool:
    return any(attribute.name is name for attribute in element.attributes)


def _class_names(element: scene.HtmlElement) -> frozenset[str]:
    value = _attribute(element, scene.HtmlAttributeName.CLASS)
    return frozenset(str(value).split()) if value is not None else frozenset()


def _html_text(node: scene.HtmlNode) -> str:
    if isinstance(node, scene.HtmlText):
        return node.content
    separator = "\n" if node.tag in {scene.HtmlTag.BR, scene.HtmlTag.LI, scene.HtmlTag.TR} else ""
    return separator.join(filter(None, (_html_text(child) for child in node.children)))


def _plain_text(value: str) -> str:
    value = _LINK.sub(r"\1", value)
    for token in ("**", "__", "~~", "`", "*", "_"):
        value = value.replace(token, "")
    return value.replace("\\", "").strip()


def _mrkdwn(value: str, markup: Markup = Markup.DISCORD_MARKDOWN) -> str:
    escaped = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if markup is Markup.PLAIN:
        return escaped
    escaped = _LINK.sub(r"<\2|\1>", escaped)
    escaped = _BOLD.sub(r"*\1*", escaped)
    escaped = _ITALIC.sub(r"_\1_", escaped)
    return escaped.replace("~~", "~")


def _nested_form(node: object) -> bool:
    if isinstance(node, sem.FormTrigger):
        return True
    if not is_dataclass(node):
        return False
    for held in dataclass_fields(node):
        value = getattr(node, held.name)
        if is_portable_node(value) and _nested_form(value):
            return True
        if isinstance(value, tuple) and any(is_portable_node(item) and _nested_form(item) for item in value):
            return True
    return False


def _modal_form(rendered: DocumentLike[Any]) -> sem.FormTrigger:
    document = as_document(rendered)
    roots = tuple(child for child in document.children if isinstance(child, sem.FormTrigger))
    if len(roots) != 1:
        message = "Slack modal planning requires exactly one top-level sl.form()"
        raise LayoutInvariantError(message)
    if any(_nested_form(child) for child in document.children if child is not roots[0]):
        message = "Slack modal forms must be top-level, not nested in surrounding content"
        raise LayoutInvariantError(message)
    return roots[0]


class _Lowerer:
    """Lower one semantic HTML intermediate into surface-specific Block Kit."""

    def __init__(
        self,
        *,
        target: SlackTargetT,
        request: PlanRequest[Any, Any, Any],
        modal_form: sem.FormTrigger | None,
    ) -> None:
        self.target = target
        self.limits = cast(SlackLimits, target.limits)
        self.request = request
        self.modal_form = modal_form
        self.events: list[PlanEvent] = []

    @property
    def surface(self) -> str:
        return self.target.id.rsplit(".", 1)[-1]

    def event(
        self,
        code: str,
        path: str,
        message: str,
        severity: PlanSeverity = PlanSeverity.ADAPTATION,
        *,
        before: Mapping[str, int] | None = None,
        after: Mapping[str, int] | None = None,
    ) -> None:
        self.events.append(PlanEvent(code, path, message, severity, before or {}, after or {}))

    def fit(self, value: str, capacity: int, path: str, subject: str) -> str:
        if len(value) <= capacity:
            return value
        fitted = value[: max(0, capacity - 1)] + ("…" if capacity else "")
        self.event(
            f"slack.{subject}.truncated",
            path,
            f"Slack {subject.replace('_', ' ')} omitted {len(value) - len(fitted)} characters",
            PlanSeverity.DEGRADATION,
            before={"characters": len(value)},
            after={"characters": len(fitted)},
        )
        return fitted

    def text(self, value: str, path: str, *, capacity: int | None = None) -> scene.SlackText:
        rendered = _mrkdwn(value)
        if capacity is not None:
            rendered = self.fit(rendered, capacity, path, "text")
        return scene.SlackText(rendered)

    def plain(self, value: str, path: str, *, capacity: int) -> scene.SlackText:
        return scene.SlackText(
            self.fit(_plain_text(value), capacity, path, "plain_text"),
            scene.SlackTextKind.PLAIN,
            emoji=True,
            verbatim=None,
        )

    def blocks(self, nodes: Sequence[scene.HtmlNode], path: str = "$") -> tuple[scene.SlackBlock, ...]:
        blocks = tuple(block for index, node in enumerate(nodes) for block in self.node_blocks(node, f"{path}.{index}"))
        if len(blocks) <= self.limits.blocks:
            return blocks
        kept = blocks[: self.limits.blocks]
        self.event(
            "slack.blocks.truncated",
            path,
            f"Slack {self.surface} omitted {len(blocks) - len(kept)} blocks",
            PlanSeverity.DEGRADATION,
            before={"blocks": len(blocks)},
            after={"blocks": len(kept)},
        )
        return kept

    def node_blocks(self, node: scene.HtmlNode, path: str) -> tuple[scene.SlackBlock, ...]:
        if isinstance(node, scene.HtmlText):
            if not node.content.strip():
                return ()
            return (scene.SlackSection(self.text(node.content, path, capacity=self.limits.components.section_text)),)
        if node.tag in _HEADINGS:
            return (scene.SlackHeader(self.plain(_html_text(node), path, capacity=self.limits.components.header_text)),)
        if node.tag is scene.HtmlTag.HR:
            return (scene.SlackDivider(),)
        if node.tag is scene.HtmlTag.TIME and node.time is not None:
            return (scene.SlackSection(self._time(node, path)),)
        if node.tag is scene.HtmlTag.IMG:
            return self._image(node, path)
        if node.tag is scene.HtmlTag.TABLE:
            return (self._table(node, path),)
        if node.tag is scene.HtmlTag.ARTICLE:
            return (self._card(node, path),)
        if node.tag is scene.HtmlTag.FORM:
            if self.surface == "modal":
                if self.modal_form is None:
                    message = "Slack modal form intermediate has no root form schema"
                    raise LayoutInvariantError(message)
                return self._form_blocks(self.modal_form, path)
            submit = next(
                (
                    descendant
                    for descendant in self._elements(node)
                    if descendant.tag is scene.HtmlTag.BUTTON and descendant.action is not None
                ),
                None,
            )
            return () if submit is None else self._control_blocks((submit,), path)
        if node.tag is scene.HtmlTag.DETAILS and node.action is not None:
            summary = next(
                (
                    child
                    for child in node.children
                    if isinstance(child, scene.HtmlElement) and child.tag is scene.HtmlTag.SUMMARY
                ),
                None,
            )
            label = _html_text(summary) if summary is not None else "Details"
            control = scene.HtmlElement(
                scene.HtmlTag.BUTTON,
                (scene.HtmlText(label),),
                action=node.action,
            )
            content = tuple(
                block
                for index, child in enumerate(node.children)
                if child is not summary
                for block in self.node_blocks(child, f"{path}.{index}")
            )
            return (*self._control_blocks((control,), path), *content)
        if node.tag is scene.HtmlTag.LABEL:
            toggle = next(
                (
                    child
                    for child in node.children
                    if isinstance(child, scene.HtmlElement)
                    and child.tag is scene.HtmlTag.INPUT
                    and child.action is not None
                ),
                None,
            )
            if toggle is not None:
                labelled = replace(toggle, children=(scene.HtmlText(_html_text(node)),))
                return self._control_blocks((labelled,), path)
        if node.tag in {scene.HtmlTag.BUTTON, scene.HtmlTag.A, scene.HtmlTag.SELECT} or (
            node.tag is scene.HtmlTag.INPUT and _attribute(node, scene.HtmlAttributeName.ENTITY_TYPE) is not None
        ):
            return self._control_blocks((node,), path)
        classes = _class_names(node)
        if "squid-gallery" in classes:
            return self._gallery(node, path)
        if "squid-actions" in classes or "squid-grid" in classes or node.tag is scene.HtmlTag.NAV:
            controls = tuple(
                child
                for child in self._elements(node)
                if child.tag in {scene.HtmlTag.BUTTON, scene.HtmlTag.A, scene.HtmlTag.SELECT}
            )
            if controls:
                return self._control_blocks(controls, path)
        if _attribute(node, scene.HtmlAttributeName.ROLE) == "status":
            content = _html_text(node)
            if self.surface == "modal":
                return (
                    scene.SlackAlert(
                        self.plain(content, path, capacity=self.limits.components.alert_text),
                        style=self._alert_style(node),
                    ),
                )
            return (scene.SlackContext((self.text(content, path, capacity=self.limits.components.section_text),)),)
        if node.tag in {
            scene.HtmlTag.P,
            scene.HtmlTag.SMALL,
            scene.HtmlTag.BLOCKQUOTE,
            scene.HtmlTag.PRE,
            scene.HtmlTag.CODE,
            scene.HtmlTag.UL,
            scene.HtmlTag.OL,
            scene.HtmlTag.DL,
            scene.HtmlTag.PROGRESS,
        }:
            content = _html_text(node).strip()
            if not content:
                return ()
            prefix = "> " if node.tag is scene.HtmlTag.BLOCKQUOTE else ""
            if node.tag is scene.HtmlTag.PRE:
                content = f"```\n{content}\n```"
            return (
                scene.SlackSection(self.text(prefix + content, path, capacity=self.limits.components.section_text)),
            )
        return tuple(
            block for index, child in enumerate(node.children) for block in self.node_blocks(child, f"{path}.{index}")
        )

    @staticmethod
    def _elements(node: scene.HtmlElement) -> tuple[scene.HtmlElement, ...]:
        elements: list[scene.HtmlElement] = []
        for child in node.children:
            if isinstance(child, scene.HtmlElement):
                elements.append(child)
                elements.extend(_Lowerer._elements(child))
        return tuple(elements)

    def _time(self, node: scene.HtmlElement, path: str) -> scene.SlackText:
        assert node.time is not None
        try:
            instant = datetime.fromisoformat(node.time.instant)
            epoch = round(instant.timestamp())
        except ValueError:
            self.event(
                "slack.time.literal",
                path,
                "Invalid ISO timestamp rendered as literal text",
                PlanSeverity.WARNING,
            )
            return self.text(_html_text(node), path, capacity=self.limits.components.section_text)
        token = _TIMESTAMP_STYLES.get(node.time.style or "f", "{date_short_pretty} at {time}")
        fallback = _plain_text(_html_text(node))
        return scene.SlackText(f"<!date^{epoch}^{token}|{fallback}>")

    def _image(self, node: scene.HtmlElement, path: str) -> tuple[scene.SlackBlock, ...]:
        if node.url is None:
            self.event("slack.image.omitted", path, "Image without a public URL was omitted", PlanSeverity.DEGRADATION)
            return ()
        alt = str(_attribute(node, scene.HtmlAttributeName.ALT) or "Image")
        title = str(_attribute(node, scene.HtmlAttributeName.TITLE) or "").strip()
        return (
            scene.SlackImage(
                node.url.url,
                self.fit(alt, 2000, path, "image_alt"),
                None if not title else self.plain(title, path, capacity=2000),
            ),
        )

    def _table(self, node: scene.HtmlElement, path: str) -> scene.SlackTable:
        rows: list[tuple[scene.SlackText, ...]] = []
        for row in (item for item in self._elements(node) if item.tag is scene.HtmlTag.TR):
            cells = tuple(
                self.text(_html_text(cell), path, capacity=self.limits.components.section_field_text)
                for cell in row.children
                if isinstance(cell, scene.HtmlElement) and cell.tag in {scene.HtmlTag.TH, scene.HtmlTag.TD}
            )[: self.limits.components.table_columns]
            if cells:
                rows.append(cells)
        rows = rows[: self.limits.components.table_rows]
        total = sum(len(cell.content) for row in rows for cell in row)
        if total > self.limits.components.table_text:
            self.event(
                "slack.table.text_overflow",
                path,
                "Slack table exceeded its aggregate text limit and was converted to records",
                PlanSeverity.DEGRADATION,
            )
            flattened = tuple(
                (self.text(" • ".join(cell.content for cell in row), path, capacity=2000),) for row in rows
            )
            return scene.SlackTable(flattened)
        return scene.SlackTable(tuple(rows))

    def _card(self, node: scene.HtmlElement, path: str) -> scene.SlackCard:
        heading = next(
            (child for child in node.children if isinstance(child, scene.HtmlElement) and child.tag in _HEADINGS),
            None,
        )
        title = (
            None
            if heading is None
            else self.plain(_html_text(heading), path, capacity=self.limits.components.card_title)
        )
        description = "\n".join(_html_text(child) for child in node.children if child is not heading).strip()
        return scene.SlackCard(
            title=title,
            description=(
                None if not description else self.text(description, path, capacity=self.limits.components.card_body)
            ),
        )

    def _gallery(self, node: scene.HtmlElement, path: str) -> tuple[scene.SlackBlock, ...]:
        images = tuple(item for item in self._elements(node) if item.tag is scene.HtmlTag.IMG and item.url is not None)
        cards: list[scene.SlackCard] = []
        for item in images[: self.limits.components.carousel_cards]:
            url = item.url
            if not isinstance(url, scene.HtmlUrlRef):
                message = f"{path}: gallery image URL was not resolved"
                raise LayoutInvariantError(message)
            cards.append(
                scene.SlackCard(
                    description=self.text(str(_attribute(item, scene.HtmlAttributeName.ALT) or "Image"), path),
                    image_url=url.url,
                )
            )
        if not cards:
            return ()
        if self.surface == "modal" or len(cards) == 1:
            return tuple(
                scene.SlackImage(
                    card.image_url or "",
                    _plain_text(card.description.content if card.description else "Image"),
                )
                for card in cards
            )
        return (scene.SlackCarousel(tuple(cards)),)

    def _alert_style(self, node: scene.HtmlElement) -> scene.SlackAlertStyle:
        tone = str(_attribute(node, scene.HtmlAttributeName.TONE) or "neutral")
        return {
            "danger": scene.SlackAlertStyle.ERROR,
            "success": scene.SlackAlertStyle.SUCCESS,
            "warning": scene.SlackAlertStyle.WARNING,
        }.get(tone, scene.SlackAlertStyle.INFO)

    def _control_blocks(self, controls: Sequence[scene.HtmlElement], path: str) -> tuple[scene.SlackBlock, ...]:
        elements: list[scene.SlackButton | scene.SlackSelect] = []
        context: list[scene.SlackBlock] = []
        for index, control in enumerate(controls):
            item_path = f"{path}.{index}"
            if _has_attribute(control, scene.HtmlAttributeName.DISABLED):
                label = _plain_text(_html_text(control)) or "Unavailable"
                context.append(scene.SlackContext((self.plain(f"{label} — Unavailable", item_path, capacity=2000),)))
                self.event(
                    "slack.control.unavailable",
                    item_path,
                    "Unavailable control was rendered as noninteractive text",
                    PlanSeverity.DEGRADATION,
                )
                continue
            if (
                control.tag is scene.HtmlTag.SELECT
                or _attribute(control, scene.HtmlAttributeName.ENTITY_TYPE) is not None
            ):
                elements.append(self._select(control, item_path))
            else:
                elements.append(self._button(control, item_path))
        rows = tuple(
            scene.SlackActions(tuple(elements[start : start + self.limits.components.actions_elements]))
            for start in range(0, len(elements), self.limits.components.actions_elements)
        )
        return (*rows, *context)

    def _button(self, node: scene.HtmlElement, path: str) -> scene.SlackButton:
        label = self.plain(_html_text(node) or "Action", path, capacity=self.limits.components.button_label)
        style = self._button_style(node)
        if node.action is not None:
            self._stable_id(node.action.action, path)
            return scene.SlackButton(
                label,
                action=scene.SlackActionRef(node.action.action, node.action.mode),
                value=self.fit(node.action.action, self.limits.components.button_value, path, "button_value"),
                style=style,
            )
        if node.route is not None:
            self._stable_id(node.route.route_id, path)
            return scene.SlackButton(label, route=scene.SlackRouteRef(node.route.route_id), style=style)
        if node.url is not None:
            return scene.SlackButton(label, url=node.url.url, style=style)
        if node.asset is not None:
            return scene.SlackButton(
                label,
                asset=scene.SlackAssetRef(node.asset.key, node.asset.name, node.asset.media_type),
                style=style,
            )
        message = f"{path}: Slack button has no action, route, URL, or asset"
        raise LayoutInvariantError(message)

    @staticmethod
    def _button_style(node: scene.HtmlElement) -> scene.SlackButtonStyle:
        tone = str(_attribute(node, scene.HtmlAttributeName.TONE) or "neutral")
        if tone == "danger":
            return scene.SlackButtonStyle.DANGER
        if tone in {"success", "info"} or _has_attribute(node, scene.HtmlAttributeName.CHECKED):
            return scene.SlackButtonStyle.PRIMARY
        return scene.SlackButtonStyle.DEFAULT

    def _select(self, node: scene.HtmlElement, path: str) -> scene.SlackSelect:
        action = None if node.action is None else scene.SlackActionRef(node.action.action, node.action.mode)
        route = None if node.route is None else scene.SlackRouteRef(node.route.route_id)
        identity = (
            node.action.action if node.action is not None else node.route.route_id if node.route is not None else ""
        )
        self._stable_id(identity, path)
        placeholder = self.plain(
            str(_attribute(node, scene.HtmlAttributeName.ARIA_LABEL) or "Choose an option"),
            path,
            capacity=self.limits.components.placeholder,
        )
        entity_type = _attribute(node, scene.HtmlAttributeName.ENTITY_TYPE)
        options = self._options(node, path)
        minimum = int(_attribute(node, scene.HtmlAttributeName.SELECTION_MIN) or 1)
        maximum = int(_attribute(node, scene.HtmlAttributeName.SELECTION_MAX) or 1)
        initial = tuple(option.value for option, selected in options if selected)
        if not initial and (stored := _attribute(node, scene.HtmlAttributeName.VALUE)):
            initial = tuple(value for value in str(stored).split(",") if value)
        if entity_type == EntityType.USER.value:
            return scene.SlackSelect(
                action,
                route,
                None,
                scene.SlackSelectKind.USERS,
                placeholder,
                initial_values=self._entity_ids(initial, path),
                minimum=minimum,
                maximum=maximum,
            )
        if entity_type == EntityType.CONVERSATION.value:
            raw_types = str(_attribute(node, scene.HtmlAttributeName.CONVERSATION_TYPES) or "")
            conversation_types = tuple(ConversationType(value) for value in raw_types.split(",") if value)
            if set(conversation_types) <= _SLACK_CONVERSATIONS:
                return scene.SlackSelect(
                    action,
                    route,
                    None,
                    scene.SlackSelectKind.CONVERSATIONS,
                    placeholder,
                    initial_values=self._entity_ids(initial, path),
                    conversation_types=conversation_types,
                    minimum=minimum,
                    maximum=maximum,
                )
        elif entity_type is not None and entity_type != EntityType.USER.value and not options:
            message = f"{path}: Slack has no native {entity_type} selector and no enumerated fallback"
            raise LayoutInvariantError(message)
        if not options:
            message = f"{path}: Slack selector needs options or a supported native entity family"
            raise LayoutInvariantError(message)
        available = tuple(option for option, _selected in options)
        if len(available) > self.limits.components.select_options:
            if maximum > 1:
                message = f"{path}: Slack multi-selects cannot exceed 100 options"
                raise LayoutInvariantError(message)
            self.event(
                "slack.select.options.truncated",
                path,
                "Slack select omitted options beyond its first 100 entries",
                PlanSeverity.DEGRADATION,
                before={"options": len(available)},
                after={"options": self.limits.components.select_options},
            )
            available = available[: self.limits.components.select_options]
        return scene.SlackSelect(
            action,
            route,
            None,
            scene.SlackSelectKind.STATIC,
            placeholder,
            options=available,
            initial_values=initial,
            minimum=minimum,
            maximum=maximum,
        )

    def _options(self, node: scene.HtmlElement, path: str) -> tuple[tuple[scene.SlackOption, bool], ...]:
        options: list[tuple[scene.SlackOption, bool]] = []
        for index, child in enumerate(node.children):
            if not isinstance(child, scene.HtmlElement) or child.tag is not scene.HtmlTag.OPTION:
                continue
            if _has_attribute(child, scene.HtmlAttributeName.DISABLED):
                self.event(
                    "slack.option.unavailable",
                    f"{path}.{index}",
                    "Unavailable option was omitted from Slack selector",
                    PlanSeverity.DEGRADATION,
                )
                continue
            value = str(_attribute(child, scene.HtmlAttributeName.VALUE) or "")
            if not value or len(value) > self.limits.components.option_value:
                message = f"{path}.{index}: Slack option values must contain 1-150 characters"
                raise LayoutInvariantError(message)
            description = _attribute(child, scene.HtmlAttributeName.TITLE)
            options.append(
                (
                    scene.SlackOption(
                        self.plain(
                            _html_text(child),
                            f"{path}.{index}",
                            capacity=self.limits.components.option_label,
                        ),
                        value,
                        None
                        if description is None
                        else self.plain(
                            str(description),
                            f"{path}.{index}",
                            capacity=self.limits.components.option_description,
                        ),
                    ),
                    _has_attribute(child, scene.HtmlAttributeName.SELECTED),
                )
            )
        return tuple(options)

    @staticmethod
    def _entity_ids(values: Sequence[str], path: str) -> tuple[str, ...]:
        identifiers: list[str] = []
        for value in values:
            try:
                identifiers.append(str(decode_entity_ref(value).id))
            except ValueError as error:
                message = f"{path}: invalid entity selector state"
                raise LayoutInvariantError(message) from error
        return tuple(identifiers)

    def _stable_id(self, value: str, path: str) -> None:
        if not value or len(value) > self.limits.components.action_id:
            message = f"{path}: Slack action ids must contain 1-{self.limits.components.action_id} characters"
            raise LayoutInvariantError(message)

    def _form_blocks(self, trigger: sem.FormTrigger, path: str) -> tuple[scene.SlackBlock, ...]:
        spec = trigger.spec.adapt(self.target.capabilities)
        blocks: list[scene.SlackBlock] = []
        for index, item in enumerate(spec.items):
            item_path = f"{path}.{index}"
            if isinstance(item, forms.FormText):
                content = resolve_text(item.content, self.request.localization).content
                blocks.append(scene.SlackSection(self.text(content, item_path, capacity=3000)))
                continue
            blocks.append(self._form_field(trigger.key, spec, item, item_path))
        return tuple(blocks)

    def _form_field(
        self,
        form_key: str,
        spec: forms.FormSpec,
        field: forms.FormField[Any],
        path: str,
    ) -> scene.SlackInput:
        self._stable_id(field.key, path)
        if field.label is None:
            message = f"{path}: adapted form field has no label"
            raise LayoutInvariantError(message)
        label_value = resolve_text(field.label, self.request.localization).content
        label = self.plain(label_value, path, capacity=2000)
        hint = (
            None
            if field.description is None
            else self.plain(resolve_text(field.description, self.request.localization).content, path, capacity=2000)
        )
        prefill = spec.prefill_for(field)
        element = self._form_element(field, prefill, path)
        block_id = f"{form_key}:{field.key}"
        if len(block_id) > self.limits.components.block_id:
            digest = hashlib.blake2s(block_id.encode(), digest_size=8).hexdigest()
            block_id = f"form:{digest}"
        return scene.SlackInput(block_id, label, element, optional=not field.required, hint=hint)

    def _form_element(self, field: forms.FormField[Any], prefill: object, path: str) -> scene.SlackInputElement:
        placeholder_value: object = getattr(field, "placeholder", None)
        if placeholder_value is not None and not isinstance(placeholder_value, str | ResolvedText | Message):
            message = f"{path}: form field placeholder is not text"
            raise LayoutInvariantError(message)
        placeholder = (
            None
            if placeholder_value is None
            else self.plain(
                resolve_text(placeholder_value, self.request.localization).content,
                path,
                capacity=self.limits.components.placeholder,
            )
        )
        initial = None if prefill is None else str(prefill)
        if isinstance(field, forms.TextAreaField):
            return scene.SlackTextInput(
                field.key,
                initial,
                placeholder,
                multiline=True,
                minimum_length=field.minimum,
                maximum_length=field.maximum,
            )
        if isinstance(field, forms.TextField | forms.DurationField | forms.DateTimeField | forms.ZonedDateTimeField):
            minimum = field.minimum if isinstance(field, forms.TextField) else None
            maximum = field.maximum if isinstance(field, forms.TextField) else None
            return scene.SlackTextInput(
                field.key,
                initial,
                placeholder,
                minimum_length=minimum,
                maximum_length=maximum,
            )
        if isinstance(field, forms.IntField | forms.FloatField):
            return scene.SlackNumberInput(
                field.key,
                initial,
                decimal_allowed=isinstance(field, forms.FloatField),
                minimum=None if field.minimum is None else str(field.minimum),
                maximum=None if field.maximum is None else str(field.maximum),
            )
        if isinstance(field, forms.DateField):
            return scene.SlackDatePicker(field.key, initial, placeholder)
        if isinstance(field, forms.TimeField):
            return scene.SlackTimePicker(field.key, initial, placeholder)
        if isinstance(field, forms.BoolField):
            option = scene.SlackOption(self.plain("Yes", path, capacity=75), "true")
            selected = ("true",) if bool(prefill) else ()
            return scene.SlackCheckboxes(field.key, (option,), selected)
        if isinstance(field, forms.ScaleField):
            options = tuple(
                scene.SlackOption(
                    self.plain(
                        resolve_text(field.label_for(value), self.request.localization).content,
                        path,
                        capacity=self.limits.components.option_label,
                    ),
                    str(value),
                )
                for value in field.points
            )
            return self._choice_element(field.key, options, initial, multiple=False, path=path)
        if isinstance(field, forms.ChoiceField | forms.MultiChoiceField):
            options = tuple(
                scene.SlackOption(
                    self.plain(
                        resolve_text(option.label, self.request.localization).content,
                        path,
                        capacity=self.limits.components.option_label,
                    ),
                    option.key,
                    None
                    if option.description is None
                    else self.plain(
                        resolve_text(option.description, self.request.localization).content,
                        path,
                        capacity=self.limits.components.option_description,
                    ),
                )
                for option in field.options
            )
            selected = tuple(prefill) if isinstance(prefill, tuple) else (() if prefill is None else (str(prefill),))
            return self._choice_element(
                field.key,
                options,
                selected if isinstance(field, forms.MultiChoiceField) else initial,
                multiple=isinstance(field, forms.MultiChoiceField),
                path=path,
            )
        message = f"{path}: Slack cannot represent form field {type(field).__name__}"
        raise LayoutInvariantError(message)

    def _choice_element(
        self,
        key: str,
        options: tuple[scene.SlackOption, ...],
        initial: str | tuple[str, ...] | None,
        *,
        multiple: bool,
        path: str,
    ) -> scene.SlackInputElement:
        if len(options) > self.limits.components.select_options:
            message = f"{path}: Slack form choices cannot exceed 100 options"
            raise LayoutInvariantError(message)
        if len(options) <= 10:
            if multiple:
                if initial is not None and not isinstance(initial, tuple):
                    message = f"{path}: multi-choice initial value is not a tuple"
                    raise LayoutInvariantError(message)
                selected = initial or ()
                return scene.SlackCheckboxes(key, options, selected)
            if isinstance(initial, tuple):
                message = f"{path}: single-choice initial value is a tuple"
                raise LayoutInvariantError(message)
            return scene.SlackRadioButtons(key, options, initial)
        if multiple:
            if initial is not None and not isinstance(initial, tuple):
                message = f"{path}: multi-choice initial value is not a tuple"
                raise LayoutInvariantError(message)
            selected_values = initial or ()
        else:
            if isinstance(initial, tuple):
                message = f"{path}: single-choice initial value is a tuple"
                raise LayoutInvariantError(message)
            selected_values = () if initial is None else (initial,)
        return scene.SlackSelect(
            action_id=key,
            kind=scene.SlackSelectKind.STATIC,
            options=options,
            initial_values=selected_values,
            minimum=0,
            maximum=len(options) if multiple else 1,
        )


def _action_keys(body: SlackBody) -> set[str]:
    actions: set[str] = set()

    def element(value: scene.SlackElement) -> None:
        if isinstance(value, scene.SlackButton | scene.SlackSelect) and value.action is not None:
            actions.add(value.action.action)

    for block in body.blocks:
        if isinstance(block, scene.SlackActions):
            for item in block.elements:
                element(item)
        elif isinstance(block, scene.SlackSection) and block.accessory is not None:
            element(block.accessory)
        elif isinstance(block, scene.SlackCard):
            for item in block.actions:
                element(item)
        elif isinstance(block, scene.SlackCarousel):
            for card in block.cards:
                for item in card.actions:
                    element(item)
    return actions


class SlackPlanner:
    """Compile portable semantic documents into deterministic Slack scenes."""

    def plan[BodyT: SlackBody, RenderTargetT: SlackTarget, AdapterT](
        self,
        rendered: DocumentLike[RenderTargetT],
        request: PlanRequest[BodyT, RenderTargetT, AdapterT],
        *,
        cache: PlanCache[BodyT] | None = None,
        memo: PlanMemo[BodyT] | None = None,
    ) -> PlanResult[BodyT]:
        target = cast(SlackTargetT, request.target.reserve(request.reservation))
        modal_form = _modal_form(cast(DocumentLike[Any], rendered)) if target.id.endswith(".modal") else None
        key = stable_fingerprint((rendered, request.cache_context(target=target, chrome=request.chrome)))
        if memo is not None and (exact := memo.replay(rendered, key, request.presentation)) is not None:
            return exact
        intermediate_request = replace(cast(Any, request), target=target, reservation=EMPTY_COST)
        intermediate = HTML_PLANNER.plan(
            cast(DocumentLike[Any], rendered),
            cast(Any, intermediate_request),
        )
        lowerer = _Lowerer(target=target, request=cast(Any, request), modal_form=modal_form)
        blocks = lowerer.blocks(intermediate.scene.body.children)
        if target.id.endswith(".message"):
            fallback = lowerer.fit(
                _plain_text("\n".join(_html_text(node) for node in intermediate.scene.body.children)),
                cast(SlackMessageLimits, target.limits).fallback_text,
                "$",
                "fallback_text",
            )
            body: SlackBody = scene.SlackMessage(fallback, blocks)
        elif target.id.endswith(".modal"):
            assert modal_form is not None
            limits = cast(SlackModalLimits, target.limits)
            title = resolve_text(modal_form.spec.title, request.localization).content
            submit = resolve_text(modal_form.label, request.localization).content
            close = resolve_text(request.chrome.cancel, request.localization).content
            body = scene.SlackModalView(
                lowerer.fit(modal_form.key, limits.callback_id, "$.form", "callback_id"),
                lowerer.plain(title, "$.form.title", capacity=limits.title),
                lowerer.plain(submit, "$.form.submit", capacity=limits.submit),
                lowerer.plain(close, "$.form.close", capacity=limits.close),
                blocks,
            )
        else:
            body = scene.SlackHomeView(blocks)
        events = (*intermediate.report.events, *lowerer.events)
        if request.strict and any(event.severity is PlanSeverity.DEGRADATION for event in events):
            raise LayoutDegradedError("; ".join(event.message for event in events))
        planned = scene.Scene(
            scene.Codec.protocol,
            target.id,
            target.version,
            body,
            intermediate.scene.assets,
            intermediate.scene.pagers,
        )
        report = PlanReport(
            events,
            logical_fingerprint=stable_fingerprint((rendered, target.fingerprint)),
            scene_fingerprint=scene.Codec.fingerprint(planned),
        )
        cached = cache.get(key) if cache is not None else None
        if cached is not None:
            planned = cast(scene.Scene[SlackBody], cached.scene)
            report = cached.report
        action_keys = _action_keys(cast(SlackBody, planned.body))
        result = PlanResult(
            scene=cast(scene.Scene[BodyT], planned),
            bindings={key: binding for key, binding in intermediate.bindings.items() if key in action_keys},
            form_bindings=intermediate.form_bindings,
            report=report,
            resources=intermediate.resources,
            metrics=PlanMetrics(
                states_explored=intermediate.metrics.states_explored,
                cache_hit=cached is not None,
                reuse=PlanReuse.STRUCTURAL if cached is not None else PlanReuse.MISS,
            ),
            session_updates=intermediate.session_updates,
        )
        if cache is not None and cached is None:
            cache.put(
                key,
                CachedPlan(
                    cast(scene.Scene[BodyT], planned),
                    report,
                    intermediate.session_updates,
                    states_explored=intermediate.metrics.states_explored,
                ),
            )
        if memo is not None:
            memo.store(rendered, key, request.presentation, result)
        return result


SLACK_PLANNER = SlackPlanner()


__all__ = ["SLACK_PLANNER", "SlackPlanner"]
