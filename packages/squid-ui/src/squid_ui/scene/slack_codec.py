"""Convert Slack scene bodies to and from the scene protocol's JSON objects.

These objects use the scene vocabulary, not Slack API request shapes. The Slack
frontend performs that final conversion after resolving actions and assets.
"""

from pydantic import TypeAdapter, ValidationError

from squid_ui.errors import SquidUiError
from squid_ui.scene._json import JsonObject, JsonReader
from squid_ui.scene.slack import (
    SlackActions,
    SlackAlert,
    SlackBlock,
    SlackBody,
    SlackButton,
    SlackCard,
    SlackCarousel,
    SlackCheckboxes,
    SlackContext,
    SlackDatePicker,
    SlackDivider,
    SlackElement,
    SlackHeader,
    SlackHomeView,
    SlackImage,
    SlackInput,
    SlackMessage,
    SlackModalView,
    SlackNumberInput,
    SlackRadioButtons,
    SlackSection,
    SlackSelect,
    SlackSelectKind,
    SlackTable,
    SlackTextInput,
    SlackTimePicker,
)


class SlackSceneCodecError(SquidUiError, ValueError):
    """A Slack scene body has an invalid shape."""


_json = JsonReader(SlackSceneCodecError)
_ELEMENT_TYPES = {
    "button": SlackButton,
    "select": SlackSelect,
    "text_input": SlackTextInput,
    "number_input": SlackNumberInput,
    "date_picker": SlackDatePicker,
    "time_picker": SlackTimePicker,
    "checkboxes": SlackCheckboxes,
    "radio_buttons": SlackRadioButtons,
}
_BLOCK_TYPES = {
    "section": SlackSection,
    "header": SlackHeader,
    "context": SlackContext,
    "divider": SlackDivider,
    "image": SlackImage,
    "actions": SlackActions,
    "input": SlackInput,
    "table": SlackTable,
    "card": SlackCard,
    "carousel": SlackCarousel,
    "alert": SlackAlert,
}
_ELEMENT_ADAPTERS = {kind: TypeAdapter(model) for kind, model in _ELEMENT_TYPES.items()}
_BLOCK_ADAPTERS = {kind: TypeAdapter(model) for kind, model in _BLOCK_TYPES.items()}
_ELEMENT_KINDS = {model: kind for kind, model in _ELEMENT_TYPES.items()}
_BLOCK_KINDS = {model: kind for kind, model in _BLOCK_TYPES.items()}
_BODY_TYPES = {
    SlackMessage.KIND: SlackMessage,
    SlackModalView.KIND: SlackModalView,
    SlackHomeView.KIND: SlackHomeView,
}
_BODY_ADAPTERS = {kind: TypeAdapter(model) for kind, model in _BODY_TYPES.items()}


def _element(value: SlackElement) -> dict[str, object]:
    kind = _ELEMENT_KINDS[type(value)]
    payload = _ELEMENT_ADAPTERS[kind].dump_python(value, mode="json")
    if isinstance(value, SlackSelect):
        payload["select_kind"] = payload.pop("kind")
    return {"kind": kind, **payload}


def _element_from(value: object) -> SlackElement:
    raw = _json.require_object(value, name="Slack element")
    kind = _json.string(raw, "kind")
    adapter = _ELEMENT_ADAPTERS.get(kind)
    if adapter is None:
        message = f"unknown Slack element kind {kind!r}"
        raise SlackSceneCodecError(message)
    payload = {key: item for key, item in raw.items() if key != "kind"}
    if kind == "select":
        payload["kind"] = payload.pop("select_kind", SlackSelectKind.STATIC)
    try:
        return adapter.validate_python(payload)
    except ValidationError as error:
        raise SlackSceneCodecError(str(error)) from error


def _block(value: SlackBlock) -> dict[str, object]:
    kind = _BLOCK_KINDS[type(value)]
    payload = _BLOCK_ADAPTERS[kind].dump_python(value, mode="json")
    if isinstance(value, SlackSection) and value.accessory is not None:
        payload["accessory"] = _element(value.accessory)
    elif isinstance(value, SlackActions):
        payload["elements"] = [_element(item) for item in value.elements]
    elif isinstance(value, SlackInput):
        payload["element"] = _element(value.element)
    elif isinstance(value, SlackCard):
        payload["actions"] = [_element(item) for item in value.actions]
    elif isinstance(value, SlackCarousel):
        payload["cards"] = [_block(item) for item in value.cards]
    return {"kind": kind, **payload}


def _block_from(value: object) -> SlackBlock:
    raw = _json.require_object(value, name="Slack block")
    kind = _json.string(raw, "kind")
    adapter = _BLOCK_ADAPTERS.get(kind)
    if adapter is None:
        message = f"unknown Slack block kind {kind!r}"
        raise SlackSceneCodecError(message)
    payload = {key: item for key, item in raw.items() if key != "kind"}
    if kind == "section" and payload.get("accessory") is not None:
        payload["accessory"] = _element_from(payload["accessory"])
    elif kind == "actions":
        payload["elements"] = tuple(_element_from(item) for item in _json.array(payload, "elements"))
    elif kind == "input":
        payload["element"] = _element_from(payload.get("element"))
    elif kind == "card":
        payload["actions"] = tuple(_element_from(item) for item in _json.array(payload, "actions"))
    elif kind == "carousel":
        payload["cards"] = tuple(_block_from(item) for item in _json.array(payload, "cards"))
    try:
        return adapter.validate_python(payload)
    except ValidationError as error:
        raise SlackSceneCodecError(str(error)) from error


def slack_body_to_dict(body: SlackBody) -> dict[str, object]:
    """Encode one Slack body into canonical JSON-compatible data."""
    payload = _BODY_ADAPTERS[body.KIND].dump_python(body, mode="json")
    payload["blocks"] = [_block(block) for block in body.blocks]
    return {"kind": body.KIND, **payload}


def slack_body_from_dict(raw: JsonObject) -> SlackBody:
    """Decode a Slack body using its tagged block and element contracts."""
    kind = _json.string(raw, "kind")
    adapter = _BODY_ADAPTERS.get(kind)
    if adapter is None:
        message = f"unknown Slack body kind {kind!r}"
        raise SlackSceneCodecError(message)
    payload = {key: item for key, item in raw.items() if key != "kind"}
    payload["blocks"] = tuple(_block_from(block) for block in _json.array(raw, "blocks"))
    try:
        return adapter.validate_python(payload)
    except ValidationError as error:
        raise SlackSceneCodecError(str(error)) from error


__all__ = ["SlackSceneCodecError", "slack_body_from_dict", "slack_body_to_dict"]
