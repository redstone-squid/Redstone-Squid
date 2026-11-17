"""Canonical JSON codec for resolved scenes."""

import hashlib
import json
import math
from copy import deepcopy
from typing import Any, cast

from pydantic import ValidationError

from squid_ui.action_mode import ActionMode
from squid_ui.content.emoji import Emoji
from squid_ui.content.entity import ConversationType, EntityKind, EntityRef, EntityType
from squid_ui.content.text import Markup
from squid_ui.errors import SquidUiError
from squid_ui.primitives.styles import ActionStyle
from squid_ui.scene._json import JsonObject, JsonReader
from squid_ui.scene.model import (
    Asset,
    Body,
    Button,
    ClassicMessage,
    ClassicRow,
    ComponentsV2,
    Control,
    Embed,
    EntitySelect,
    Extension,
    File,
    Gallery,
    GalleryItem,
    HtmlActionRef,
    HtmlAssetRef,
    HtmlAttribute,
    HtmlAttributeName,
    HtmlBody,
    HtmlColourRef,
    HtmlElement,
    HtmlFormRef,
    HtmlNode,
    HtmlRouteRef,
    HtmlTag,
    HtmlText,
    HtmlTimeRef,
    HtmlUrlRef,
    Link,
    Node,
    Option,
    Pager,
    Panel,
    PremiumButton,
    RoutedButton,
    RoutedSelect,
    Row,
    Scene,
    Section,
    Select,
    Separator,
    Text,
    Thumbnail,
    Time,
    ZonedTime,
)
from squid_ui.scene.schema import SCHEMA
from squid_ui.scene.slack import SlackHomeView, SlackMessage, SlackModalView
from squid_ui.scene.slack_codec import SlackSceneCodecError, slack_body_from_dict, slack_body_to_dict


class CodecError(SquidUiError, ValueError):
    """A scene payload is malformed or uses an unsupported protocol."""


_json = JsonReader(CodecError)


