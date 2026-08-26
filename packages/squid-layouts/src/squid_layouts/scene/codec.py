"""Canonical JSON codec for resolved scenes."""

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, cast

from squid_layouts.emoji import Emoji
from squid_layouts.entity import ChannelType, EntityKind, EntityRef, EntityType
from squid_layouts.interactions import ActionMode
from squid_layouts.primitives.styles import ActionStyle
from squid_layouts.scene.model import (
    Asset,
    Body,
    Button,
    ClassicMessage,
    ClassicRow,
    ComponentsV2,
    Control,
    Document,
    Embed,
    EmbedAuthor,
    EmbedField,
    EmbedFooter,
    EmbedMedia,
    EntitySelect,
    Extension,
    File,
    Gallery,
    GalleryItem,
    Link,
    Node,
    Option,
    Pager,
    Panel,
    PremiumButton,
    RoutedButton,
    RoutedSelect,
    Row,
    Section,
    Select,
    Separator,
    Text,
    Thumbnail,
    Time,
    ZonedTime,
)
from squid_layouts.scene.schema import SCHEMA
from squid_layouts.text import Markup


class CodecError(ValueError):
    """A scene payload is malformed or uses an unsupported protocol."""


class Codec:
    """Encode and decode deterministic resolved-scene protocol 1."""

    protocol = 1

    @classmethod
    def schema(cls) -> dict[str, Any]:
        """Return an isolated JSON Schema for cross-language scene consumers."""
        return deepcopy(SCHEMA)

    @classmethod
    def schema_json(cls) -> str:
        """Return the scene schema in deterministic JSON form."""
        return json.dumps(cls.schema(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def dumps(cls, scene: Document[Any]) -> str:
        return json.dumps(cls.to_dict(scene), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def loads(cls, payload: str) -> Document:
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as error:
            raise CodecError(str(error)) from error
        if not isinstance(raw, dict):
            msg = "scene must be a JSON object"
            raise CodecError(msg)
        return cls.from_dict(raw)

    @classmethod
    def fingerprint(cls, scene: Document[Any]) -> str:
        return hashlib.blake2s(cls.dumps(scene).encode(), digest_size=16).hexdigest()

    @classmethod
    def to_dict(cls, scene: Document[Any]) -> dict[str, Any]:
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
    def from_dict(cls, raw: Mapping[str, Any]) -> Document:
        protocol = _integer(raw, "protocol")
        if protocol != cls.protocol:
            msg = f"unsupported scene protocol {protocol}"
            raise CodecError(msg)
        assets = raw.get("assets", [])
        pagers = raw.get("pagers", [])
        if not isinstance(assets, list) or not isinstance(pagers, list):
            msg = "scene assets and pagers must be arrays"
            raise CodecError(msg)
        return Document(
            protocol=protocol,
            target=_string(raw, "target"),
            target_version=_integer(raw, "target_version"),
            body=_body_from_dict(_object(raw.get("body"))),
            assets=tuple(
                Asset(
                    key=_string(_object(asset), "key"),
                    name=_string(_object(asset), "name"),
                    media_type=_string(_object(asset), "media_type"),
                )
                for asset in assets
            ),
            pagers=tuple(
                Pager(
                    key=_string(_object(pager), "key"),
                    page=_integer(_object(pager), "page"),
                    pages=_integer(_object(pager), "pages"),
                    content_fingerprint=_string(_object(pager), "content_fingerprint"),
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


def _body_from_dict(raw: Mapping[str, Any]) -> Body:
    kind = _string(raw, "kind")
    match kind:
        case ComponentsV2.KIND:
            children = raw.get("children")
            if not isinstance(children, list):
                msg = "components_v2 children must be an array"
                raise CodecError(msg)
            return ComponentsV2(tuple(_node_from_dict(_object(child)) for child in children))
        case ClassicMessage.KIND:
            embeds = raw.get("embeds")
            rows = raw.get("rows")
            if not isinstance(embeds, list) or not isinstance(rows, list):
                msg = "classic_message embeds and rows must be arrays"
                raise CodecError(msg)
            return ClassicMessage(
                content=_optional_string(raw, "content"),
                embeds=tuple(_embed_from_dict(_object(embed)) for embed in embeds),
                rows=tuple(_row_from_dict(_object(row)) for row in rows),
            )
        case _:
            msg = f"unknown scene body kind {kind!r}"
            raise CodecError(msg)


def _row_from_dict(raw: Mapping[str, Any]) -> ClassicRow:
    controls = raw.get("controls")
    if not isinstance(controls, list):
        msg = "classic row controls must be an array"
        raise CodecError(msg)
    decoded = tuple(_node_from_dict(_object(control)) for control in controls)
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


def _media_to_dict(media: EmbedMedia | None) -> dict[str, Any] | None:
    return None if media is None else {"url": media.url, "description": media.description}


def _embed_from_dict(raw: Mapping[str, Any]) -> Embed:
    fields = raw.get("fields")
    if not isinstance(fields, list):
        msg = "embed fields must be an array"
        raise CodecError(msg)
    colour = raw.get("colour")
    if not (colour is None or (isinstance(colour, int) and not isinstance(colour, bool))):
        msg = "embed colour must be an integer or null"
        raise CodecError(msg)
    footer = raw.get("footer")
    author = raw.get("author")
    return Embed(
        title=_optional_string(raw, "title"),
        url=_optional_string(raw, "url"),
        description=_optional_string(raw, "description"),
        fields=tuple(
            EmbedField(
                name=_string(_object(field), "name"),
                value=_string(_object(field), "value"),
                inline=_boolean(_object(field), "inline"),
            )
            for field in fields
        ),
        footer=(
            None
            if footer is None
            else EmbedFooter(
                text=_string(_object(footer), "text"), icon_url=_optional_string(_object(footer), "icon_url")
            )
        ),
        author=(
            None
            if author is None
            else EmbedAuthor(
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


def _media_from_dict(raw: Any) -> EmbedMedia | None:
    if raw is None:
        return None
    return EmbedMedia(url=_string(_object(raw), "url"), description=_optional_string(_object(raw), "description"))


def _node_to_dict(node: Node | Link | PremiumButton | Button | RoutedButton) -> dict[str, Any]:
    match node:
        case Text(content=content, markup=markup):
            # The JSON key stays "dialect": the Python name moved, the wire format did not.
            return {"kind": Text.KIND, "content": content, "dialect": markup.value}
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
                "policy": mode.value,
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
                "policy": mode.value,
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
            channel_types=channel_types,
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
                "channel_types": [value.value for value in channel_types],
                "min_values": min_values,
                "max_values": max_values,
                "disabled": disabled,
                "policy": mode.value,
            }
        case Row(items=items):
            return {"kind": Row.KIND, "items": [_node_to_dict(item) for item in items]}
        case Thumbnail(url=url, description=description, spoiler=spoiler):
            return {"kind": Thumbnail.KIND, "url": url, "description": description, "spoiler": spoiler}
        case Gallery(items=items):
            return {
                "kind": Gallery.KIND,
                "items": [
                    {"url": item.url, "description": item.description, "spoiler": item.spoiler} for item in items
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
    raw: Mapping[str, Any],
) -> Node | Link | PremiumButton | Button | RoutedButton:
    kind = _string(raw, "kind")
    match kind:
        case Text.KIND:
            return Text(_string(raw, "content"), Markup(_string(raw, "dialect")))
        case Time.KIND:
            return Time(
                _string(raw, "instant"),
                _string(raw, "style"),
                _optional_string(raw, "prefix"),
            )
        case ZonedTime.KIND:
            return ZonedTime(
                _string(raw, "instant"),
                _string(raw, "timezone"),
                _optional_string(raw, "prefix"),
            )
        case File.KIND:
            return File(
                _string(raw, "asset_key"),
                _string(raw, "name"),
                _string(raw, "media_type"),
                _boolean(raw, "spoiler", default=False),
            )
        case Separator.KIND:
            return Separator(large=_boolean(raw, "large"), visible=_boolean(raw, "visible"))
        case Link.KIND:
            return Link(
                label=_optional_string(raw, "label"),
                url=_string(raw, "url"),
                emoji=_emoji_from_value(raw.get("emoji")),
                disabled=_boolean(raw, "disabled", default=False),
            )
        case PremiumButton.KIND:
            return PremiumButton(_integer(raw, "sku_id"))
        case Button.KIND:
            return Button(
                label=_optional_string(raw, "label"),
                action=_string(raw, "action"),
                style=ActionStyle(_string(raw, "style")),
                emoji=_emoji_from_value(raw.get("emoji")),
                disabled=_boolean(raw, "disabled"),
                mode=ActionMode(_string(raw, "policy")),
            )
        case RoutedButton.KIND:
            return RoutedButton(
                label=_optional_string(raw, "label"),
                route_id=_string(raw, "route_id"),
                style=ActionStyle(_string(raw, "style")),
                emoji=_emoji_from_value(raw.get("emoji")),
                disabled=_boolean(raw, "disabled"),
            )
        case Select.KIND:
            options = raw.get("options")
            if not isinstance(options, list):
                msg = "select options must be an array"
                raise CodecError(msg)
            return Select(
                options=tuple(
                    Option(
                        label=_string(_object(option), "label"),
                        value=_string(_object(option), "value"),
                        description=_optional_string(_object(option), "description"),
                        default=_boolean(_object(option), "default"),
                        emoji=_emoji_from_value(_object(option).get("emoji")),
                    )
                    for option in options
                ),
                action=_string(raw, "action"),
                placeholder=_optional_string(raw, "placeholder"),
                min_values=_integer(raw, "min_values"),
                max_values=_integer(raw, "max_values"),
                disabled=_boolean(raw, "disabled"),
                mode=ActionMode(_string(raw, "policy")),
            )
        case RoutedSelect.KIND:
            options = raw.get("options")
            if not isinstance(options, list):
                msg = "routed select options must be an array"
                raise CodecError(msg)
            return RoutedSelect(
                options=tuple(
                    Option(
                        label=_string(_object(option), "label"),
                        value=_string(_object(option), "value"),
                        description=_optional_string(_object(option), "description"),
                        default=_boolean(_object(option), "default"),
                        emoji=_emoji_from_value(_object(option).get("emoji")),
                    )
                    for option in options
                ),
                route_id=_string(raw, "route_id"),
                placeholder=_optional_string(raw, "placeholder"),
                min_values=_integer(raw, "min_values"),
                max_values=_integer(raw, "max_values"),
                disabled=_boolean(raw, "disabled"),
            )
        case EntitySelect.KIND:
            defaults = raw.get("default_values")
            if not isinstance(defaults, list):
                msg = "entity select default_values must be an array"
                raise CodecError(msg)
            return EntitySelect(
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
                mode=ActionMode(_string(raw, "policy")),
            )
        case Row.KIND:
            items = raw.get("items")
            if not isinstance(items, list):
                msg = "row items must be an array"
                raise CodecError(msg)
            decoded = tuple(_node_from_dict(_object(item)) for item in items)
            if not all(isinstance(item, Link | PremiumButton | Button | RoutedButton | Extension) for item in decoded):
                msg = "row contains an unsupported child"
                raise CodecError(msg)
            return Row(decoded)
        case Thumbnail.KIND:
            return Thumbnail(
                url=_string(raw, "url"),
                description=_optional_string(raw, "description"),
                spoiler=_boolean(raw, "spoiler", default=False),
            )
        case Gallery.KIND:
            items = raw.get("items")
            if not isinstance(items, list):
                msg = "gallery items must be an array"
                raise CodecError(msg)
            return Gallery(
                tuple(
                    GalleryItem(
                        url=_string(_object(item), "url"),
                        description=_optional_string(_object(item), "description"),
                        spoiler=_boolean(_object(item), "spoiler", default=False),
                    )
                    for item in items
                )
            )
        case Section.KIND:
            texts = raw.get("texts")
            if not isinstance(texts, list):
                msg = "section texts must be an array"
                raise CodecError(msg)
            decoded_texts = tuple(_node_from_dict(_object(text)) for text in texts)
            accessory = _node_from_dict(_object(raw.get("accessory")))
            if not all(isinstance(text, Text) for text in decoded_texts):
                msg = "section contains a non-text slot"
                raise CodecError(msg)
            if not isinstance(
                accessory,
                Thumbnail | Link | PremiumButton | Button | RoutedButton | Extension,
            ):
                msg = "section has an unsupported accessory"
                raise CodecError(msg)
            return Section(decoded_texts, accessory)
        case Panel.KIND:
            children = raw.get("children")
            accent = raw.get("accent")
            if not isinstance(children, list) or not (accent is None or isinstance(accent, int)):
                msg = "panel children or accent is malformed"
                raise CodecError(msg)
            return Panel(
                tuple(_node_from_dict(_object(child)) for child in children),
                accent=accent,
                spoiler=_boolean(raw, "spoiler", default=False),
            )
        case Extension.KIND:
            payload = raw.get("payload")
            if not isinstance(payload, dict):
                msg = "extension payload must be an object"
                raise CodecError(msg)
            return Extension(kind=_string(raw, "extension"), version=_integer(raw, "version"), payload=payload)
        case _:
            msg = f"unknown scene node kind {kind!r}"
            raise CodecError(msg)


def _object(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        msg = "expected an object"
        raise CodecError(msg)
    return value


def _string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        msg = f"{key} must be a string"
        raise CodecError(msg)
    return value


def _optional_string(raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        msg = f"{key} must be a string or null"
        raise CodecError(msg)
    return value


def _string_array(raw: Mapping[str, Any], key: str) -> list[str]:
    value = raw.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        msg = f"{key} must be an array of strings"
        raise CodecError(msg)
    return value


def _integer(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        msg = f"{key} must be an integer"
        raise CodecError(msg)
    return value


def _boolean(raw: Mapping[str, Any], key: str, *, default: bool | None = None) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        msg = f"{key} must be a boolean"
        raise CodecError(msg)
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
    raw = _object(value)
    emoji_id = raw.get("id")
    if emoji_id is not None and (not isinstance(emoji_id, int) or isinstance(emoji_id, bool)):
        message = "emoji id must be an integer or null"
        raise CodecError(message)
    return Emoji(
        name=_string(raw, "name"),
        id=emoji_id,
        animated=_boolean(raw, "animated", default=False),
    )
