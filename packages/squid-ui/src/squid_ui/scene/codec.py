"""Canonical JSON codec for resolved scenes."""

import hashlib
import json
from copy import deepcopy
from typing import Any, Never

from pydantic import BaseModel, TypeAdapter, ValidationError

from squid_ui.errors import SquidUiError
from squid_ui.scene._json import JsonObject, JsonReader
from squid_ui.scene.model import (
    Asset,
    Body,
    Button,
    ClassicMessage,
    ClassicRow,
    ComponentsV2,
    EntitySelect,
    Extension,
    File,
    Gallery,
    HtmlBody,
    HtmlElement,
    HtmlNode,
    HtmlText,
    Link,
    Node,
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
_text_adapter = TypeAdapter(Text)
_html_text_adapter = TypeAdapter(HtmlText)
_html_element_adapter = TypeAdapter(HtmlElement)
_components_adapter = TypeAdapter(ComponentsV2)
_classic_adapter = TypeAdapter(ClassicMessage)
_classic_row_adapter = TypeAdapter(ClassicRow)
_html_body_adapter = TypeAdapter(HtmlBody)
_row_adapter = TypeAdapter(Row)
_section_adapter = TypeAdapter(Section)
_panel_adapter = TypeAdapter(Panel)
_extension_adapter = TypeAdapter(Extension)
_leaf_adapters = {
    model.KIND: TypeAdapter(model)
    for model in (Link, Button, RoutedButton, Select, RoutedSelect, EntitySelect, Thumbnail, Gallery)
}


def _reject_json_constant(value: str) -> Never:
    message = f"non-finite JSON number {value}"
    raise ValueError(message)


class Codec:
    """Encode and decode resolved-scene protocol 1 with deterministic JSON output.

    This is the Scene's transport and storage format, before conversion to a target's
    native payload. Action keys and asset declarations survive serialization; Python
    handlers and asset bytes stay in PlanResult. Sorted JSON also gives fingerprint
    a stable input for comparing planned displays.

    Decoding checks supported node shapes and selected model invariants, but does
    not run the published JSON Schema or enforce every target limit. Pydantic
    values reject unknown fields; dataclass adapters ignore them.
    Use `schema()` with a JSON Schema validator for the full structural contract.

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
            raw = json.loads(payload, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, ValueError) as error:
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
            "assets": [asset.model_dump(mode="json") for asset in scene.assets],
            "pagers": [pager.model_dump(mode="json") for pager in scene.pagers],
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
        try:
            declared_assets = tuple(Asset.model_validate(_json.require_object(asset)) for asset in assets)
            declared_pagers = tuple(Pager.model_validate(_json.require_object(pager)) for pager in pagers)
        except ValidationError as error:
            raise CodecError(str(error)) from error
        return Scene(
            protocol=protocol,
            target=_json.string(raw, "target"),
            target_version=_json.integer(raw, "target_version"),
            body=_body_from_dict(_json.require_object(raw.get("body"))),
            assets=declared_assets,
            pagers=declared_pagers,
        )


def _body_to_dict(body: Body) -> dict[str, Any]:
    match body:
        case ComponentsV2(children=children):
            payload = _components_adapter.dump_python(body, mode="json")
            payload["children"] = [_node_to_dict(child) for child in children]
            return {"kind": ComponentsV2.KIND, **payload}
        case ClassicMessage(rows=rows):
            payload = _classic_adapter.dump_python(body, mode="json")
            payload["rows"] = [{"controls": [_node_to_dict(control) for control in row.controls]} for row in rows]
            return {"kind": ClassicMessage.KIND, **payload}
        case HtmlBody(children=children):
            payload = _html_body_adapter.dump_python(body, mode="json")
            payload["children"] = [_html_node_to_dict(child) for child in children]
            return {"kind": HtmlBody.KIND, **payload}
        case SlackMessage() | SlackModalView() | SlackHomeView():
            return slack_body_to_dict(body)


def _body_from_dict(raw: JsonObject) -> Body:
    kind = _json.string(raw, "kind")
    match kind:
        case ComponentsV2.KIND:
            payload = {key: value for key, value in raw.items() if key != "kind"}
            payload["children"] = tuple(
                _node_from_dict(_json.require_object(child)) for child in _json.array(raw, "children")
            )
            return _validate_dataclass(_components_adapter, payload)
        case ClassicMessage.KIND:
            payload = {key: value for key, value in raw.items() if key != "kind"}
            payload["rows"] = tuple(_row_from_dict(_json.require_object(row)) for row in _json.array(raw, "rows"))
            return _validate_dataclass(_classic_adapter, payload)
        case HtmlBody.KIND:
            payload = {key: value for key, value in raw.items() if key != "kind"}
            payload["children"] = tuple(
                _html_node_from_dict(_json.require_object(child)) for child in _json.array(raw, "children")
            )
            return _validate_dataclass(_html_body_adapter, payload)
        case SlackMessage.KIND | SlackModalView.KIND | SlackHomeView.KIND:
            try:
                return slack_body_from_dict(raw)
            except SlackSceneCodecError as error:
                raise CodecError(str(error)) from error
        case _:
            msg = f"unknown scene body kind {kind!r}"
            raise CodecError(msg)


def _html_node_to_dict(node: HtmlNode) -> dict[str, Any]:
    if isinstance(node, HtmlText):
        return {"kind": HtmlText.KIND, **_html_text_adapter.dump_python(node, mode="json")}
    payload = _html_element_adapter.dump_python(node, mode="json")
    payload["children"] = [_html_node_to_dict(child) for child in node.children]
    return {"kind": HtmlElement.KIND, **payload}


def _html_node_from_dict(raw: JsonObject) -> HtmlNode:
    kind = _json.string(raw, "kind")
    if kind == HtmlText.KIND:
        adapter = _html_text_adapter
    elif kind == HtmlElement.KIND:
        adapter = _html_element_adapter
    else:
        msg = f"unknown HTML scene node kind {kind!r}"
        raise CodecError(msg)
    payload = {key: value for key, value in raw.items() if key != "kind"}
    if kind == HtmlElement.KIND:
        payload["children"] = tuple(
            _html_node_from_dict(_json.require_object(child)) for child in _json.array(raw, "children")
        )
    try:
        return adapter.validate_python(payload)
    except ValidationError as error:
        raise CodecError(str(error)) from error


def _row_from_dict(raw: JsonObject) -> ClassicRow:
    payload = dict(raw)
    payload["controls"] = tuple(
        _node_from_dict(_json.require_object(control)) for control in _json.array(raw, "controls")
    )
    return _validate_dataclass(_classic_row_adapter, payload)


def _validate_dataclass[ValueT](adapter: TypeAdapter[ValueT], payload: JsonObject) -> ValueT:
    try:
        return adapter.validate_python(payload)
    except ValidationError as error:
        raise CodecError(str(error)) from error


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


def _model_node_from_dict[NodeT: BaseModel](model: type[NodeT], raw: JsonObject) -> NodeT:
    try:
        return model.model_validate({key: value for key, value in raw.items() if key != "kind"})
    except ValidationError as error:
        raise CodecError(str(error)) from error


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
            return _validate_dataclass(_row_adapter, payload)
        case Thumbnail.KIND | Gallery.KIND:
            return _leaf_node_from_dict(kind, raw)
        case Section.KIND:
            payload = {key: value for key, value in raw.items() if key != "kind"}
            payload["texts"] = tuple(_node_from_dict(_json.require_object(text)) for text in _json.array(raw, "texts"))
            payload["accessory"] = _node_from_dict(_json.require_object(raw.get("accessory")))
            return _validate_dataclass(_section_adapter, payload)
        case Panel.KIND:
            payload = {key: value for key, value in raw.items() if key != "kind"}
            payload["children"] = tuple(
                _node_from_dict(_json.require_object(child)) for child in _json.array(raw, "children")
            )
            return _validate_dataclass(_panel_adapter, payload)
        case Extension.KIND:
            payload = {key: value for key, value in raw.items() if key not in {"kind", "extension"}}
            payload["kind"] = _json.string(raw, "extension")
            return _validate_dataclass(_extension_adapter, payload)
        case _:
            msg = f"unknown scene node kind {kind!r}"
            raise CodecError(msg)


def _leaf_node_from_dict(kind: str, raw: JsonObject) -> Node:
    try:
        return _leaf_adapters[kind].validate_python({key: value for key, value in raw.items() if key != "kind"})
    except ValidationError as error:
        raise CodecError(str(error)) from error