class Codec:
    """Encode and decode resolved-scene protocol 1 with deterministic JSON output.

    This is the Scene's transport and storage format, before conversion to a target's
    native payload. Action keys and asset declarations survive serialization; Python
    handlers and asset bytes stay in PlanResult. Sorted JSON also gives fingerprint
    a stable input for comparing planned displays.

    Decoding checks supported node shapes and selected model invariants, but does
    not run the published JSON Schema or enforce every target limit. Embeds
    reject unknown fields; other node decoders ignore them. Use `schema()` with
    a JSON Schema validator to check the full structural contract.

    Classic rows containing `EntitySelect` can be encoded but are currently
    rejected during decoding, so those scenes cannot round-trip through this codec.
    """

    protocol = 1

    @classmethod
    def schema(cls) -> dict[str, Any]:
        """Return an isolated JSON Schema for cross-language scene consumers."""
        return deepcopy(SCHEMA)

    @classmethod
    def schema_json(cls, *, indent: int | None = None) -> str:
        """Return the scene schema in deterministic JSON form."""
        if indent is None:
            return json.dumps(cls.schema(), sort_keys=True, separators=(",", ":"))
        return json.dumps(cls.schema(), indent=indent, sort_keys=True)

    @classmethod
    def dumps(cls, scene: Scene[Any]) -> str:
        """Serialize a scene as compact JSON with sorted keys and unescaped Unicode."""
        return json.dumps(cls.to_dict(scene), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def loads(cls, payload: str) -> Scene:
        """Parse JSON and decode its scene, rejecting a non-object root.

        Shape and protocol errors raise `CodecError`. Invalid enum values or model
        invariants can also raise their constructors' `ValueError` or `TypeError`.
        """
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as error:
            raise CodecError(str(error)) from error
        if not isinstance(raw, dict):
            msg = "scene must be a JSON object"
            raise CodecError(msg)
        return cls.from_dict(raw)

    @classmethod
    def fingerprint(cls, scene: Scene[Any]) -> str:
        """Return the 32-character hexadecimal BLAKE2s digest of the canonical scene JSON.

        Asset declarations and pager state contribute to the digest; asset bytes
        and process-local bindings are not part of the scene.
        """
        return hashlib.blake2s(cls.dumps(scene).encode(), digest_size=16).hexdigest()

    @classmethod
    def to_dict(cls, scene: Scene[Any]) -> dict[str, Any]:
        """Encode protocol 1 into plain containers without validating target limits."""
        if scene.protocol != cls.protocol:
            msg = f"unsupported scene protocol {scene.protocol}"
            raise CodecError(msg)
        return {
            "protocol": scene.protocol,
            "target": scene.target,
            "target_version": scene.target_version,
            "body": _body_to_dict(scene.body),
            "assets": [
                {"key": asset.key, "name": asset.name, "media_type": asset.media_type} for asset in scene.assets
            ],
            "pagers": [
                {
                    "key": pager.key,
                    "page": pager.page,
                    "pages": pager.pages,
                    "content_fingerprint": pager.content_fingerprint,
                }
                for pager in scene.pagers
            ],
        }

    @classmethod
    def from_dict(cls, raw: JsonObject) -> Scene:
        """Decode supported shapes; omitted asset and pager arrays default to empty.

        Nullable fields generally accept omission as `None`. The `target` name and
        version are carried through without selecting or validating a renderer.
        """
        protocol = _json.integer(raw, "protocol")
        if protocol != cls.protocol:
            msg = f"unsupported scene protocol {protocol}"
            raise CodecError(msg)
        assets = raw.get("assets", [])
        pagers = raw.get("pagers", [])
        if not isinstance(assets, list) or not isinstance(pagers, list):
            msg = "scene assets and pagers must be arrays"
            raise CodecError(msg)
        return Scene(
            protocol=protocol,
            target=_json.string(raw, "target"),
            target_version=_json.integer(raw, "target_version"),
            body=_body_from_dict(_json.require_object(raw.get("body"))),
            assets=tuple(
                Asset(
                    key=_json.string(_json.require_object(asset), "key"),
                    name=_json.string(_json.require_object(asset), "name"),
                    media_type=_json.string(_json.require_object(asset), "media_type"),
                )
                for asset in assets
            ),
            pagers=tuple(
                Pager(
                    key=_json.string(_json.require_object(pager), "key"),
                    page=_json.integer(_json.require_object(pager), "page"),
                    pages=_json.integer(_json.require_object(pager), "pages"),
                    content_fingerprint=_json.string(_json.require_object(pager), "content_fingerprint"),
                )
                for pager in pagers
            ),
        )


def _body_to_dict(body: Body) -> dict[str, Any]:
    match body:
        case ComponentsV2(children=children):
            return {"kind": ComponentsV2.KIND, "children": [_node_to_dict(child) for child in children]}
        case ClassicMessage(content=content, embeds=embeds, rows=rows):
            return {
                "kind": ClassicMessage.KIND,
                "content": content,
                "embeds": [_embed_to_dict(embed) for embed in embeds],
                "rows": [{"controls": [_node_to_dict(control) for control in row.controls]} for row in rows],
            }
        case HtmlBody(children=children, locale=locale):
            return {
                "kind": HtmlBody.KIND,
                "children": [_html_node_to_dict(child) for child in children],
                "locale": locale,
            }
        case SlackMessage() | SlackModalView() | SlackHomeView():
            return slack_body_to_dict(body)


def _body_from_dict(raw: JsonObject) -> Body:
    kind = _json.string(raw, "kind")
    match kind:
        case ComponentsV2.KIND:
            children = raw.get("children")
            if not isinstance(children, list):
                msg = "components_v2 children must be an array"
                raise CodecError(msg)
            return ComponentsV2(tuple(_node_from_dict(_json.require_object(child)) for child in children))
        case ClassicMessage.KIND:
            embeds = raw.get("embeds")
            rows = raw.get("rows")
            if not isinstance(embeds, list) or not isinstance(rows, list):
                msg = "classic_message embeds and rows must be arrays"
                raise CodecError(msg)
            return ClassicMessage(
                content=_json.optional_string(raw, "content"),
                embeds=tuple(_embed_from_dict(_json.require_object(embed)) for embed in embeds),
                rows=tuple(_row_from_dict(_json.require_object(row)) for row in rows),
            )
        case HtmlBody.KIND:
            children = raw.get("children")
            if not isinstance(children, list):
                msg = "HTML body children must be an array"
                raise CodecError(msg)
            return HtmlBody(
                tuple(_html_node_from_dict(_json.require_object(child)) for child in children),
                locale=_json.optional_string(raw, "locale"),
            )
        case SlackMessage.KIND | SlackModalView.KIND | SlackHomeView.KIND:
            try:
                return slack_body_from_dict(raw)
            except SlackSceneCodecError as error:
                raise CodecError(str(error)) from error
        case _:
            msg = f"unknown scene body kind {kind!r}"
            raise CodecError(msg)


def _html_node_to_dict(node: HtmlNode) -> dict[str, Any]:
    match node:
        case HtmlText(content=content, markup=markup):
            return {"kind": HtmlText.KIND, "content": content, "markup": markup.value}
        case HtmlElement(
            tag=tag,
            children=children,
            attributes=attributes,
            action=action,
            route=route,
            form=form,
            url=url,
            time=time,
            colour=colour,
            asset=asset,
        ):
            return {
                "kind": HtmlElement.KIND,
                "tag": tag.value,
                "children": [_html_node_to_dict(child) for child in children],
                "attributes": [{"name": attribute.name.value, "value": attribute.value} for attribute in attributes],
                "action": None if action is None else {"action": action.action, "mode": action.mode.value},
                "route": None if route is None else {"route_id": route.route_id},
                "form": None if form is None else {"key": form.key, "field_name": form.field_name},
                "url": None if url is None else {"url": url.url},
                "time": (
                    None if time is None else {"instant": time.instant, "timezone": time.timezone, "style": time.style}
                ),
                "colour": None if colour is None else {"value": colour.value},
                "asset": (
                    None if asset is None else {"key": asset.key, "name": asset.name, "media_type": asset.media_type}
                ),
            }


def _html_node_from_dict(raw: JsonObject) -> HtmlNode:
    kind = _json.string(raw, "kind")
    if kind == HtmlText.KIND:
        return HtmlText(_json.string(raw, "content"), Markup(_json.string(raw, "markup")))
    if kind != HtmlElement.KIND:
        msg = f"unknown HTML scene node kind {kind!r}"
        raise CodecError(msg)
    children = raw.get("children")
    attributes = raw.get("attributes")
    if not isinstance(children, list) or not isinstance(attributes, list):
        msg = "HTML element children and attributes must be arrays"
        raise CodecError(msg)

    def optional_ref(key: str) -> JsonObject | None:
        value = raw.get(key)
        return None if value is None else _json.require_object(value)

    action = optional_ref("action")
    route = optional_ref("route")
    form = optional_ref("form")
    url = optional_ref("url")
    time = optional_ref("time")
    colour = optional_ref("colour")
    asset = optional_ref("asset")
    return HtmlElement(
        tag=HtmlTag(_json.string(raw, "tag")),
        children=tuple(_html_node_from_dict(_json.require_object(child)) for child in children),
        attributes=tuple(
            HtmlAttribute(
                HtmlAttributeName(_json.string(_json.require_object(attribute), "name")),
                _html_attribute_value(_json.require_object(attribute).get("value")),
            )
            for attribute in attributes
        ),
        action=(
            None
            if action is None
            else HtmlActionRef(_json.string(action, "action"), ActionMode(_json.string(action, "mode")))
        ),
        route=None if route is None else HtmlRouteRef(_json.string(route, "route_id")),
        form=None
        if form is None
        else HtmlFormRef(_json.string(form, "key"), _json.optional_string(form, "field_name")),
        url=None if url is None else HtmlUrlRef(_json.string(url, "url")),
        time=(
            None
            if time is None
            else HtmlTimeRef(
                _json.string(time, "instant"),
                timezone=_json.optional_string(time, "timezone"),
                style=_json.optional_string(time, "style"),
            )
        ),
        colour=None if colour is None else HtmlColourRef(_json.integer(colour, "value")),
        asset=(
            None
            if asset is None
            else HtmlAssetRef(
                _json.string(asset, "key"), _json.string(asset, "name"), _json.string(asset, "media_type")
            )
        ),
    )


def _html_attribute_value(value: object) -> str | int | float | bool:
    if not isinstance(value, str | int | float | bool) or (isinstance(value, float) and not math.isfinite(value)):
        msg = "HTML attribute value must be a finite string, number, or boolean"
        raise CodecError(msg)
    return value


def _row_from_dict(raw: JsonObject) -> ClassicRow:
    controls = raw.get("controls")
    if not isinstance(controls, list):
        msg = "classic row controls must be an array"
        raise CodecError(msg)
    decoded = tuple(_node_from_dict(_json.require_object(control)) for control in controls)
    if not all(
        isinstance(
            control,
            Link | PremiumButton | Button | RoutedButton | Select | RoutedSelect | Extension,
        )
        for control in decoded
    ):
        msg = "classic row contains an unsupported control"
        raise CodecError(msg)
    return ClassicRow(cast(tuple[Control, ...], decoded))


def _embed_to_dict(embed: Embed) -> dict[str, Any]:
    return embed.model_dump(mode="json")


def _embed_from_dict(raw: JsonObject) -> Embed:
    try:
        return Embed.model_validate(raw)
    except ValidationError as error:
        raise CodecError(str(error)) from error


def _node_to_dict(node: Node | Link | PremiumButton | Button | RoutedButton) -> dict[str, Any]:
    match node:
        case Text(content=content, markup=markup):
            return {"kind": Text.KIND, "content": content, "markup": markup.value}
        case Time(instant=instant, style=style, prefix=prefix):
            return {"kind": Time.KIND, "instant": instant, "style": style, "prefix": prefix}
        case ZonedTime(instant=instant, timezone=timezone, prefix=prefix):
            return {"kind": ZonedTime.KIND, "instant": instant, "timezone": timezone, "prefix": prefix}
        case File(asset_key=asset_key, name=name, media_type=media_type, spoiler=spoiler):
            return {
                "kind": File.KIND,
                "asset_key": asset_key,
                "name": name,
                "media_type": media_type,
                "spoiler": spoiler,
            }
        case Separator(large=large, visible=visible):
            return {"kind": Separator.KIND, "large": large, "visible": visible}
        case Link(label=label, url=url, emoji=emoji, disabled=disabled):
            return {
                "kind": Link.KIND,
                "label": label,
                "url": url,
                "emoji": _emoji_to_dict(emoji),
                "disabled": disabled,
            }
        case PremiumButton(sku_id=sku_id):
            return {"kind": PremiumButton.KIND, "sku_id": sku_id}
        case Button(label=label, action=action, style=style, emoji=emoji, disabled=disabled, mode=mode):
            return {
                "kind": Button.KIND,
                "label": label,
                "action": action,
                "style": style.value,
                "emoji": _emoji_to_dict(emoji),
                "disabled": disabled,
                "mode": mode.value,
            }
        case RoutedButton(label=label, route_id=route_id, style=style, emoji=emoji, disabled=disabled):
            return {
                "kind": RoutedButton.KIND,
                "label": label,
                "route_id": route_id,
                "style": style.value,
                "emoji": _emoji_to_dict(emoji),
                "disabled": disabled,
            }
        case Select(
            options=options,
            action=action,
            placeholder=placeholder,
            min_values=min_values,
            max_values=max_values,
            disabled=disabled,
            mode=mode,
        ):
            return {
                "kind": Select.KIND,
                "options": [
                    {
                        "label": option.label,
                        "value": option.value,
                        "description": option.description,
                        "default": option.default,
                        "emoji": _emoji_to_dict(option.emoji),
                    }
                    for option in options
                ],
                "action": action,
                "placeholder": placeholder,
                "min_values": min_values,
                "max_values": max_values,
                "disabled": disabled,
                "mode": mode.value,
            }
        case RoutedSelect(
            options=options,
            route_id=route_id,
            placeholder=placeholder,
            min_values=min_values,
            max_values=max_values,
            disabled=disabled,
        ):
            return {
                "kind": RoutedSelect.KIND,
                "options": [
                    {
                        "label": option.label,
                        "value": option.value,
                        "description": option.description,
                        "default": option.default,
                        "emoji": _emoji_to_dict(option.emoji),
                    }
                    for option in options
                ],
                "route_id": route_id,
                "placeholder": placeholder,
                "min_values": min_values,
                "max_values": max_values,
                "disabled": disabled,
            }
        case EntitySelect(
            entity_type=entity_type,
            action=action,
            placeholder=placeholder,
            default_values=default_values,
            conversation_types=conversation_types,
            min_values=min_values,
            max_values=max_values,
            disabled=disabled,
            mode=mode,
        ):
            return {
                "kind": EntitySelect.KIND,
                "entity_type": entity_type.value,
                "action": action,
                "placeholder": placeholder,
                "default_values": [{"kind": value.kind.value, "id": value.id} for value in default_values],
                "conversation_types": [value.value for value in conversation_types],
                "min_values": min_values,
                "max_values": max_values,
                "disabled": disabled,
                "mode": mode.value,
            }
        case Row(items=items):
            return {"kind": Row.KIND, "items": [_node_to_dict(item) for item in items]}
        case Thumbnail(url=url, description=description, spoiler=spoiler):
            return {"kind": Thumbnail.KIND, "url": url, "description": description, "spoiler": spoiler}
        case Gallery(items=items):
            return {
                "kind": Gallery.KIND,
                "items": [
                    {
                        "url": item.url,
                        "description": item.description,
                        "spoiler": item.spoiler,
                        "asset_name": item.asset_name,
                    }
                    for item in items
                ],
            }
        case Section(texts=texts, accessory=accessory):
            return {
                "kind": Section.KIND,
                "texts": [_node_to_dict(text) for text in texts],
                "accessory": _node_to_dict(accessory),
            }
        case Panel(children=children, accent=accent, spoiler=spoiler):
            return {
                "kind": Panel.KIND,
                "children": [_node_to_dict(child) for child in children],
                "accent": accent,
                "spoiler": spoiler,
            }
        case Extension(kind=kind, version=version, payload=payload):
            # Round-trip through json now so errors point at planning rather than a remote renderer.
            try:
                normalized = json.loads(json.dumps(payload, ensure_ascii=False))
            except (TypeError, ValueError) as error:
                msg = f"extension {kind!r} payload is not JSON serializable"
                raise CodecError(msg) from error
            return {"kind": Extension.KIND, "extension": kind, "version": version, "payload": normalized}


def _node_from_dict(
    raw: JsonObject,
) -> Node | Link | PremiumButton | Button | RoutedButton:
    kind = _json.string(raw, "kind")
    match kind:
        case Text.KIND:
            return Text(_json.string(raw, "content"), Markup(_json.string(raw, "markup")))
        case Time.KIND:
            return Time(
                _json.string(raw, "instant"),
                _json.string(raw, "style"),
                _json.optional_string(raw, "prefix"),
            )
        case ZonedTime.KIND:
            return ZonedTime(
                _json.string(raw, "instant"),
                _json.string(raw, "timezone"),
                _json.optional_string(raw, "prefix"),
            )
        case File.KIND:
            return File(
                _json.string(raw, "asset_key"),
                _json.string(raw, "name"),
                _json.string(raw, "media_type"),
                _json.boolean(raw, "spoiler", default=False),
            )
        case Separator.KIND:
            return Separator(large=_json.boolean(raw, "large"), visible=_json.boolean(raw, "visible"))
        case Link.KIND:
            return Link(
                label=_json.optional_string(raw, "label"),
                url=_json.string(raw, "url"),
                emoji=_emoji_from_value(raw.get("emoji")),
                disabled=_json.boolean(raw, "disabled", default=False),
            )
        case PremiumButton.KIND:
            return PremiumButton(_json.integer(raw, "sku_id"))
        case Button.KIND:
            return Button(
                label=_json.optional_string(raw, "label"),
                action=_json.string(raw, "action"),
                style=ActionStyle(_json.string(raw, "style")),
                emoji=_emoji_from_value(raw.get("emoji")),
                disabled=_json.boolean(raw, "disabled"),
                mode=ActionMode(_json.string(raw, "mode")),
            )
        case RoutedButton.KIND:
            return RoutedButton(
                label=_json.optional_string(raw, "label"),
                route_id=_json.string(raw, "route_id"),
                style=ActionStyle(_json.string(raw, "style")),
                emoji=_emoji_from_value(raw.get("emoji")),
                disabled=_json.boolean(raw, "disabled"),
            )
        case Select.KIND:
            options = raw.get("options")
            if not isinstance(options, list):
                msg = "select options must be an array"
                raise CodecError(msg)
            return Select(
                options=tuple(
                    Option(
                        label=_json.string(_json.require_object(option), "label"),
                        value=_json.string(_json.require_object(option), "value"),
                        description=_json.optional_string(_json.require_object(option), "description"),
                        default=_json.boolean(_json.require_object(option), "default"),
                        emoji=_emoji_from_value(_json.require_object(option).get("emoji")),
                    )
                    for option in options
                ),
                action=_json.string(raw, "action"),
                placeholder=_json.optional_string(raw, "placeholder"),
                min_values=_json.integer(raw, "min_values"),
                max_values=_json.integer(raw, "max_values"),
                disabled=_json.boolean(raw, "disabled"),
                mode=ActionMode(_json.string(raw, "mode")),
            )
        case RoutedSelect.KIND:
            options = raw.get("options")
            if not isinstance(options, list):
                msg = "routed select options must be an array"
                raise CodecError(msg)
            return RoutedSelect(
                options=tuple(
                    Option(
                        label=_json.string(_json.require_object(option), "label"),
                        value=_json.string(_json.require_object(option), "value"),
                        description=_json.optional_string(_json.require_object(option), "description"),
                        default=_json.boolean(_json.require_object(option), "default"),
                        emoji=_emoji_from_value(_json.require_object(option).get("emoji")),
                    )
                    for option in options
                ),
                route_id=_json.string(raw, "route_id"),
                placeholder=_json.optional_string(raw, "placeholder"),
                min_values=_json.integer(raw, "min_values"),
                max_values=_json.integer(raw, "max_values"),
                disabled=_json.boolean(raw, "disabled"),
            )
        case EntitySelect.KIND:
            defaults = raw.get("default_values")
            if not isinstance(defaults, list):
                msg = "entity select default_values must be an array"
                raise CodecError(msg)
            return EntitySelect(
                entity_type=EntityType(_json.string(raw, "entity_type")),
                action=_json.string(raw, "action"),
                placeholder=_json.optional_string(raw, "placeholder"),
                default_values=tuple(
                    EntityRef(
                        EntityKind(_json.string(_json.require_object(value), "kind")),
                        _entity_id(_json.require_object(value)),
                    )
                    for value in defaults
                ),
                conversation_types=tuple(
                    ConversationType(value) for value in _json.string_array(raw, "conversation_types")
                ),
                min_values=_json.integer(raw, "min_values"),
                max_values=_json.integer(raw, "max_values"),
                disabled=_json.boolean(raw, "disabled"),
                mode=ActionMode(_json.string(raw, "mode")),
            )
        case Row.KIND:
            items = raw.get("items")
            if not isinstance(items, list):
                msg = "row items must be an array"
                raise CodecError(msg)
            decoded = tuple(_node_from_dict(_json.require_object(item)) for item in items)
            if not all(isinstance(item, Link | PremiumButton | Button | RoutedButton | Extension) for item in decoded):
                msg = "row contains an unsupported child"
                raise CodecError(msg)
            return Row(cast(tuple[Link | PremiumButton | Button | RoutedButton | Extension, ...], decoded))
        case Thumbnail.KIND:
            return Thumbnail(
                url=_json.string(raw, "url"),
                description=_json.optional_string(raw, "description"),
                spoiler=_json.boolean(raw, "spoiler", default=False),
            )
        case Gallery.KIND:
            items = raw.get("items")
            if not isinstance(items, list):
                msg = "gallery items must be an array"
                raise CodecError(msg)
            return Gallery(
                tuple(
                    GalleryItem(
                        url=_json.string(_json.require_object(item), "url"),
                        description=_json.optional_string(_json.require_object(item), "description"),
                        spoiler=_json.boolean(_json.require_object(item), "spoiler", default=False),
                        asset_name=_json.optional_string(_json.require_object(item), "asset_name"),
                    )
                    for item in items
                )
            )
        case Section.KIND:
            texts = raw.get("texts")
            if not isinstance(texts, list):
                msg = "section texts must be an array"
                raise CodecError(msg)
            decoded_texts = tuple(_node_from_dict(_json.require_object(text)) for text in texts)
            accessory = _node_from_dict(_json.require_object(raw.get("accessory")))
            if not all(isinstance(text, Text) for text in decoded_texts):
                msg = "section contains a non-text slot"
                raise CodecError(msg)
            if not isinstance(
                accessory,
                Thumbnail | Link | PremiumButton | Button | RoutedButton | Extension,
            ):
                msg = "section has an unsupported accessory"
                raise CodecError(msg)
            return Section(cast(tuple[Text, ...], decoded_texts), accessory)
        case Panel.KIND:
            children = raw.get("children")
            accent = raw.get("accent")
            if not isinstance(children, list) or not (accent is None or isinstance(accent, int)):
                msg = "panel children or accent is malformed"
                raise CodecError(msg)
            return Panel(
                tuple(_node_from_dict(_json.require_object(child)) for child in children),
                accent=accent,
                spoiler=_json.boolean(raw, "spoiler", default=False),
            )
        case Extension.KIND:
            payload = raw.get("payload")
            if not isinstance(payload, dict):
                msg = "extension payload must be an object"
                raise CodecError(msg)
            return Extension(
                kind=_json.string(raw, "extension"), version=_json.integer(raw, "version"), payload=payload
            )
        case _:
            msg = f"unknown scene node kind {kind!r}"
            raise CodecError(msg)


def _entity_id(raw: JsonObject) -> int | str:
    value = raw.get("id")
    if isinstance(value, bool) or not isinstance(value, int | str):
        message = "id must be an integer or string"
        raise CodecError(message)
    return value


def _emoji_to_dict(emoji: Emoji | None) -> dict[str, object] | None:
    if emoji is None:
        return None
    return {"name": emoji.name, "id": emoji.id, "animated": emoji.animated}


def _emoji_from_value(value: object) -> Emoji | None:
    if value is None:
        return None
    if isinstance(value, str):
        return Emoji(value)
    raw = _json.require_object(value)
    emoji_id = raw.get("id")
    if emoji_id is not None and (not isinstance(emoji_id, int) or isinstance(emoji_id, bool)):
        message = "emoji id must be an integer or null"
        raise CodecError(message)
    return Emoji(
        name=_json.string(raw, "name"),
        id=emoji_id,
        animated=_json.boolean(raw, "animated", default=False),
    )
