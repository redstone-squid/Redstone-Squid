"""Canonical JSON codec for resolved scenes."""

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, cast

from squid_layouts.actions import ActionPolicy
from squid_layouts.entities import ChannelType, EntityKind, EntityRef, EntityType
from squid_layouts.primitives.styles import ActionStyle
from squid_layouts.scene.model import (
    SceneAsset,
    SceneBody,
    SceneButton,
    SceneClassicMessage,
    SceneClassicRow,
    SceneComponentsV2,
    SceneControl,
    SceneDocument,
    SceneEmbed,
    SceneEmbedAuthor,
    SceneEmbedField,
    SceneEmbedFooter,
    SceneEmbedMedia,
    SceneEntitySelect,
    SceneExtension,
    SceneFile,
    SceneGallery,
    SceneGalleryItem,
    SceneLink,
    SceneNode,
    SceneOption,
    ScenePager,
    ScenePanel,
    SceneRoutedButton,
    SceneRoutedSelect,
    SceneRow,
    SceneSection,
    SceneSelect,
    SceneSeparator,
    SceneText,
    SceneThumbnail,
    SceneTime,
    SceneZonedTime,
)
from squid_layouts.scene.schema import SCENE_SCHEMA
from squid_layouts.text import TextDialect


class SceneCodecError(ValueError):
    """A scene payload is malformed or uses an unsupported protocol."""


