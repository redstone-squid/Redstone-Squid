"""Canonical wire encoding for this target's scene bodies."""

import json
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from squid_ui.ext.discord.scene import model
from squid_ui.ext.discord.scene.model import (
    Button,
    ClassicMessage,
    ClassicRow,
    ComponentsV2,
    EntitySelect,
    Extension,
    File,
    Gallery,
    Link,
    Node,
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
from squid_ui.ext.discord.scene.schema import SCHEMA
from squid_ui.scene._json import JsonObject, JsonReader
from squid_ui.scene.codec import CodecError, SceneDialect

_json = JsonReader(CodecError)
_text_adapter = TypeAdapter(Text)
_components_adapter = TypeAdapter(ComponentsV2)
_classic_adapter = TypeAdapter(ClassicMessage)
_classic_row_adapter = TypeAdapter(ClassicRow)
_row_adapter = TypeAdapter(Row)
_section_adapter = TypeAdapter(Section)
_panel_adapter = TypeAdapter(Panel)
_extension_adapter = TypeAdapter(Extension)
_leaf_adapters = {
    model.KIND: TypeAdapter(model)
    for model in (Link, Button, RoutedButton, Select, RoutedSelect, EntitySelect, Thumbnail, Gallery)
}


def _body_to_dict(body: ComponentsV2 | ClassicMessage) -> dict[str, Any]:
    match body:
        case ComponentsV2(children=children):
            payload = _components_adapter.dump_python(body, mode="json")
            payload["children"] = [_node_to_dict(child) for child in children]
            return {"kind": ComponentsV2.KIND, **payload}
        case ClassicMessage(rows=rows):
            payload = _classic_adapter.dump_python(body, mode="json")
            payload["rows"] = [{"controls": [_node_to_dict(control) for control in row.controls]} for row in rows]
            return {"kind": ClassicMessage.KIND, **payload}


def _body_from_dict(raw: JsonObject) -> ComponentsV2 | ClassicMessage:
    kind = _json.string(raw, "kind")
    match kind:
        case ComponentsV2.KIND:
            payload = {key: value for key, value in raw.items() if key != "kind"}
            payload["children"] = tuple(
                _node_from_dict(_json.require_object(child)) for child in _json.array(raw, "children")
            )
            return SceneDialect.validate(_components_adapter, payload)
        case ClassicMessage.KIND:
            payload = {key: value for key, value in raw.items() if key != "kind"}
            payload["rows"] = tuple(_row_from_dict(_json.require_object(row)) for row in _json.array(raw, "rows"))
            return SceneDialect.validate(_classic_adapter, payload)
        case _:
            msg = f"unknown scene body kind {kind!r}"
            raise CodecError(msg)


def _node_to_dict(node: Node | Link | PremiumButton | Button | RoutedButton) -> dict[str, Any]:
    match node:
        case Text():
            return {"kind": Text.KIND, **_text_adapter.dump_python(node, mode="json")}
        case Time() | ZonedTime() | File() | Separator() | PremiumButton():
            return {"kind": node.KIND, **node.model_dump(mode="json")}
        case Link() | Button() | RoutedButton() | Select() | RoutedSelect() | EntitySelect() | Thumbnail() | Gallery():
            return {"kind": node.KIND, **_leaf_adapters[node.KIND].dump_python(node, mode="json")}
        case Row(items=items):
            payload = _row_adapter.dump_python(node, mode="json")
            payload["items"] = [_node_to_dict(item) for item in items]
            return {"kind": Row.KIND, **payload}
        case Section(texts=texts, accessory=accessory):
            payload = _section_adapter.dump_python(node, mode="json")
            payload["texts"] = [_node_to_dict(text) for text in texts]
            payload["accessory"] = _node_to_dict(accessory)
            return {"kind": Section.KIND, **payload}
        case Panel(children=children):
            payload = _panel_adapter.dump_python(node, mode="json")
            payload["children"] = [_node_to_dict(child) for child in children]
            return {"kind": Panel.KIND, **payload}
        case Extension(kind=kind, payload=payload):
            # Round-trip through json now so errors point at planning rather than a remote renderer.
            try:
                normalized = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
            except (TypeError, ValueError) as error:
                msg = f"extension {kind!r} payload is not JSON serializable"
                raise CodecError(msg) from error
            data = _extension_adapter.dump_python(node, mode="json")
            data["payload"] = normalized
            data["extension"] = data.pop("kind")
            return {"kind": Extension.KIND, **data}


def _row_from_dict(raw: JsonObject) -> ClassicRow:
    payload = dict(raw)
    payload["controls"] = tuple(
        _node_from_dict(_json.require_object(control)) for control in _json.array(raw, "controls")
    )
    return SceneDialect.validate(_classic_row_adapter, payload)


def _node_from_dict(
    raw: JsonObject,
) -> Node | Link | PremiumButton | Button | RoutedButton:
    kind = _json.string(raw, "kind")
    match kind:
        case Text.KIND:
            try:
                return _text_adapter.validate_python({key: value for key, value in raw.items() if key != "kind"})
            except ValidationError as error:
                raise CodecError(str(error)) from error
        case Time.KIND:
            return _model_node_from_dict(Time, raw)
        case ZonedTime.KIND:
            return _model_node_from_dict(ZonedTime, raw)
        case File.KIND:
            return _model_node_from_dict(File, raw)
        case Separator.KIND:
            return _model_node_from_dict(Separator, raw)
        case Link.KIND | Button.KIND | RoutedButton.KIND | Select.KIND | RoutedSelect.KIND | EntitySelect.KIND:
            return _leaf_node_from_dict(kind, raw)
        case PremiumButton.KIND:
            return _model_node_from_dict(PremiumButton, raw)
        case Row.KIND:
            payload = {key: value for key, value in raw.items() if key != "kind"}
            payload["items"] = tuple(_node_from_dict(_json.require_object(item)) for item in _json.array(raw, "items"))
            return SceneDialect.validate(_row_adapter, payload)
        case Thumbnail.KIND | Gallery.KIND:
            return _leaf_node_from_dict(kind, raw)
        case Section.KIND:
            payload = {key: value for key, value in raw.items() if key != "kind"}
            payload["texts"] = tuple(_node_from_dict(_json.require_object(text)) for text in _json.array(raw, "texts"))
            payload["accessory"] = _node_from_dict(_json.require_object(raw.get("accessory")))
            return SceneDialect.validate(_section_adapter, payload)
        case Panel.KIND:
            payload = {key: value for key, value in raw.items() if key != "kind"}
            payload["children"] = tuple(
                _node_from_dict(_json.require_object(child)) for child in _json.array(raw, "children")
            )
            return SceneDialect.validate(_panel_adapter, payload)
        case Extension.KIND:
            payload = {key: value for key, value in raw.items() if key not in {"kind", "extension"}}
            payload["kind"] = _json.string(raw, "extension")
            return SceneDialect.validate(_extension_adapter, payload)
        case _:
            msg = f"unknown scene node kind {kind!r}"
            raise CodecError(msg)


def _model_node_from_dict[NodeT: BaseModel](model: type[NodeT], raw: JsonObject) -> NodeT:
    try:
        return model.model_validate({key: value for key, value in raw.items() if key != "kind"})
    except ValidationError as error:
        raise CodecError(str(error)) from error


def _leaf_node_from_dict(kind: str, raw: JsonObject) -> Node:
    try:
        return _leaf_adapters[kind].validate_python({key: value for key, value in raw.items() if key != "kind"})
    except ValidationError as error:
        raise CodecError(str(error)) from error


DIALECT = SceneDialect[Any](
    "discord",
    (ComponentsV2, ClassicMessage),
    _body_to_dict,
    _body_from_dict,
    SCHEMA.fragment,
    query_types={
        f"scene.{name}": value
        for name, value in vars(model).items()
        if isinstance(value, type) and value.__module__ == model.__name__
    },
)
