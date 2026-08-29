"""Canonical data conversion for Slack scene bodies."""

from collections.abc import Mapping
from typing import Any, cast

from squid_ui.entity import ConversationType
from squid_ui.errors import SquidUiError
from squid_ui.interactions import ActionMode
from squid_ui.scene.slack import (
    SlackActionRef,
    SlackActions,
    SlackAlert,
    SlackAlertStyle,
    SlackBlock,
    SlackBody,
    SlackButton,
    SlackButtonStyle,
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
    SlackOption,
    SlackRadioButtons,
    SlackRouteRef,
    SlackSection,
    SlackSelect,
    SlackSelectKind,
    SlackTable,
    SlackText,
    SlackTextInput,
    SlackTextKind,
    SlackTimePicker,
)


class SlackSceneCodecError(SquidUiError, ValueError):
    """A Slack scene body has an invalid shape."""


def _object(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        message = f"{name} must be an object"
        raise SlackSceneCodecError(message)
    return value


def _array(raw: Mapping[str, Any], key: str) -> list[Any]:
    value = raw.get(key)
    if not isinstance(value, list):
        message = f"{key} must be an array"
        raise SlackSceneCodecError(message)
    return value


def _string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        message = f"{key} must be a string"
        raise SlackSceneCodecError(message)
    return value


def _optional_string(raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is not None and not isinstance(value, str):
        message = f"{key} must be a string or null"
        raise SlackSceneCodecError(message)
    return value


def _integer(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"{key} must be an integer"
        raise SlackSceneCodecError(message)
    return value


def _boolean(raw: Mapping[str, Any], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        message = f"{key} must be a boolean"
        raise SlackSceneCodecError(message)
    return value


def _optional_object(raw: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    value = raw.get(key)
    return None if value is None else _object(value, key)


def _text(value: SlackText) -> dict[str, object]:
    return {
        "content": value.content,
        "kind": value.kind.value,
        "emoji": value.emoji,
        "verbatim": value.verbatim,
    }


def _text_from(value: object) -> SlackText:
    raw = _object(value, "Slack text")
    emoji = raw.get("emoji")
    verbatim = raw.get("verbatim")
    if (emoji is not None and not isinstance(emoji, bool)) or (verbatim is not None and not isinstance(verbatim, bool)):
        message = "Slack text emoji and verbatim flags must be booleans or null"
        raise SlackSceneCodecError(message)
    return SlackText(_string(raw, "content"), SlackTextKind(_string(raw, "kind")), emoji, verbatim)


def _option(value: SlackOption) -> dict[str, object]:
    return {
        "label": _text(value.label),
        "value": value.value,
        "description": None if value.description is None else _text(value.description),
    }


def _option_from(value: object) -> SlackOption:
    raw = _object(value, "Slack option")
    description = raw.get("description")
    return SlackOption(
        _text_from(raw.get("label")),
        _string(raw, "value"),
        None if description is None else _text_from(description),
    )


def _action(value: SlackActionRef | None) -> dict[str, object] | None:
    return None if value is None else {"action": value.action, "mode": value.mode.value}


def _action_from(value: object) -> SlackActionRef | None:
    if value is None:
        return None
    raw = _object(value, "Slack action")
    return SlackActionRef(_string(raw, "action"), ActionMode(_string(raw, "mode")))


def _route(value: SlackRouteRef | None) -> dict[str, object] | None:
    return None if value is None else {"route_id": value.route_id}


def _route_from(value: object) -> SlackRouteRef | None:
    if value is None:
        return None
    return SlackRouteRef(_string(_object(value, "Slack route"), "route_id"))


def _element(value: SlackElement) -> dict[str, object]:
    match value:
        case SlackButton(label, action, route, url, held_value, style):
            return {
                "kind": "button",
                "label": _text(label),
                "action": _action(action),
                "route": _route(route),
                "url": url,
                "value": held_value,
                "style": style.value,
            }
        case SlackSelect(
            action,
            route,
            kind,
            placeholder,
            options,
            initial_values,
            conversation_types,
            minimum,
            maximum,
        ):
            return {
                "kind": "select",
                "action": _action(action),
                "route": _route(route),
                "select_kind": kind.value,
                "placeholder": None if placeholder is None else _text(placeholder),
                "options": [_option(option) for option in options],
                "initial_values": list(initial_values),
                "conversation_types": [item.value for item in conversation_types],
                "minimum": minimum,
                "maximum": maximum,
            }
        case SlackTextInput(action_id, initial_value, placeholder, multiline, minimum_length, maximum_length):
            return {
                "kind": "text_input",
                "action_id": action_id,
                "initial_value": initial_value,
                "placeholder": None if placeholder is None else _text(placeholder),
                "multiline": multiline,
                "minimum_length": minimum_length,
                "maximum_length": maximum_length,
            }
        case SlackNumberInput(action_id, initial_value, decimal_allowed, minimum, maximum):
            return {
                "kind": "number_input",
                "action_id": action_id,
                "initial_value": initial_value,
                "decimal_allowed": decimal_allowed,
                "minimum": minimum,
                "maximum": maximum,
            }
        case SlackDatePicker(action_id, initial_date, placeholder):
            return {
                "kind": "date_picker",
                "action_id": action_id,
                "initial_date": initial_date,
                "placeholder": None if placeholder is None else _text(placeholder),
            }
        case SlackTimePicker(action_id, initial_time, placeholder):
            return {
                "kind": "time_picker",
                "action_id": action_id,
                "initial_time": initial_time,
                "placeholder": None if placeholder is None else _text(placeholder),
            }
        case SlackCheckboxes(action_id, options, initial_values):
            return {
                "kind": "checkboxes",
                "action_id": action_id,
                "options": [_option(option) for option in options],
                "initial_values": list(initial_values),
            }
        case SlackRadioButtons(action_id, options, initial_value):
            return {
                "kind": "radio_buttons",
                "action_id": action_id,
                "options": [_option(option) for option in options],
                "initial_value": initial_value,
            }


def _element_from(value: object) -> SlackElement:
    raw = _object(value, "Slack element")
    kind = _string(raw, "kind")
    placeholder = raw.get("placeholder")
    match kind:
        case "button":
            return SlackButton(
                _text_from(raw.get("label")),
                _action_from(raw.get("action")),
                _route_from(raw.get("route")),
                _optional_string(raw, "url"),
                _optional_string(raw, "value"),
                SlackButtonStyle(_string(raw, "style")),
            )
        case "select":
            return SlackSelect(
                _action_from(raw.get("action")),
                _route_from(raw.get("route")),
                SlackSelectKind(_string(raw, "select_kind")),
                None if placeholder is None else _text_from(placeholder),
                tuple(_option_from(option) for option in _array(raw, "options")),
                tuple(
                    _string(_object({"value": item}, "initial value"), "value")
                    for item in _array(raw, "initial_values")
                ),
                tuple(ConversationType(item) for item in _array(raw, "conversation_types")),
                _integer(raw, "minimum"),
                _integer(raw, "maximum"),
            )
        case "text_input":
            return SlackTextInput(
                _string(raw, "action_id"),
                _optional_string(raw, "initial_value"),
                None if placeholder is None else _text_from(placeholder),
                _boolean(raw, "multiline"),
                cast(int | None, raw.get("minimum_length")),
                cast(int | None, raw.get("maximum_length")),
            )
        case "number_input":
            return SlackNumberInput(
                _string(raw, "action_id"),
                _optional_string(raw, "initial_value"),
                _boolean(raw, "decimal_allowed"),
                _optional_string(raw, "minimum"),
                _optional_string(raw, "maximum"),
            )
        case "date_picker":
            return SlackDatePicker(
                _string(raw, "action_id"),
                _optional_string(raw, "initial_date"),
                None if placeholder is None else _text_from(placeholder),
            )
        case "time_picker":
            return SlackTimePicker(
                _string(raw, "action_id"),
                _optional_string(raw, "initial_time"),
                None if placeholder is None else _text_from(placeholder),
            )
        case "checkboxes":
            return SlackCheckboxes(
                _string(raw, "action_id"),
                tuple(_option_from(option) for option in _array(raw, "options")),
                tuple(cast(str, item) for item in _array(raw, "initial_values")),
            )
        case "radio_buttons":
            return SlackRadioButtons(
                _string(raw, "action_id"),
                tuple(_option_from(option) for option in _array(raw, "options")),
                _optional_string(raw, "initial_value"),
            )
        case _:
            message = f"unknown Slack element kind {kind!r}"
            raise SlackSceneCodecError(message)


def _block(value: SlackBlock) -> dict[str, object]:
    match value:
        case SlackSection(text, fields, accessory):
            return {
                "kind": "section",
                "text": None if text is None else _text(text),
                "fields": [_text(item) for item in fields],
                "accessory": None if accessory is None else _element(accessory),
            }
        case SlackHeader(text):
            return {"kind": "header", "text": _text(text)}
        case SlackContext(elements):
            return {"kind": "context", "elements": [_text(item) for item in elements]}
        case SlackDivider():
            return {"kind": "divider"}
        case SlackImage(image_url, alt_text, title):
            return {
                "kind": "image",
                "image_url": image_url,
                "alt_text": alt_text,
                "title": None if title is None else _text(title),
            }
        case SlackActions(elements, block_id):
            return {"kind": "actions", "elements": [_element(item) for item in elements], "block_id": block_id}
        case SlackInput(block_id, label, element, optional, hint):
            return {
                "kind": "input",
                "block_id": block_id,
                "label": _text(label),
                "element": _element(element),
                "optional": optional,
                "hint": None if hint is None else _text(hint),
            }
        case SlackTable(rows):
            return {"kind": "table", "rows": [[_text(cell) for cell in row] for row in rows]}
        case SlackCard(title, description, image_url, actions):
            return {
                "kind": "card",
                "title": None if title is None else _text(title),
                "description": None if description is None else _text(description),
                "image_url": image_url,
                "actions": [_element(action) for action in actions],
            }
        case SlackCarousel(cards):
            return {"kind": "carousel", "cards": [_block(card) for card in cards]}
        case SlackAlert(title, text, style):
            return {
                "kind": "alert",
                "title": _text(title),
                "text": None if text is None else _text(text),
                "style": style.value,
            }


def _block_from(value: object) -> SlackBlock:
    raw = _object(value, "Slack block")
    kind = _string(raw, "kind")
    match kind:
        case "section":
            text = raw.get("text")
            accessory = raw.get("accessory")
            return SlackSection(
                None if text is None else _text_from(text),
                tuple(_text_from(item) for item in _array(raw, "fields")),
                None if accessory is None else _element_from(accessory),
            )
        case "header":
            return SlackHeader(_text_from(raw.get("text")))
        case "context":
            return SlackContext(tuple(_text_from(item) for item in _array(raw, "elements")))
        case "divider":
            return SlackDivider()
        case "image":
            title = raw.get("title")
            return SlackImage(
                _string(raw, "image_url"),
                _string(raw, "alt_text"),
                None if title is None else _text_from(title),
            )
        case "actions":
            elements = tuple(_element_from(item) for item in _array(raw, "elements"))
            if not all(isinstance(item, SlackButton | SlackSelect) for item in elements):
                message = "Slack actions contain an unsupported element"
                raise SlackSceneCodecError(message)
            return SlackActions(
                cast(tuple[SlackButton | SlackSelect, ...], elements), _optional_string(raw, "block_id")
            )
        case "input":
            element = _element_from(raw.get("element"))
            if isinstance(element, SlackButton):
                message = "Slack input blocks cannot contain buttons"
                raise SlackSceneCodecError(message)
            hint = raw.get("hint")
            return SlackInput(
                _string(raw, "block_id"),
                _text_from(raw.get("label")),
                element,
                _boolean(raw, "optional"),
                None if hint is None else _text_from(hint),
            )
        case "table":
            return SlackTable(
                tuple(tuple(_text_from(cell) for cell in cast(list[Any], row)) for row in _array(raw, "rows"))
            )
        case "card":
            title = raw.get("title")
            description = raw.get("description")
            actions = tuple(_element_from(item) for item in _array(raw, "actions"))
            if not all(isinstance(item, SlackButton) for item in actions):
                message = "Slack card actions must be buttons"
                raise SlackSceneCodecError(message)
            return SlackCard(
                None if title is None else _text_from(title),
                None if description is None else _text_from(description),
                _optional_string(raw, "image_url"),
                cast(tuple[SlackButton, ...], actions),
            )
        case "carousel":
            cards = tuple(_block_from(item) for item in _array(raw, "cards"))
            if not all(isinstance(item, SlackCard) for item in cards):
                message = "Slack carousels must contain cards"
                raise SlackSceneCodecError(message)
            return SlackCarousel(cast(tuple[SlackCard, ...], cards))
        case "alert":
            text = raw.get("text")
            return SlackAlert(
                _text_from(raw.get("title")),
                None if text is None else _text_from(text),
                SlackAlertStyle(_string(raw, "style")),
            )
        case _:
            message = f"unknown Slack block kind {kind!r}"
            raise SlackSceneCodecError(message)


def slack_body_to_dict(body: SlackBody) -> dict[str, object]:
    """Encode one Slack body into canonical JSON-compatible data."""
    match body:
        case SlackMessage(text, blocks):
            return {"kind": SlackMessage.KIND, "text": text, "blocks": [_block(block) for block in blocks]}
        case SlackModalView(callback_id, title, submit, close, blocks, private_metadata):
            return {
                "kind": SlackModalView.KIND,
                "callback_id": callback_id,
                "title": _text(title),
                "submit": _text(submit),
                "close": _text(close),
                "blocks": [_block(block) for block in blocks],
                "private_metadata": private_metadata,
            }
        case SlackHomeView(blocks, callback_id, private_metadata):
            return {
                "kind": SlackHomeView.KIND,
                "blocks": [_block(block) for block in blocks],
                "callback_id": callback_id,
                "private_metadata": private_metadata,
            }


def slack_body_from_dict(raw: Mapping[str, Any]) -> SlackBody:
    """Decode one Slack body from canonical JSON-compatible data."""
    kind = _string(raw, "kind")
    blocks = tuple(_block_from(block) for block in _array(raw, "blocks"))
    match kind:
        case SlackMessage.KIND:
            return SlackMessage(_string(raw, "text"), blocks)
        case SlackModalView.KIND:
            return SlackModalView(
                _string(raw, "callback_id"),
                _text_from(raw.get("title")),
                _text_from(raw.get("submit")),
                _text_from(raw.get("close")),
                blocks,
                _optional_string(raw, "private_metadata"),
            )
        case SlackHomeView.KIND:
            return SlackHomeView(
                blocks,
                _optional_string(raw, "callback_id"),
                _optional_string(raw, "private_metadata"),
            )
        case _:
            message = f"unknown Slack body kind {kind!r}"
            raise SlackSceneCodecError(message)


__all__ = ["SlackSceneCodecError", "slack_body_from_dict", "slack_body_to_dict"]
