"""Canonical JSON codec for resolved scenes."""

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from squid_layouts.actions import ActionPolicy
from squid_layouts.scene import (
    SceneAsset,
    SceneButton,
    SceneDocument,
    SceneExtension,
    SceneGallery,
    SceneGalleryItem,
    SceneLink,
    SceneNode,
    SceneOption,
    ScenePager,
    ScenePanel,
    SceneRow,
    SceneSection,
    SceneSelect,
    SceneSeparator,
    SceneText,
    SceneThumbnail,
)
from squid_layouts.scene_schema import SCENE_SCHEMA
from squid_layouts.styles import ActionStyle


class SceneCodecError(ValueError):
    """A scene payload is malformed or uses an unsupported protocol."""


class SceneCodec:
    """Encode and decode deterministic experimental scene protocol 0."""

    protocol = 0

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
            "children": [_node_to_dict(child) for child in scene.children],
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
        children = raw.get("children")
        assets = raw.get("assets", [])
        pagers = raw.get("pagers", [])
        if not isinstance(children, list) or not isinstance(assets, list) or not isinstance(pagers, list):
            msg = "scene children, assets, and pagers must be arrays"
            raise SceneCodecError(msg)
        return SceneDocument(
            protocol=protocol,
            target=_string(raw, "target"),
            target_version=_integer(raw, "target_version"),
            children=tuple(_node_from_dict(_object(child)) for child in children),
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


def _node_to_dict(node: SceneNode | SceneLink | SceneButton) -> dict[str, Any]:
    match node:
        case SceneText(content=content):
            return {"kind": "text", "content": content}
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


def _node_from_dict(raw: Mapping[str, Any]) -> SceneNode | SceneLink | SceneButton:
    kind = _string(raw, "kind")
    match kind:
        case "text":
            return SceneText(_string(raw, "content"))
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
        case "row":
            items = raw.get("items")
            if not isinstance(items, list):
                msg = "row items must be an array"
                raise SceneCodecError(msg)
            decoded = tuple(_node_from_dict(_object(item)) for item in items)
            if not all(isinstance(item, SceneLink | SceneButton | SceneExtension) for item in decoded):
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
            if not isinstance(accessory, SceneThumbnail | SceneLink | SceneButton | SceneExtension):
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