class SceneCodec:
    """Encode and decode deterministic resolved-scene protocol 1."""

    protocol = 1

    @classmethod
    def schema(cls) -> dict[str, Any]:
        """Return an isolated JSON Schema for cross-language scene consumers."""
        return deepcopy(SCENE_SCHEMA)

    @classmethod
    def schema_json(cls) -> str:
        """Return the scene schema in deterministic JSON form."""
        return json.dumps(cls.schema(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def dumps(cls, scene: SceneDocument) -> str:
        return json.dumps(cls.to_dict(scene), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def loads(cls, payload: str) -> SceneDocument:
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as error:
            raise SceneCodecError(str(error)) from error
        if not isinstance(raw, dict):
            msg = "scene must be a JSON object"
            raise SceneCodecError(msg)
        return cls.from_dict(raw)

    @classmethod
    def fingerprint(cls, scene: SceneDocument) -> str:
        return hashlib.blake2s(cls.dumps(scene).encode(), digest_size=16).hexdigest()

    @classmethod
    def to_dict(cls, scene: SceneDocument) -> dict[str, Any]:
        if scene.protocol != cls.protocol:
            msg = f"unsupported scene protocol {scene.protocol}"
            raise SceneCodecError(msg)
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
    def from_dict(cls, raw: Mapping[str, Any]) -> SceneDocument:
        protocol = _integer(raw, "protocol")
        if protocol != cls.protocol:
            msg = f"unsupported scene protocol {protocol}"
            raise SceneCodecError(msg)
        assets = raw.get("assets", [])
        pagers = raw.get("pagers", [])
        if not isinstance(assets, list) or not isinstance(pagers, list):
            msg = "scene assets and pagers must be arrays"
            raise SceneCodecError(msg)
        return SceneDocument(
            protocol=protocol,
            target=_string(raw, "target"),
            target_version=_integer(raw, "target_version"),
            body=_body_from_dict(_object(raw.get("body"))),
            assets=tuple(
                SceneAsset(
                    key=_string(_object(asset), "key"),
                    name=_string(_object(asset), "name"),
                    media_type=_string(_object(asset), "media_type"),
                )
                for asset in assets
            ),
            pagers=tuple(
                ScenePager(
                    key=_string(_object(pager), "key"),
                    page=_integer(_object(pager), "page"),
                    pages=_integer(_object(pager), "pages"),
                    content_fingerprint=_string(_object(pager), "content_fingerprint"),
                )
                for pager in pagers
            ),
        )


def _body_to_dict(body: SceneBody) -> dict[str, Any]:
    match body:
        case SceneComponentsV2(children=children):
            return {"kind": "components_v2", "children": [_node_to_dict(child) for child in children]}
        case SceneClassicMessage(content=content, embeds=embeds, rows=rows):
            return {
                "kind": "classic_message",
                "content": content,
                "embeds": [_embed_to_dict(embed) for embed in embeds],
                "rows": [{"controls": [_node_to_dict(control) for control in row.controls]} for row in rows],
            }


def _body_from_dict(raw: Mapping[str, Any]) -> SceneBody:
    kind = _string(raw, "kind")
    match kind:
        case "components_v2":
            children = raw.get("children")
            if not isinstance(children, list):
                msg = "components_v2 children must be an array"
                raise SceneCodecError(msg)
            return SceneComponentsV2(tuple(_node_from_dict(_object(child)) for child in children))
        case "classic_message":
            embeds = raw.get("embeds")
            rows = raw.get("rows")
            if not isinstance(embeds, list) or not isinstance(rows, list):
                msg = "classic_message embeds and rows must be arrays"
                raise SceneCodecError(msg)
            return SceneClassicMessage(
                content=_optional_string(raw, "content"),
                embeds=tuple(_embed_from_dict(_object(embed)) for embed in embeds),
                rows=tuple(_row_from_dict(_object(row)) for row in rows),
            )
        case _:
            msg = f"unknown scene body kind {kind!r}"
            raise SceneCodecError(msg)


def _row_from_dict(raw: Mapping[str, Any]) -> SceneClassicRow:
    controls = raw.get("controls")
    if not isinstance(controls, list):
        msg = "classic row controls must be an array"
        raise SceneCodecError(msg)
    decoded = tuple(_node_from_dict(_object(control)) for control in controls)
    if not all(
        isinstance(
            control, SceneLink | SceneButton | SceneRoutedButton | SceneSelect | SceneRoutedSelect | SceneExtension
        )
        for control in decoded
    ):
        msg = "classic row contains an unsupported control"
        raise SceneCodecError(msg)
    return SceneClassicRow(cast(tuple[SceneControl, ...], decoded))


def _embed_to_dict(embed: SceneEmbed) -> dict[str, Any]:
    return {
        "title": embed.title,
        "url": embed.url,
        "description": embed.description,
        "fields": [{"name": field.name, "value": field.value, "inline": field.inline} for field in embed.fields],
        "footer": None if embed.footer is None else {"text": embed.footer.text, "icon_url": embed.footer.icon_url},
        "author": (
            None
            if embed.author is None
            else {"name": embed.author.name, "url": embed.author.url, "icon_url": embed.author.icon_url}
        ),
        "colour": embed.colour,
        "image": _media_to_dict(embed.image),
        "thumbnail": _media_to_dict(embed.thumbnail),
        "timestamp": embed.timestamp,
    }


def _media_to_dict(media: SceneEmbedMedia | None) -> dict[str, Any] | None:
    return None if media is None else {"url": media.url, "description": media.description}


def _embed_from_dict(raw: Mapping[str, Any]) -> SceneEmbed:
    fields = raw.get("fields")
    if not isinstance(fields, list):
        msg = "embed fields must be an array"
        raise SceneCodecError(msg)
    colour = raw.get("colour")
    if not (colour is None or (isinstance(colour, int) and not isinstance(colour, bool))):
        msg = "embed colour must be an integer or null"
        raise SceneCodecError(msg)
    footer = raw.get("footer")
    author = raw.get("author")
    return SceneEmbed(
        title=_optional_string(raw, "title"),
        url=_optional_string(raw, "url"),
        description=_optional_string(raw, "description"),
        fields=tuple(
            SceneEmbedField(
                name=_string(_object(field), "name"),
                value=_string(_object(field), "value"),
                inline=_boolean(_object(field), "inline"),
            )
            for field in fields
        ),
        footer=(
            None
            if footer is None
            else SceneEmbedFooter(
                text=_string(_object(footer), "text"), icon_url=_optional_string(_object(footer), "icon_url")
            )
        ),
        author=(
            None
            if author is None
            else SceneEmbedAuthor(
                name=_string(_object(author), "name"),
                url=_optional_string(_object(author), "url"),
                icon_url=_optional_string(_object(author), "icon_url"),
            )
        ),
        colour=colour,
        image=_media_from_dict(raw.get("image")),
        thumbnail=_media_from_dict(raw.get("thumbnail")),
        timestamp=_optional_string(raw, "timestamp"),
    )


def _media_from_dict(raw: Any) -> SceneEmbedMedia | None:
    if raw is None:
        return None
    return SceneEmbedMedia(url=_string(_object(raw), "url"), description=_optional_string(_object(raw), "description"))


def _node_to_dict(node: SceneNode | SceneLink | SceneButton | SceneRoutedButton) -> dict[str, Any]:
    match node:
        case SceneText(content=content, dialect=dialect):
            return {"kind": "text", "content": content, "dialect": dialect.value}
        case SceneTime(instant=instant, style=style, prefix=prefix):
            return {"kind": "time", "instant": instant, "style": style, "prefix": prefix}
        case SceneZonedTime(instant=instant, timezone=timezone, prefix=prefix):
            return {"kind": "zoned_time", "instant": instant, "timezone": timezone, "prefix": prefix}
        case SceneFile(asset_key=asset_key, name=name, media_type=media_type):
            return {"kind": "file", "asset_key": asset_key, "name": name, "media_type": media_type}
        case SceneSeparator(large=large, visible=visible):
            return {"kind": "separator", "large": large, "visible": visible}
        case SceneLink(label=label, url=url):
            return {"kind": "link", "label": label, "url": url}
        case SceneButton(label=label, action=action, style=style, emoji=emoji, disabled=disabled, policy=policy):
            return {
                "kind": "button",
                "label": label,
                "action": action,
                "style": style.value,
                "emoji": emoji,
                "disabled": disabled,
                "policy": policy.value,
            }
        case SceneRoutedButton(label=label, route_id=route_id, style=style, emoji=emoji, disabled=disabled):
            return {
                "kind": "routed_button",
                "label": label,
                "route_id": route_id,
                "style": style.value,
                "emoji": emoji,
                "disabled": disabled,
            }
        case SceneSelect(
            options=options,
            action=action,
            placeholder=placeholder,
            min_values=min_values,
            max_values=max_values,
            disabled=disabled,
            policy=policy,
        ):
            return {
                "kind": "select",
                "options": [
                    {
                        "label": option.label,
                        "value": option.value,
                        "description": option.description,
                        "default": option.default,
                    }
                    for option in options
                ],
                "action": action,
                "placeholder": placeholder,
                "min_values": min_values,
                "max_values": max_values,
                "disabled": disabled,
                "policy": policy.value,
            }
        case SceneRoutedSelect(
            options=options,
            route_id=route_id,
            placeholder=placeholder,
            min_values=min_values,
            max_values=max_values,
            disabled=disabled,
        ):
            return {
                "kind": "routed_select",
                "options": [
                    {
                        "label": option.label,
                        "value": option.value,
                        "description": option.description,
                        "default": option.default,
                    }
                    for option in options
                ],
                "route_id": route_id,
                "placeholder": placeholder,
                "min_values": min_values,
                "max_values": max_values,
                "disabled": disabled,
            }
        case SceneEntitySelect(
            entity_type=entity_type,
            action=action,
            placeholder=placeholder,
            default_values=default_values,
            channel_types=channel_types,
            min_values=min_values,
            max_values=max_values,
            disabled=disabled,
            policy=policy,
        ):
            return {
                "kind": "entity_select",
                "entity_type": entity_type.value,
                "action": action,
                "placeholder": placeholder,
                "default_values": [{"kind": value.kind.value, "id": value.id} for value in default_values],
                "channel_types": [value.value for value in channel_types],
                "min_values": min_values,
                "max_values": max_values,
                "disabled": disabled,
                "policy": policy.value,
            }
        case SceneRow(items=items):
            return {"kind": "row", "items": [_node_to_dict(item) for item in items]}
        case SceneThumbnail(url=url, description=description):
            return {"kind": "thumbnail", "url": url, "description": description}
        case SceneGallery(items=items):
            return {
                "kind": "gallery",
                "items": [{"url": item.url, "description": item.description} for item in items],
            }
        case SceneSection(texts=texts, accessory=accessory):
            return {
                "kind": "section",
                "texts": [_node_to_dict(text) for text in texts],
                "accessory": _node_to_dict(accessory),
            }
        case ScenePanel(children=children, accent=accent):
            return {"kind": "panel", "children": [_node_to_dict(child) for child in children], "accent": accent}
        case SceneExtension(kind=kind, version=version, payload=payload):
            # Round-trip through json now so errors point at planning rather than a remote renderer.
            try:
                normalized = json.loads(json.dumps(payload, ensure_ascii=False))
            except (TypeError, ValueError) as error:
                msg = f"extension {kind!r} payload is not JSON serializable"
                raise SceneCodecError(msg) from error
            return {"kind": "extension", "extension": kind, "version": version, "payload": normalized}


def _node_from_dict(raw: Mapping[str, Any]) -> SceneNode | SceneLink | SceneButton | SceneRoutedButton:
    kind = _string(raw, "kind")
    match kind:
        case "text":
            return SceneText(_string(raw, "content"), TextDialect(_string(raw, "dialect")))
        case "time":
            return SceneTime(
                _string(raw, "instant"),
                _string(raw, "style"),
                _optional_string(raw, "prefix"),
            )
        case "zoned_time":
            return SceneZonedTime(
                _string(raw, "instant"),
                _string(raw, "timezone"),
                _optional_string(raw, "prefix"),
            )
        case "file":
            return SceneFile(
                _string(raw, "asset_key"),
                _string(raw, "name"),
                _string(raw, "media_type"),
            )
        case "separator":
            return SceneSeparator(large=_boolean(raw, "large"), visible=_boolean(raw, "visible"))
        case "link":
            return SceneLink(label=_string(raw, "label"), url=_string(raw, "url"))
        case "button":
            return SceneButton(
                label=_string(raw, "label"),
                action=_string(raw, "action"),
                style=ActionStyle(_string(raw, "style")),
                emoji=_optional_string(raw, "emoji"),
                disabled=_boolean(raw, "disabled"),
                policy=ActionPolicy(_string(raw, "policy")),
            )
        case "routed_button":
            return SceneRoutedButton(
                label=_string(raw, "label"),
                route_id=_string(raw, "route_id"),
                style=ActionStyle(_string(raw, "style")),
                emoji=_optional_string(raw, "emoji"),
                disabled=_boolean(raw, "disabled"),
            )
        case "select":
            options = raw.get("options")
            if not isinstance(options, list):
                msg = "select options must be an array"
                raise SceneCodecError(msg)
            return SceneSelect(
                options=tuple(
                    SceneOption(
                        label=_string(_object(option), "label"),
                        value=_string(_object(option), "value"),
                        description=_optional_string(_object(option), "description"),
                        default=_boolean(_object(option), "default"),
                    )
                    for option in options
                ),
                action=_string(raw, "action"),
                placeholder=_optional_string(raw, "placeholder"),
                min_values=_integer(raw, "min_values"),
                max_values=_integer(raw, "max_values"),
                disabled=_boolean(raw, "disabled"),
                policy=ActionPolicy(_string(raw, "policy")),
            )
        case "routed_select":
            options = raw.get("options")
            if not isinstance(options, list):
                msg = "routed select options must be an array"
                raise SceneCodecError(msg)
            return SceneRoutedSelect(
                options=tuple(
                    SceneOption(
                        label=_string(_object(option), "label"),
                        value=_string(_object(option), "value"),
                        description=_optional_string(_object(option), "description"),
                        default=_boolean(_object(option), "default"),
                    )
                    for option in options
                ),
                route_id=_string(raw, "route_id"),
                placeholder=_optional_string(raw, "placeholder"),
                min_values=_integer(raw, "min_values"),
                max_values=_integer(raw, "max_values"),
                disabled=_boolean(raw, "disabled"),
            )
        case "entity_select":
            defaults = raw.get("default_values")
            if not isinstance(defaults, list):
                msg = "entity select default_values must be an array"
                raise SceneCodecError(msg)
            return SceneEntitySelect(
                entity_type=EntityType(_string(raw, "entity_type")),
                action=_string(raw, "action"),
                placeholder=_optional_string(raw, "placeholder"),
                default_values=tuple(
                    EntityRef(EntityKind(_string(_object(value), "kind")), _integer(_object(value), "id"))
                    for value in defaults
                ),
                channel_types=tuple(ChannelType(value) for value in _string_array(raw, "channel_types")),
                min_values=_integer(raw, "min_values"),
                max_values=_integer(raw, "max_values"),
                disabled=_boolean(raw, "disabled"),
                policy=ActionPolicy(_string(raw, "policy")),
            )
        case "row":
            items = raw.get("items")
            if not isinstance(items, list):
                msg = "row items must be an array"
                raise SceneCodecError(msg)
            decoded = tuple(_node_from_dict(_object(item)) for item in items)
            if not all(
                isinstance(item, SceneLink | SceneButton | SceneRoutedButton | SceneExtension) for item in decoded
            ):
                msg = "row contains an unsupported child"
                raise SceneCodecError(msg)
            return SceneRow(decoded)
        case "thumbnail":
            return SceneThumbnail(url=_string(raw, "url"), description=_optional_string(raw, "description"))
        case "gallery":
            items = raw.get("items")
            if not isinstance(items, list):
                msg = "gallery items must be an array"
                raise SceneCodecError(msg)
            return SceneGallery(
                tuple(
                    SceneGalleryItem(
                        url=_string(_object(item), "url"),
                        description=_optional_string(_object(item), "description"),
                    )
                    for item in items
                )
            )
        case "section":
            texts = raw.get("texts")
            if not isinstance(texts, list):
                msg = "section texts must be an array"
                raise SceneCodecError(msg)
            decoded_texts = tuple(_node_from_dict(_object(text)) for text in texts)
            accessory = _node_from_dict(_object(raw.get("accessory")))
            if not all(isinstance(text, SceneText) for text in decoded_texts):
                msg = "section contains a non-text slot"
                raise SceneCodecError(msg)
            if not isinstance(accessory, SceneThumbnail | SceneLink | SceneButton | SceneRoutedButton | SceneExtension):
                msg = "section has an unsupported accessory"
                raise SceneCodecError(msg)
            return SceneSection(decoded_texts, accessory)
        case "panel":
            children = raw.get("children")
            accent = raw.get("accent")
            if not isinstance(children, list) or not (accent is None or isinstance(accent, int)):
                msg = "panel children or accent is malformed"
                raise SceneCodecError(msg)
            return ScenePanel(tuple(_node_from_dict(_object(child)) for child in children), accent=accent)
        case "extension":
            payload = raw.get("payload")
            if not isinstance(payload, dict):
                msg = "extension payload must be an object"
                raise SceneCodecError(msg)
            return SceneExtension(kind=_string(raw, "extension"), version=_integer(raw, "version"), payload=payload)
        case _:
            msg = f"unknown scene node kind {kind!r}"
            raise SceneCodecError(msg)


def _object(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        msg = "expected an object"
        raise SceneCodecError(msg)
    return value


def _string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        msg = f"{key} must be a string"
        raise SceneCodecError(msg)
    return value


def _optional_string(raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        msg = f"{key} must be a string or null"
        raise SceneCodecError(msg)
    return value


def _string_array(raw: Mapping[str, Any], key: str) -> list[str]:
    value = raw.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        msg = f"{key} must be an array of strings"
        raise SceneCodecError(msg)
    return value


def _integer(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        msg = f"{key} must be an integer"
        raise SceneCodecError(msg)
    return value


def _boolean(raw: Mapping[str, Any], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        msg = f"{key} must be a boolean"
        raise SceneCodecError(msg)
    return value
