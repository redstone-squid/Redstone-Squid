"""JSON Schema for the experimental resolved-scene protocol."""

from typing import Any

from squid_ui.entity import ConversationType
from squid_ui.interactions import ActionMode
from squid_ui.scene.model import (
    Button,
    ClassicMessage,
    ComponentsV2,
    EntitySelect,
    Extension,
    File,
    Gallery,
    HtmlAttributeName,
    HtmlBody,
    HtmlElement,
    HtmlTag,
    HtmlText,
    Link,
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
from squid_ui.scene.slack import SlackHomeView, SlackMessage, SlackModalView


def _node(kind: str, properties: dict[str, Any], *required: str) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"kind": {"const": kind}, **properties},
        "required": ["kind", *required],
    }


def _nullable(properties: dict[str, Any], *required: str) -> dict[str, Any]:
    return {
        "oneOf": [
            {"type": "null"},
            {
                "type": "object",
                "additionalProperties": False,
                "properties": properties,
                "required": list(required),
            },
        ]
    }


SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://redstone-squid.github.io/Redstone-Squid/schema/scene-v1.schema.json",
    "title": "squid-ui resolved scene protocol 1",
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
        "body": {
            "oneOf": [
                {"$ref": f"#/$defs/{kind}"}
                for kind in (
                    ComponentsV2.KIND,
                    ClassicMessage.KIND,
                    HtmlBody.KIND,
                    SlackMessage.KIND,
                    SlackModalView.KIND,
                    SlackHomeView.KIND,
                )
            ]
        },
        "components_v2": _node(
            ComponentsV2.KIND,
            {"children": {"type": "array", "items": {"$ref": "#/$defs/node"}}},
            "children",
        ),
        "classic_message": _node(
            ClassicMessage.KIND,
            {
                "content": {"type": ["string", "null"]},
                "embeds": {"type": "array", "items": {"$ref": "#/$defs/embed"}},
                "rows": {"type": "array", "items": {"$ref": "#/$defs/classic_row"}, "maxItems": 5},
            },
            "content",
            "embeds",
            "rows",
        ),
        "html": _node(
            HtmlBody.KIND,
            {
                "children": {"type": "array", "items": {"$ref": "#/$defs/html_node"}},
                "locale": {"type": ["string", "null"]},
            },
            "children",
            "locale",
        ),
        "slack_message": _node(
            SlackMessage.KIND,
            {
                "text": {"type": "string", "maxLength": 40000},
                "blocks": {"type": "array", "maxItems": 50, "items": {"$ref": "#/$defs/slack_block"}},
            },
            "text",
            "blocks",
        ),
        "slack_modal": _node(
            SlackModalView.KIND,
            {
                "callback_id": {"type": "string", "maxLength": 255},
                "title": {"$ref": "#/$defs/slack_text"},
                "submit": {"$ref": "#/$defs/slack_text"},
                "close": {"$ref": "#/$defs/slack_text"},
                "blocks": {"type": "array", "maxItems": 100, "items": {"$ref": "#/$defs/slack_block"}},
                "private_metadata": {"type": ["string", "null"], "maxLength": 3000},
            },
            "callback_id",
            "title",
            "submit",
            "close",
            "blocks",
            "private_metadata",
        ),
        "slack_home": _node(
            SlackHomeView.KIND,
            {
                "blocks": {"type": "array", "maxItems": 100, "items": {"$ref": "#/$defs/slack_block"}},
                "callback_id": {"type": ["string", "null"], "maxLength": 255},
                "private_metadata": {"type": ["string", "null"], "maxLength": 3000},
            },
            "blocks",
            "callback_id",
            "private_metadata",
        ),
        "slack_text": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "content": {"type": "string"},
                "kind": {"enum": ["plain_text", "mrkdwn"]},
                "emoji": {"type": ["boolean", "null"]},
                "verbatim": {"type": ["boolean", "null"]},
            },
            "required": ["content", "kind", "emoji", "verbatim"],
        },
        "slack_action": _nullable(
            {
                "action": {"type": "string", "maxLength": 255},
                "mode": {"enum": [mode.value for mode in ActionMode]},
            },
            "action",
            "mode",
        ),
        "slack_route": _nullable({"route_id": {"type": "string", "maxLength": 255}}, "route_id"),
        "slack_option": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "label": {"$ref": "#/$defs/slack_text"},
                "value": {"type": "string", "maxLength": 150},
                "description": {
                    "oneOf": [{"type": "null"}, {"$ref": "#/$defs/slack_text"}]
                },
            },
            "required": ["label", "value", "description"],
        },
        "slack_element": {
            "oneOf": [
                {"$ref": "#/$defs/slack_button"},
                {"$ref": "#/$defs/slack_select"},
                {"$ref": "#/$defs/slack_text_input"},
                {"$ref": "#/$defs/slack_number_input"},
                {"$ref": "#/$defs/slack_date_picker"},
                {"$ref": "#/$defs/slack_time_picker"},
                {"$ref": "#/$defs/slack_checkboxes"},
                {"$ref": "#/$defs/slack_radio_buttons"},
            ]
        },
        "slack_input_element": {
            "oneOf": [
                {"$ref": "#/$defs/slack_select"},
                {"$ref": "#/$defs/slack_text_input"},
                {"$ref": "#/$defs/slack_number_input"},
                {"$ref": "#/$defs/slack_date_picker"},
                {"$ref": "#/$defs/slack_time_picker"},
                {"$ref": "#/$defs/slack_checkboxes"},
                {"$ref": "#/$defs/slack_radio_buttons"},
            ]
        },
        "slack_button": _node(
            "button",
            {
                "label": {"$ref": "#/$defs/slack_text"},
                "action": {"$ref": "#/$defs/slack_action"},
                "route": {"$ref": "#/$defs/slack_route"},
                "url": {"type": ["string", "null"], "maxLength": 3000},
                "value": {"type": ["string", "null"], "maxLength": 2000},
                "style": {"enum": ["default", "primary", "danger"]},
            },
            "label",
            "action",
            "route",
            "url",
            "value",
            "style",
        ),
        "slack_select": _node(
            "select",
            {
                "action": {"$ref": "#/$defs/slack_action"},
                "route": {"$ref": "#/$defs/slack_route"},
                "select_kind": {"enum": ["static", "users", "conversations"]},
                "placeholder": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/slack_text"}]},
                "options": {"type": "array", "maxItems": 100, "items": {"$ref": "#/$defs/slack_option"}},
                "initial_values": {"type": "array", "items": {"type": "string"}},
                "conversation_types": {
                    "type": "array",
                    "items": {"enum": [value.value for value in ConversationType]},
                },
                "minimum": {"type": "integer", "minimum": 0},
                "maximum": {"type": "integer", "minimum": 1},
            },
            "action",
            "route",
            "select_kind",
            "placeholder",
            "options",
            "initial_values",
            "conversation_types",
            "minimum",
            "maximum",
        ),
        "slack_text_input": _node(
            "text_input",
            {
                "action_id": {"type": "string", "maxLength": 255},
                "initial_value": {"type": ["string", "null"]},
                "placeholder": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/slack_text"}]},
                "multiline": {"type": "boolean"},
                "minimum_length": {"type": ["integer", "null"], "minimum": 0},
                "maximum_length": {"type": ["integer", "null"], "minimum": 0},
            },
            "action_id",
            "initial_value",
            "placeholder",
            "multiline",
            "minimum_length",
            "maximum_length",
        ),
        "slack_number_input": _node(
            "number_input",
            {
                "action_id": {"type": "string", "maxLength": 255},
                "initial_value": {"type": ["string", "null"]},
                "decimal_allowed": {"type": "boolean"},
                "minimum": {"type": ["string", "null"]},
                "maximum": {"type": ["string", "null"]},
            },
            "action_id",
            "initial_value",
            "decimal_allowed",
            "minimum",
            "maximum",
        ),
        "slack_date_picker": _node(
            "date_picker",
            {
                "action_id": {"type": "string", "maxLength": 255},
                "initial_date": {"type": ["string", "null"]},
                "placeholder": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/slack_text"}]},
            },
            "action_id",
            "initial_date",
            "placeholder",
        ),
        "slack_time_picker": _node(
            "time_picker",
            {
                "action_id": {"type": "string", "maxLength": 255},
                "initial_time": {"type": ["string", "null"]},
                "placeholder": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/slack_text"}]},
            },
            "action_id",
            "initial_time",
            "placeholder",
        ),
        "slack_checkboxes": _node(
            "checkboxes",
            {
                "action_id": {"type": "string", "maxLength": 255},
                "options": {"type": "array", "maxItems": 10, "items": {"$ref": "#/$defs/slack_option"}},
                "initial_values": {"type": "array", "items": {"type": "string"}},
            },
            "action_id",
            "options",
            "initial_values",
        ),
        "slack_radio_buttons": _node(
            "radio_buttons",
            {
                "action_id": {"type": "string", "maxLength": 255},
                "options": {"type": "array", "maxItems": 10, "items": {"$ref": "#/$defs/slack_option"}},
                "initial_value": {"type": ["string", "null"]},
            },
            "action_id",
            "options",
            "initial_value",
        ),
        "slack_block": {
            "oneOf": [
                {"$ref": f"#/$defs/slack_{kind}"}
                for kind in (
                    "section",
                    "header",
                    "context",
                    "divider",
                    "image",
                    "actions",
                    "input",
                    "table",
                    "card",
                    "carousel",
                    "alert",
                )
            ]
        },
        "slack_section": _node(
            "section",
            {
                "text": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/slack_text"}]},
                "fields": {"type": "array", "maxItems": 10, "items": {"$ref": "#/$defs/slack_text"}},
                "accessory": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/slack_element"}]},
            },
            "text",
            "fields",
            "accessory",
        ),
        "slack_header": _node("header", {"text": {"$ref": "#/$defs/slack_text"}}, "text"),
        "slack_context": _node(
            "context",
            {"elements": {"type": "array", "maxItems": 10, "items": {"$ref": "#/$defs/slack_text"}}},
            "elements",
        ),
        "slack_divider": _node("divider", {}),
        "slack_image": _node(
            "image",
            {
                "image_url": {"type": "string", "maxLength": 3000},
                "alt_text": {"type": "string", "maxLength": 2000},
                "title": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/slack_text"}]},
            },
            "image_url",
            "alt_text",
            "title",
        ),
        "slack_actions": _node(
            "actions",
            {
                "elements": {"type": "array", "maxItems": 25, "items": {"$ref": "#/$defs/slack_element"}},
                "block_id": {"type": ["string", "null"], "maxLength": 255},
            },
            "elements",
            "block_id",
        ),
        "slack_input": _node(
            "input",
            {
                "block_id": {"type": "string", "maxLength": 255},
                "label": {"$ref": "#/$defs/slack_text"},
                "element": {"$ref": "#/$defs/slack_input_element"},
                "optional": {"type": "boolean"},
                "hint": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/slack_text"}]},
            },
            "block_id",
            "label",
            "element",
            "optional",
            "hint",
        ),
        "slack_table": _node(
            "table",
            {
                "rows": {
                    "type": "array",
                    "maxItems": 100,
                    "items": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {"$ref": "#/$defs/slack_text"},
                    },
                }
            },
            "rows",
        ),
        "slack_card": _node(
            "card",
            {
                "title": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/slack_text"}]},
                "description": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/slack_text"}]},
                "image_url": {"type": ["string", "null"], "maxLength": 3000},
                "actions": {"type": "array", "items": {"$ref": "#/$defs/slack_button"}},
            },
            "title",
            "description",
            "image_url",
            "actions",
        ),
        "slack_carousel": _node(
            "carousel",
            {"cards": {"type": "array", "maxItems": 10, "items": {"$ref": "#/$defs/slack_card"}}},
            "cards",
        ),
        "slack_alert": _node(
            "alert",
            {
                "title": {"$ref": "#/$defs/slack_text"},
                "text": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/slack_text"}]},
                "style": {"enum": ["info", "success", "warning", "error"]},
            },
            "title",
            "text",
            "style",
        ),
        "html_node": {
            "oneOf": [
                {"$ref": f"#/$defs/{HtmlText.KIND}"},
                {"$ref": f"#/$defs/{HtmlElement.KIND}"},
            ]
        },
        "html_text": _node(
            HtmlText.KIND,
            {"content": {"type": "string"}, "markup": {"enum": ["plain", "discord-markdown"]}},
            "content",
            "markup",
        ),
        "html_element": _node(
            HtmlElement.KIND,
            {
                "tag": {"enum": [tag.value for tag in HtmlTag]},
                "children": {"type": "array", "items": {"$ref": "#/$defs/html_node"}},
                "attributes": {"type": "array", "items": {"$ref": "#/$defs/html_attribute"}},
                "action": _nullable(
                    {"action": {"type": "string"}, "mode": {"enum": [mode.value for mode in ActionMode]}},
                    "action",
                    "mode",
                ),
                "route": _nullable({"route_id": {"type": "string"}}, "route_id"),
                "form": _nullable(
                    {"key": {"type": "string"}, "field_name": {"type": ["string", "null"]}},
                    "key",
                    "field_name",
                ),
                "url": _nullable({"url": {"type": "string"}}, "url"),
                "time": _nullable(
                    {
                        "instant": {"type": "string"},
                        "timezone": {"type": ["string", "null"]},
                        "style": {"type": ["string", "null"]},
                    },
                    "instant",
                    "timezone",
                    "style",
                ),
                "colour": _nullable({"value": {"type": "integer", "minimum": 0, "maximum": 16777215}}, "value"),
                "asset": _nullable(
                    {
                        "key": {"type": "string"},
                        "name": {"type": "string"},
                        "media_type": {"type": "string"},
                    },
                    "key",
                    "name",
                    "media_type",
                ),
            },
            "tag",
            "children",
            "attributes",
            "action",
            "route",
            "form",
            "url",
            "time",
            "colour",
            "asset",
        ),
        "html_attribute": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"enum": [name.value for name in HtmlAttributeName]},
                "value": {"type": ["string", "integer", "number", "boolean"]},
            },
            "required": ["name", "value"],
        },
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
                    Text.KIND,
                    Time.KIND,
                    ZonedTime.KIND,
                    File.KIND,
                    Separator.KIND,
                    Link.KIND,
                    Button.KIND,
                    RoutedButton.KIND,
                    PremiumButton.KIND,
                    Select.KIND,
                    RoutedSelect.KIND,
                    EntitySelect.KIND,
                    Row.KIND,
                    Thumbnail.KIND,
                    Gallery.KIND,
                    Section.KIND,
                    Panel.KIND,
                    Extension.KIND,
                )
            ]
        },
        "text": _node(
            Text.KIND,
            {"content": {"type": "string"}, "markup": {"enum": ["plain", "discord-markdown"]}},
            "content",
            "markup",
        ),
        "time": _node(
            Time.KIND,
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
            ZonedTime.KIND,
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
            File.KIND,
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
            Separator.KIND,
            {"large": {"type": "boolean"}, "visible": {"type": "boolean"}},
            "large",
            "visible",
        ),
        "link": _node(
            Link.KIND,
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
            PremiumButton.KIND,
            {"sku_id": {"type": "integer", "minimum": 1}},
            "sku_id",
        ),
        "button": _node(
            Button.KIND,
            {
                "label": {"type": ["string", "null"]},
                "action": {"type": "string"},
                "style": {"enum": ["primary", "secondary", "success", "danger"]},
                "emoji": {"$ref": "#/$defs/emoji"},
                "disabled": {"type": "boolean"},
                "mode": {"enum": ["exclusive", "rebase", "parallel_read", "immediate"]},
            },
            "label",
            "action",
            "style",
            "emoji",
            "disabled",
            "mode",
        ),
        "routed_button": _node(
            RoutedButton.KIND,
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
            Select.KIND,
            {
                "options": {"type": "array", "items": {"$ref": "#/$defs/option"}},
                "action": {"type": "string"},
                "placeholder": {"type": ["string", "null"]},
                "min_values": {"type": "integer", "minimum": 0},
                "max_values": {"type": "integer", "minimum": 0},
                "disabled": {"type": "boolean"},
                "mode": {"enum": ["exclusive", "rebase", "parallel_read", "immediate"]},
            },
            "options",
            "action",
            "placeholder",
            "min_values",
            "max_values",
            "disabled",
            "mode",
        ),
        "routed_select": _node(
            RoutedSelect.KIND,
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
            EntitySelect.KIND,
            {
                "entity_type": {"enum": ["user", "role", "conversation", "mentionable"]},
                "action": {"type": "string"},
                "placeholder": {"type": ["string", "null"], "maxLength": 150},
                "default_values": {
                    "type": "array",
                    "maxItems": 25,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "kind": {"enum": ["user", "role", "conversation"]},
                            "id": {
                                "oneOf": [
                                    {"type": "integer", "minimum": 1},
                                    {"type": "string", "minLength": 1},
                                ]
                            },
                        },
                        "required": ["kind", "id"],
                    },
                },
                "conversation_types": {
                    "type": "array",
                    "items": {
                        "enum": [
                            "guild_text",
                            "guild_voice",
                            "guild_category",
                            "guild_announcement",
                            "guild_announcement_thread",
                            "guild_public_thread",
                            "guild_private_thread",
                            "guild_stage_voice",
                            "guild_forum",
                            "guild_media",
                            "workspace_public",
                            "workspace_private",
                            "direct",
                            "group_direct",
                        ]
                    },
                },
                "min_values": {"type": "integer", "minimum": 0, "maximum": 25},
                "max_values": {"type": "integer", "minimum": 0, "maximum": 25},
                "disabled": {"type": "boolean"},
                "mode": {"enum": ["exclusive", "rebase", "parallel_read", "immediate"]},
            },
            "entity_type",
            "action",
            "placeholder",
            "default_values",
            "conversation_types",
            "min_values",
            "max_values",
            "disabled",
            "mode",
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
            Row.KIND,
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
            Thumbnail.KIND,
            {
                "url": {"type": "string"},
                "description": {"type": ["string", "null"]},
                "spoiler": {"type": "boolean"},
            },
            "url",
            "description",
        ),
        "gallery": _node(
            Gallery.KIND,
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
            Section.KIND,
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
            Panel.KIND,
            {
                "children": {"type": "array", "items": {"$ref": "#/$defs/node"}},
                "accent": {"type": ["integer", "null"], "minimum": 0, "maximum": 16777215},
                "spoiler": {"type": "boolean"},
            },
            "children",
            "accent",
        ),
        "extension": _node(
            Extension.KIND,
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
