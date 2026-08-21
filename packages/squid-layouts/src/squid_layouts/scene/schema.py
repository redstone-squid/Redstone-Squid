"""JSON Schema for the experimental resolved-scene protocol."""

from typing import Any


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
        "children": {"type": "array", "items": {"$ref": "#/$defs/node"}},
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
    "required": ["protocol", "target", "target_version", "children", "assets", "pagers"],
    "$defs": {
        "node": {
            "oneOf": [
                {"$ref": f"#/$defs/{kind}"}
                for kind in (
                    "text",
                    "separator",
                    "link",
                    "button",
                    "routed_button",
                    "select",
                    "row",
                    "thumbnail",
                    "gallery",
                    "section",
                    "panel",
                    "extension",
                )
            ]
        },
        "text": _node(
            "text",
            {"content": {"type": "string"}, "dialect": {"enum": ["plain", "discord-markdown"]}},
            "content",
            "dialect",
        ),
        "separator": _node(
            "separator",
            {"large": {"type": "boolean"}, "visible": {"type": "boolean"}},
            "large",
            "visible",
        ),
        "link": _node(
            "link",
            {"label": {"type": "string"}, "url": {"type": "string"}},
            "label",
            "url",
        ),
        "button": _node(
            "button",
            {
                "label": {"type": "string"},
                "action": {"type": "string"},
                "style": {"enum": ["primary", "secondary", "success", "danger"]},
                "emoji": {"type": ["string", "null"]},
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
            "routed_button",
            {
                "label": {"type": "string"},
                "route_id": {"type": "string", "maxLength": 100},
                "style": {"enum": ["primary", "secondary", "success", "danger"]},
                "emoji": {"type": ["string", "null"]},
                "disabled": {"type": "boolean"},
            },
            "label",
            "route_id",
            "style",
            "emoji",
            "disabled",
        ),
        "select": _node(
            "select",
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
        "option": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "label": {"type": "string"},
                "value": {"type": "string"},
                "description": {"type": ["string", "null"]},
                "default": {"type": "boolean"},
            },
            "required": ["label", "value", "description", "default"],
        },
        "row": _node(
            "row",
            {
                "items": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {"$ref": "#/$defs/link"},
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
            "thumbnail",
            {"url": {"type": "string"}, "description": {"type": ["string", "null"]}},
            "url",
            "description",
        ),
        "gallery": _node(
            "gallery",
            {"items": {"type": "array", "items": {"$ref": "#/$defs/gallery_item"}}},
            "items",
        ),
        "gallery_item": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "url": {"type": "string"},
                "description": {"type": ["string", "null"]},
            },
            "required": ["url", "description"],
        },
        "section": _node(
            "section",
            {
                "texts": {"type": "array", "items": {"$ref": "#/$defs/text"}},
                "accessory": {
                    "oneOf": [
                        {"$ref": "#/$defs/thumbnail"},
                        {"$ref": "#/$defs/link"},
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
            "panel",
            {
                "children": {"type": "array", "items": {"$ref": "#/$defs/node"}},
                "accent": {"type": ["integer", "null"], "minimum": 0, "maximum": 16777215},
            },
            "children",
            "accent",
        ),
        "extension": _node(
            "extension",
            {
                "extension": {"type": "string"},
                "version": {"type": "integer", "minimum": 0},
                "payload": {},
            },
            "extension",
            "version",
            "payload",
        ),
    },
}
