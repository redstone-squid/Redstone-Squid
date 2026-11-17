"""JSON Schema for resolved-scene protocol 1.

Pydantic owns each structured value's fields. This module adds the transport's
``kind`` tags and the unions needed by consumers outside Python.
"""

from typing import Any

from pydantic import TypeAdapter

from squid_ui.content.emoji import Emoji
from squid_ui.content.entity import EntityRef
from squid_ui.scene import model as scene_model
from squid_ui.scene import slack as slack_scene

_SCENE_NODES = (
    scene_model.Text,
    scene_model.Time,
    scene_model.ZonedTime,
    scene_model.File,
    scene_model.Separator,
    scene_model.Link,
    scene_model.Button,
    scene_model.Row,
    scene_model.Select,
    scene_model.RoutedSelect,
    scene_model.EntitySelect,
    scene_model.RoutedButton,
    scene_model.PremiumButton,
    scene_model.Thumbnail,
    scene_model.Gallery,
    scene_model.Section,
    scene_model.Panel,
    scene_model.Extension,
)
_HTML_NODES = (scene_model.HtmlText, scene_model.HtmlElement)
_SLACK_ELEMENTS = (
    slack_scene.SlackButton,
    slack_scene.SlackSelect,
    slack_scene.SlackTextInput,
    slack_scene.SlackNumberInput,
    slack_scene.SlackDatePicker,
    slack_scene.SlackTimePicker,
    slack_scene.SlackCheckboxes,
    slack_scene.SlackRadioButtons,
)
_SLACK_BLOCKS = (
    slack_scene.SlackSection,
    slack_scene.SlackHeader,
    slack_scene.SlackContext,
    slack_scene.SlackDivider,
    slack_scene.SlackImage,
    slack_scene.SlackActions,
    slack_scene.SlackInput,
    slack_scene.SlackTable,
    slack_scene.SlackCard,
    slack_scene.SlackCarousel,
    slack_scene.SlackAlert,
)
_BODIES = (
    scene_model.ComponentsV2,
    scene_model.ClassicMessage,
    scene_model.HtmlBody,
    slack_scene.SlackMessage,
    slack_scene.SlackModalView,
    slack_scene.SlackHomeView,
)


def _schema() -> dict[str, Any]:
    defs: dict[str, Any] = {}
    tagged: dict[str, Any] = {}
    contracts = [
        *((model, model.KIND, model.KIND) for model in (*_SCENE_NODES, *_HTML_NODES)),
        (scene_model.ComponentsV2, "components_v2", "components_v2"),
        (scene_model.ClassicMessage, "classic_message", "classic_message"),
        (scene_model.HtmlBody, "html", "html"),
        *(
            (model, f"slack_{name}", name)
            for model, name in zip(
                _SLACK_ELEMENTS,
                (
                    "button",
                    "select",
                    "text_input",
                    "number_input",
                    "date_picker",
                    "time_picker",
                    "checkboxes",
                    "radio_buttons",
                ),
                strict=True,
            )
        ),
        *(
            (model, f"slack_{name}", name)
            for model, name in zip(
                _SLACK_BLOCKS,
                (
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
                ),
                strict=True,
            )
        ),
        (slack_scene.SlackMessage, "slack_message", "slack_message"),
        (slack_scene.SlackModalView, "slack_modal", "slack_modal"),
        (slack_scene.SlackHomeView, "slack_home", "slack_home"),
    ]
    for model, name, kind in contracts:
        generated = TypeAdapter(model).json_schema(ref_template="#/$defs/{model}")
        defs.update(generated.pop("$defs", {}))
        if "$ref" in generated:
            generated = defs[model.__name__]
        properties = dict(generated["properties"])
        if model is scene_model.Extension:
            properties["extension"] = properties.pop("kind")
        elif model is slack_scene.SlackSelect:
            properties["select_kind"] = properties.pop("kind")
        contract = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"kind": {"const": kind}, **properties},
            "required": ["kind", *properties],
        }
        defs[name] = contract
        tagged[model.__name__] = contract
    defs.update(tagged)

    for name, model in (
        ("embed", scene_model.Embed),
        ("asset", scene_model.Asset),
        ("pager", scene_model.Pager),
        ("classic_row", scene_model.ClassicRow),
        ("slack_text", slack_scene.SlackText),
        ("slack_option", slack_scene.SlackOption),
        ("slack_action", slack_scene.SlackActionRef | None),
        ("slack_route", slack_scene.SlackRouteRef | None),
        ("slack_asset", slack_scene.SlackAssetRef | None),
    ):
        generated = TypeAdapter(model).json_schema(ref_template="#/$defs/{model}")
        defs.update(generated.pop("$defs", {}))
        defs[name] = generated
    defs.update(tagged)

    # The codec emits defaults and nulls explicitly. Keep those fields required
    # in the canonical wire shape even when Python callers may omit them.
    plain_required = {
        scene_model.ClassicRow: ("controls",),
        scene_model.Option: ("label", "value", "description", "default"),
        scene_model.GalleryItem: ("url", "description"),
        scene_model.HtmlAttribute: ("name", "value"),
        scene_model.HtmlActionRef: ("action", "mode"),
        scene_model.HtmlRouteRef: ("route_id",),
        scene_model.HtmlFormRef: ("key", "field_name"),
        scene_model.HtmlUrlRef: ("url",),
        scene_model.HtmlTimeRef: ("instant", "timezone", "style"),
        scene_model.HtmlColourRef: ("value",),
        scene_model.HtmlAssetRef: ("key", "name", "media_type"),
        slack_scene.SlackText: ("content", "kind", "emoji", "verbatim"),
        slack_scene.SlackOption: ("label", "value", "description"),
        slack_scene.SlackActionRef: ("action", "mode"),
        slack_scene.SlackRouteRef: ("route_id",),
        slack_scene.SlackAssetRef: ("key", "name", "media_type"),
        Emoji: ("name", "id", "animated"),
        EntityRef: ("kind", "id"),
    }
    for model, required in plain_required.items():
        definition = defs[model.__name__]
        definition["additionalProperties"] = False
        definition["required"] = list(required)
    for name, model in (
        ("classic_row", scene_model.ClassicRow),
        ("slack_text", slack_scene.SlackText),
        ("slack_option", slack_scene.SlackOption),
    ):
        defs[name] = defs[model.__name__]

    def one_of(models: tuple[type, ...]) -> dict[str, Any]:
        return {"oneOf": [{"$ref": f"#/$defs/{model.__name__}"} for model in models]}

    defs.update(
        {
            "body": one_of(_BODIES),
            "node": one_of(_SCENE_NODES),
            "html_node": one_of(_HTML_NODES),
            "slack_element": one_of(_SLACK_ELEMENTS),
            "slack_input_element": one_of(_SLACK_ELEMENTS[1:]),
            "slack_block": one_of(_SLACK_BLOCKS),
        }
    )
    return {
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
            "assets": {"type": "array", "items": {"$ref": "#/$defs/asset"}},
            "pagers": {"type": "array", "items": {"$ref": "#/$defs/pager"}},
        },
        "required": ["protocol", "target", "target_version", "body", "assets", "pagers"],
        "$defs": defs,
    }


SCHEMA = _schema()
