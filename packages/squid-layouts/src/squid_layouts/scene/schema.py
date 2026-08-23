"""JSON Schema for the experimental resolved-scene protocol."""

from typing import Any

from squid_layouts.scene.model import (
    SceneButton,
    SceneClassicMessage,
    SceneComponentsV2,
    SceneEntitySelect,
    SceneExtension,
    SceneFile,
    SceneGallery,
    SceneLink,
    ScenePanel,
    ScenePremiumButton,
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


def _node(kind: str, properties: dict[str, Any], *required: str) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"kind": {"const": kind}, **properties},
        "required": ["kind", *required],
    }


SCENE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://schem-at.github.io/squid-layouts/scene-v1.schema.json",
    "title": "squid-layouts resolved scene protocol 1",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "protocol": {"const": 1},
        "target": {"type": "string"},
        "target_version": {"type": "integer", "minimum": 0},
        "body": {"$ref": "#/$defs/body"},
        "assets": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "key": {"type": "string"},
                    "name": {"type": "string"},
                    "media_type": {"type": "string"},
                },
                "required": ["key", "name", "media_type"],
            },
        },
        "pagers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "key": {"type": "string"},
                    "page": {"type": "integer", "minimum": 0},
                    "pages": {"type": "integer", "minimum": 1},
                    "content_fingerprint": {"type": "string"},
                },
                "required": ["key", "page", "pages", "content_fingerprint"],
            },
        },
    },
    "required": ["protocol", "target", "target_version", "body", "assets", "pagers"],
    "$defs": {
        "body": {"oneOf": [{"$ref": f"#/$defs/{kind}"} for kind in (SceneComponentsV2.KIND, SceneClassicMessage.KIND)]},
        "components_v2": _node(
            SceneComponentsV2.KIND,
            {"children": {"type": "array", "items": {"$ref": "#/$defs/node"}}},
            "children",
        ),
        "classic_message": _node(
            SceneClassicMessage.KIND,
            {
                "content": {"type": ["string", "null"]},
                "embeds": {"type": "array", "items": {"$ref": "#/$defs/embed"}},
                "rows": {"type": "array", "items": {"$ref": "#/$defs/classic_row"}, "maxItems": 5},
            },
            "content",
            "embeds",
            "rows",
        ),
        "classic_row": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "controls": {
                    "type": "array",
                    "maxItems": 5,
                    "items": {
                        "oneOf": [
                            {"$ref": "#/$defs/link"},
                            {"$ref": "#/$defs/premium_button"},
                            {"$ref": "#/$defs/button"},
                            {"$ref": "#/$defs/routed_button"},
                            {"$ref": "#/$defs/select"},
                            {"$ref": "#/$defs/routed_select"},
                            {"$ref": "#/$defs/entity_select"},
                            {"$ref": "#/$defs/extension"},
                        ]
                    },
                }
            },
            "required": ["controls"],
        },
        "embed": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": ["string", "null"]},
                "url": {"type": ["string", "null"]},
                "description": {"type": ["string", "null"]},
                "fields": {"type": "array", "items": {"$ref": "#/$defs/embed_field"}, "maxItems": 25},
                "footer": {
                    "oneOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {"text": {"type": "string"}, "icon_url": {"type": ["string", "null"]}},
                            "required": ["text", "icon_url"],
                        },
                    ]
                },
                "author": {
                    "oneOf": [
                        {"type": "null"},
                        {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string"},
                                "url": {"type": ["string", "null"]},
                                "icon_url": {"type": ["string", "null"]},
                            },
                            "required": ["name", "url", "icon_url"],
                        },
                    ]
                },
                "colour": {"type": ["integer", "null"], "minimum": 0, "maximum": 16777215},
                "image": {"$ref": "#/$defs/embed_media"},
                "thumbnail": {"$ref": "#/$defs/embed_media"},
                "timestamp": {"type": ["string", "null"]},
            },
            "required": [
                "title",
                "url",
                "description",
                "fields",
                "footer",
                "author",
                "colour",
                "image",
                "thumbnail",
                "timestamp",
            ],
        },
        "embed_field": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "value": {"type": "string"},
                "inline": {"type": "boolean"},
            },
            "required": ["name", "value", "inline"],
        },
        "embed_media": {
            "oneOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"url": {"type": "string"}, "description": {"type": ["string", "null"]}},
                    "required": ["url", "description"],
                },
            ]
        },
        "node": {
            "oneOf": [
                {"$ref": f"#/$defs/{kind}"}
                for kind in (
                    SceneText.KIND,
                    SceneTime.KIND,
                    SceneZonedTime.KIND,
                    SceneFile.KIND,
                    SceneSeparator.KIND,
                    SceneLink.KIND,
                    SceneButton.KIND,
                    SceneRoutedButton.KIND,
                    ScenePremiumButton.KIND,
                    SceneSelect.KIND,
                    SceneRoutedSelect.KIND,
                    SceneEntitySelect.KIND,
                    SceneRow.KIND,
                    SceneThumbnail.KIND,
                    SceneGallery.KIND,
                    SceneSection.KIND,
                    ScenePanel.KIND,
                    SceneExtension.KIND,
                )
            ]
        },
        "text": _node(
            SceneText.KIND,
            {"content": {"type": "string"}, "dialect": {"enum": ["plain", "discord-markdown"]}},
            "content",
            "dialect",
        ),
        "time": _node(
            SceneTime.KIND,
            {
                "instant": {"type": "string", "format": "date-time"},
                "style": {"enum": ["t", "T", "d", "D", "f", "F", "R"]},
                "prefix": {"type": ["string", "null"]},
            },
            "instant",
            "style",
            "prefix",
        ),
        "zoned_time": _node(
            SceneZonedTime.KIND,
            {
                "instant": {"type": "string", "format": "date-time"},
                "timezone": {"type": "string", "minLength": 1},
                "prefix": {"type": ["string", "null"]},
            },
            "instant",
            "timezone",
            "prefix",
        ),
        "file": _node(
            SceneFile.KIND,
            {
                "asset_key": {"type": "string"},
                "name": {"type": "string"},
                "media_type": {"type": "string"},
                "spoiler": {"type": "boolean"},
            },
            "asset_key",
            "name",
            "media_type",
        ),
        "separator": _node(
            SceneSeparator.KIND,
            {"large": {"type": "boolean"}, "visible": {"type": "boolean"}},
            "large",
            "visible",
        ),
        "link": _node(
            SceneLink.KIND,
            {
                "label": {"type": ["string", "null"]},
                "url": {"type": "string", "maxLength": 512},
                "emoji": {"$ref": "#/$defs/emoji"},
                "disabled": {"type": "boolean"},
            },
            "label",
            "url",
        ),
        "premium_button": _node(
            ScenePremiumButton.KIND,
            {"sku_id": {"type": "integer", "minimum": 1}},
            "sku_id",
        ),
        "button": _node(
            SceneButton.KIND,
            {
                "label": {"type": ["string", "null"]},
                "action": {"type": "string"},
                "style": {"enum": ["primary", "secondary", "success", "danger"]},
                "emoji": {"$ref": "#/$defs/emoji"},
                "disabled": {"type": "boolean"},
                "policy": {"enum": ["exclusive", "rebase", "parallel_read", "immediate"]},
            },
            "label",
            "action",
            "style",
            "emoji",
            "disabled",
            "policy",
        ),
        "routed_button": _node(
            SceneRoutedButton.KIND,
            {
                "label": {"type": ["string", "null"]},
                "route_id": {"type": "string", "maxLength": 100},
                "style": {"enum": ["primary", "secondary", "success", "danger"]},
                "emoji": {"$ref": "#/$defs/emoji"},
                "disabled": {"type": "boolean"},
            },
            "label",
            "route_id",
            "style",
            "emoji",
            "disabled",
        ),
        "select": _node(
            SceneSelect.KIND,
            {
                "options": {"type": "array", "items": {"$ref": "#/$defs/option"}},
                "action": {"type": "string"},
                "placeholder": {"type": ["string", "null"]},
                "min_values": {"type": "integer", "minimum": 0},
                "max_values": {"type": "integer", "minimum": 0},
                "disabled": {"type": "boolean"},
                "policy": {"enum": ["exclusive", "rebase", "parallel_read", "immediate"]},
            },
            "options",
            "action",
            "placeholder",
            "min_values",
            "max_values",
            "disabled",
            "policy",
        ),
        "routed_select": _node(
            SceneRoutedSelect.KIND,
            {
                "options": {"type": "array", "items": {"$ref": "#/$defs/option"}, "maxItems": 25},
                "route_id": {"type": "string", "maxLength": 100},
                "placeholder": {"type": ["string", "null"]},
                "min_values": {"type": "integer", "minimum": 0},
                "max_values": {"type": "integer", "minimum": 0},
                "disabled": {"type": "boolean"},
            },
            "options",
            "route_id",
            "placeholder",
            "min_values",
            "max_values",
            "disabled",
        ),
        "entity_select": _node(
            SceneEntitySelect.KIND,
            {
                "entity_type": {"enum": ["user", "role", "channel", "mentionable"]},
                "action": {"type": "string"},
                "placeholder": {"type": ["string", "null"], "maxLength": 150},
                "default_values": {
                    "type": "array",
                    "maxItems": 25,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "kind": {"enum": ["user", "role", "channel"]},
                            "id": {"type": "integer", "minimum": 1},
                        },
                        "required": ["kind", "id"],
                    },
                },
                "channel_types": {
                    "type": "array",
                    "items": {
                        "enum": [
                            "text",
                            "voice",
                            "category",
                            "announcement",
                            "announcement_thread",
                            "public_thread",
                            "private_thread",
                            "stage_voice",
                            "forum",
                            "media",
                        ]
                    },
                },
                "min_values": {"type": "integer", "minimum": 0, "maximum": 25},
                "max_values": {"type": "integer", "minimum": 0, "maximum": 25},
                "disabled": {"type": "boolean"},
                "policy": {"enum": ["exclusive", "rebase", "parallel_read", "immediate"]},
            },
            "entity_type",
            "action",
            "placeholder",
            "default_values",
            "channel_types",
            "min_values",
            "max_values",
            "disabled",
            "policy",
        ),
        "option": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "label": {"type": "string"},
                "value": {"type": "string"},
                "description": {"type": ["string", "null"]},
                "default": {"type": "boolean"},
                "emoji": {"$ref": "#/$defs/emoji"},
            },
            "required": ["label", "value", "description", "default"],
        },
        "row": _node(
            SceneRow.KIND,
            {
                "items": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"$ref": "#/$defs/link"},
                            {"$ref": "#/$defs/premium_button"},
                            {"$ref": "#/$defs/button"},
                            {"$ref": "#/$defs/routed_button"},
                            {"$ref": "#/$defs/extension"},
                        ]
                    },
                }
            },
            "items",
        ),
        "thumbnail": _node(
            SceneThumbnail.KIND,
            {
                "url": {"type": "string"},
                "description": {"type": ["string", "null"]},
                "spoiler": {"type": "boolean"},
            },
            "url",
            "description",
        ),
        "gallery": _node(
            SceneGallery.KIND,
            {"items": {"type": "array", "items": {"$ref": "#/$defs/gallery_item"}}},
            "items",
        ),
        "gallery_item": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "url": {"type": "string"},
                "description": {"type": ["string", "null"]},
                "spoiler": {"type": "boolean"},
            },
            "required": ["url", "description"],
        },
        "section": _node(
            SceneSection.KIND,
            {
                "texts": {"type": "array", "items": {"$ref": "#/$defs/text"}},
                "accessory": {
                    "oneOf": [
                        {"$ref": "#/$defs/thumbnail"},
                        {"$ref": "#/$defs/link"},
                        {"$ref": "#/$defs/premium_button"},
                        {"$ref": "#/$defs/button"},
                        {"$ref": "#/$defs/routed_button"},
                        {"$ref": "#/$defs/extension"},
                    ]
                },
            },
            "texts",
            "accessory",
        ),
        "panel": _node(
            ScenePanel.KIND,
            {
                "children": {"type": "array", "items": {"$ref": "#/$defs/node"}},
                "accent": {"type": ["integer", "null"], "minimum": 0, "maximum": 16777215},
                "spoiler": {"type": "boolean"},
            },
            "children",
            "accent",
        ),
        "extension": _node(
            SceneExtension.KIND,
            {
                "extension": {"type": "string"},
                "version": {"type": "integer", "minimum": 0},
                "payload": {},
            },
            "extension",
            "version",
            "payload",
        ),
        "emoji": {
            "oneOf": [
                {"type": "null"},
                {"type": "string"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "id": {"type": ["integer", "null"], "minimum": 1},
                        "animated": {"type": "boolean"},
                    },
                    "required": ["name", "id", "animated"],
                },
            ]
        },
    },
}
