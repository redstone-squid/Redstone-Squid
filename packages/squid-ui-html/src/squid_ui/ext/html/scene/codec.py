"""Canonical wire encoding for this target's scene bodies."""

from typing import Any

from pydantic import TypeAdapter, ValidationError

from squid_ui.ext.html.scene import model
from squid_ui.ext.html.scene.model import HtmlBody, HtmlElement, HtmlNode, HtmlText
from squid_ui.ext.html.scene.schema import SCHEMA
from squid_ui.scene._json import JsonObject, JsonReader
from squid_ui.scene.codec import CodecError, SceneDialect

_json = JsonReader(CodecError)
_html_text_adapter = TypeAdapter(HtmlText)
_html_element_adapter = TypeAdapter(HtmlElement)
_html_body_adapter = TypeAdapter(HtmlBody)


def _body_to_dict(body: HtmlBody) -> dict[str, Any]:
    match body:
        case HtmlBody(children=children):
            payload = _html_body_adapter.dump_python(body, mode="json")
            payload["children"] = [_html_node_to_dict(child) for child in children]
            return {"kind": HtmlBody.KIND, **payload}


def _body_from_dict(raw: JsonObject) -> HtmlBody:
    kind = _json.string(raw, "kind")
    match kind:
        case HtmlBody.KIND:
            payload = {key: value for key, value in raw.items() if key != "kind"}
            payload["children"] = tuple(
                _html_node_from_dict(_json.require_object(child)) for child in _json.array(raw, "children")
            )
            return SceneDialect.validate(_html_body_adapter, payload)
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


DIALECT = SceneDialect[Any](
    "html",
    (HtmlBody,),
    _body_to_dict,
    _body_from_dict,
    SCHEMA.fragment,
    query_types={
        f"scene.{name}": value
        for name, value in vars(model).items()
        if isinstance(value, type) and value.__module__ == model.__name__
    },
)
