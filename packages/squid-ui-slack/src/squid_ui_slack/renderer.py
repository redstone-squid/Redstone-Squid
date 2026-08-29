"""Mechanical drawing of resolved Slack Block Kit scenes."""

import hashlib
from collections.abc import Callable, Sequence
from urllib.parse import urlsplit

from slack_sdk.errors import SlackObjectFormationError
from slack_sdk.models.blocks import (
    ActionsBlock,
    AlertBlock,
    Block,
    ButtonElement,
    CardBlock,
    CarouselBlock,
    CheckboxesElement,
    ContextBlock,
    ConversationFilter,
    ConversationMultiSelectElement,
    ConversationSelectElement,
    DatePickerElement,
    DividerBlock,
    HeaderBlock,
    ImageBlock,
    InputBlock,
    InputInteractiveElement,
    MarkdownTextObject,
    NumberInputElement,
    Option,
    PlainTextInputElement,
    PlainTextObject,
    RadioButtonsElement,
    RawTextObject,
    SectionBlock,
    StaticMultiSelectElement,
    StaticSelectElement,
    TableBlock,
    TimePickerElement,
    UserMultiSelectElement,
    UserSelectElement,
)
from slack_sdk.models.views import View

from squid_ui import scene
from squid_ui.assets import Asset, StoredAsset
from squid_ui.entity import ConversationType
from squid_ui.errors import DrawInvariantError
from squid_ui.planning.adapter import AdapterCapability, AdapterProfile
from squid_ui.renderer import Renderer
from squid_ui.scene.model import PlanResult
from squid_ui.slack.target import SLACK_HOME_LIMITS, SLACK_MESSAGE_LIMITS, SLACK_MODAL_LIMITS, SlackLimits
from squid_ui.target_types import SlackSdkAdapter
from squid_ui_slack.adapter import SLACK_SDK_343_ADAPTER, require_slack_sdk_capability
from squid_ui_slack.message_payload import MessagePayload

type AssetResolver = Callable[[scene.Asset], str | None]
type SdkText = PlainTextObject | MarkdownTextObject

_CONVERSATION_TYPES = {
    ConversationType.WORKSPACE_PUBLIC: "public",
    ConversationType.WORKSPACE_PRIVATE: "private",
    ConversationType.DIRECT: "im",
    ConversationType.GROUP_DIRECT: "mpim",
}


def _text(value: scene.SlackText) -> SdkText:
    if value.kind is scene.SlackTextKind.PLAIN:
        return PlainTextObject(text=value.content, emoji=value.emoji)
    return MarkdownTextObject(text=value.content, verbatim=value.verbatim)


def _plain(value: scene.SlackText, role: str) -> PlainTextObject:
    if value.kind is not scene.SlackTextKind.PLAIN:
        message = f"{role} requires Slack plain_text, not {value.kind.value}"
        raise DrawInvariantError(message)
    return PlainTextObject(text=value.content, emoji=value.emoji)


def _safe_url(value: str, *, https_only: bool = False) -> str | None:
    parsed = urlsplit(value)
    schemes = {"https"} if https_only else {"http", "https"}
    return value if parsed.scheme in schemes and parsed.netloc else None


def _generated_action_id(prefix: str, value: str) -> str:
    digest = hashlib.blake2s(value.encode(), digest_size=12).hexdigest()
    return f"squid:{prefix}:{digest}"


class _Drawer:
    def __init__(
        self,
        *,
        assets: Sequence[scene.Asset],
        plan: PlanResult[scene.SlackBody] | None,
        asset_resolver: AssetResolver | None,
    ) -> None:
        self.assets = {asset.key: asset for asset in assets}
        self.plan = plan
        self.asset_resolver = asset_resolver

    def blocks(self, values: Sequence[scene.SlackBlock]) -> tuple[Block, ...]:
        return tuple(self.block(value) for value in values)

    def block(self, value: scene.SlackBlock) -> Block:
        match value:
            case scene.SlackSection(text=text, fields=fields, accessory=accessory):
                return SectionBlock(
                    text=None if text is None else _text(text),
                    fields=[_text(field) for field in fields] or None,
                    accessory=None if accessory is None else self.element(accessory),
                )
            case scene.SlackHeader(text=text):
                return HeaderBlock(text=_plain(text, "header text"))
            case scene.SlackContext(elements=elements):
                return ContextBlock(elements=[_text(element) for element in elements])
            case scene.SlackDivider():
                return DividerBlock()
            case scene.SlackImage(image_url=image_url, alt_text=alt_text, title=title):
                safe = _safe_url(image_url, https_only=True)
                if safe is None:
                    message = "Slack images require an absolute HTTPS URL"
                    raise DrawInvariantError(message)
                return ImageBlock(
                    image_url=safe,
                    alt_text=alt_text,
                    title=None if title is None else _plain(title, "image title"),
                )
            case scene.SlackActions(elements=elements, block_id=block_id):
                return ActionsBlock(elements=[self.interactive(element) for element in elements], block_id=block_id)
            case scene.SlackInput(block_id=block_id, label=label, element=element, optional=optional, hint=hint):
                return InputBlock(
                    block_id=block_id,
                    label=_plain(label, "input label"),
                    element=self.input_element(element),
                    optional=optional,
                    hint=None if hint is None else _plain(hint, "input hint"),
                )
            case scene.SlackTable(rows=rows):
                return TableBlock(rows=[[RawTextObject(text=cell.content).to_dict() for cell in row] for row in rows])
            case scene.SlackCard():
                return self.card(value)
            case scene.SlackCarousel(cards=cards):
                return CarouselBlock(elements=[self.card(card) for card in cards])
            case scene.SlackAlert(title=title, text=text, style=style):
                content = title.content if text is None else f"{title.content}\n{text.content}"
                return AlertBlock(text=MarkdownTextObject(text=content), level=style.value)

    def card(self, value: scene.SlackCard) -> CardBlock:
        image_url = value.image_url
        if image_url is not None and (image_url := _safe_url(image_url, https_only=True)) is None:
            message = "Slack card images require an absolute HTTPS URL"
            raise DrawInvariantError(message)
        return CardBlock(
            hero_image=image_url,
            title=None if value.title is None else _text(value.title),
            body=None if value.description is None else _text(value.description),
            actions=[self.button(button) for button in value.actions] or None,
        )

    def interactive(self, value: scene.SlackButton | scene.SlackSelect) -> ButtonElement | InputInteractiveElement:
        if isinstance(value, scene.SlackButton):
            return self.button(value)
        return self.select(value)

    def element(self, value: scene.SlackElement) -> ButtonElement | InputInteractiveElement:
        if isinstance(value, scene.SlackButton):
            return self.button(value)
        return self.input_element(value)

    def input_element(self, value: scene.SlackInputElement) -> InputInteractiveElement:
        match value:
            case scene.SlackSelect():
                return self.select(value)
            case scene.SlackTextInput():
                return PlainTextInputElement(
                    action_id=value.action_id,
                    initial_value=value.initial_value,
                    placeholder=None if value.placeholder is None else _plain(value.placeholder, "input placeholder"),
                    multiline=value.multiline,
                    min_length=value.minimum_length,
                    max_length=value.maximum_length,
                )
            case scene.SlackNumberInput():
                return NumberInputElement(
                    action_id=value.action_id,
                    initial_value=value.initial_value,
                    is_decimal_allowed=value.decimal_allowed,
                    min_value=value.minimum,
                    max_value=value.maximum,
                )
            case scene.SlackDatePicker():
                return DatePickerElement(
                    action_id=value.action_id,
                    initial_date=value.initial_date,
                    placeholder=None if value.placeholder is None else _plain(value.placeholder, "date placeholder"),
                )
            case scene.SlackTimePicker():
                return TimePickerElement(
                    action_id=value.action_id,
                    initial_time=value.initial_time,
                    placeholder=None if value.placeholder is None else _plain(value.placeholder, "time placeholder"),
                )
            case scene.SlackCheckboxes():
                options = self.options(value.options)
                return CheckboxesElement(
                    action_id=value.action_id,
                    options=options,
                    initial_options=self.initial_options(options, value.options, value.initial_values),
                )
            case scene.SlackRadioButtons():
                options = self.options(value.options)
                initial = self.initial_options(
                    options,
                    value.options,
                    () if value.initial_value is None else (value.initial_value,),
                )
                return RadioButtonsElement(
                    action_id=value.action_id,
                    options=options,
                    initial_option=None if not initial else initial[0],
                )

    def button(self, value: scene.SlackButton) -> ButtonElement:
        action_id: str
        url: str | None = None
        if value.action is not None:
            action_id = value.action.action
        elif value.route is not None:
            action_id = value.route.route_id
        elif value.url is not None:
            action_id = _generated_action_id("url", value.url)
            url = _safe_url(value.url)
            if url is None:
                message = "Slack URL buttons require an absolute HTTP or HTTPS URL"
                raise DrawInvariantError(message)
        else:
            assert value.asset is not None
            action_id = _generated_action_id("asset", value.asset.key)
            url = self.resolve_asset(value.asset)
        style = None if value.style is scene.SlackButtonStyle.DEFAULT else value.style.value
        return ButtonElement(
            text=_plain(value.label, "button label"),
            action_id=action_id,
            url=url,
            value=value.value,
            style=style,
        )

    def select(self, value: scene.SlackSelect) -> InputInteractiveElement:
        action_id = (
            value.action.action
            if value.action is not None
            else value.route.route_id
            if value.route is not None
            else value.action_id
        )
        assert action_id is not None
        placeholder = None if value.placeholder is None else _plain(value.placeholder, "select placeholder")
        multiple = value.maximum > 1
        if value.kind is scene.SlackSelectKind.STATIC:
            options = self.options(value.options)
            initial = self.initial_options(options, value.options, value.initial_values)
            if multiple:
                return StaticMultiSelectElement(
                    action_id=action_id,
                    placeholder=placeholder,
                    options=options,
                    initial_options=initial,
                    max_selected_items=value.maximum,
                )
            return StaticSelectElement(
                action_id=action_id,
                placeholder=placeholder,
                options=options,
                initial_option=None if not initial else initial[0],
            )
        if value.kind is scene.SlackSelectKind.USERS:
            if multiple:
                return UserMultiSelectElement(
                    action_id=action_id,
                    placeholder=placeholder,
                    initial_users=list(value.initial_values),
                    max_selected_items=value.maximum,
                )
            return UserSelectElement(
                action_id=action_id,
                placeholder=placeholder,
                initial_user=None if not value.initial_values else value.initial_values[0],
            )
        included: list[str] = []
        for conversation_type in value.conversation_types:
            translated = _CONVERSATION_TYPES.get(conversation_type)
            if translated is None:
                message = f"Slack SDK cannot filter conversation type {conversation_type.value!r}"
                raise DrawInvariantError(message)
            if translated not in included:
                included.append(translated)
        filter_value = None if not included else ConversationFilter(include=included)
        if multiple:
            return ConversationMultiSelectElement(
                action_id=action_id,
                placeholder=placeholder,
                initial_conversations=list(value.initial_values),
                max_selected_items=value.maximum,
                filter=filter_value,
            )
        return ConversationSelectElement(
            action_id=action_id,
            placeholder=placeholder,
            initial_conversation=None if not value.initial_values else value.initial_values[0],
            filter=filter_value,
        )

    @staticmethod
    def options(values: Sequence[scene.SlackOption]) -> list[Option]:
        return [
            Option(
                value=value.value,
                text=_plain(value.label, "option label"),
                description=None if value.description is None else _plain(value.description, "option description"),
            )
            for value in values
        ]

    @staticmethod
    def initial_options(
        sdk_options: Sequence[Option],
        scene_options: Sequence[scene.SlackOption],
        selected: Sequence[str],
    ) -> list[Option]:
        by_value = {source.value: target for source, target in zip(scene_options, sdk_options, strict=True)}
        try:
            return [by_value[value] for value in selected]
        except KeyError as error:
            message = f"Slack initial option {error.args[0]!r} is not in the option set"
            raise DrawInvariantError(message) from error

    def resolve_asset(self, reference: scene.SlackAssetRef) -> str:
        metadata = self.assets.get(reference.key)
        if metadata is None or (metadata.name, metadata.media_type) != (reference.name, reference.media_type):
            message = f"Slack asset {reference.key!r} has no matching scene metadata"
            raise DrawInvariantError(message)
        resolved = self.asset_resolver(metadata) if self.asset_resolver is not None else None
        if resolved is None and self.plan is not None:
            resource = self.plan.resources.get(f"asset:{reference.key}")
            if isinstance(resource, Asset) and isinstance(resource.source, StoredAsset):
                resolved = resource.source.reference
        safe = None if resolved is None else _safe_url(resolved, https_only=True)
        if safe is None:
            message = f"Slack asset {reference.key!r} requires a public HTTPS URL from StoredAsset or asset_resolver"
            raise DrawInvariantError(message)
        return safe


def _validate_scene[BodyT: scene.SlackBody](
    document: scene.Scene[BodyT],
    *,
    target: str,
    body_type: type[BodyT],
    capability: AdapterCapability,
    profile: AdapterProfile[SlackSdkAdapter],
) -> BodyT:
    require_slack_sdk_capability(profile, capability, f"draw {target}")
    if document.protocol != scene.Codec.protocol:
        message = f"Slack renderer cannot draw scene protocol {document.protocol}"
        raise DrawInvariantError(message)
    if document.target != target:
        message = f"Slack renderer for {target!r} cannot draw target {document.target!r}"
        raise DrawInvariantError(message)
    if document.target_version != 1:
        message = f"Slack renderer cannot draw target version {document.target_version}"
        raise DrawInvariantError(message)
    if not isinstance(document.body, body_type):
        message = f"Slack renderer cannot draw a {type(document.body).__name__} body"
        raise DrawInvariantError(message)
    return document.body


def _validate_sdk(value: Block | View) -> None:
    try:
        value.to_dict()
    except (SlackObjectFormationError, TypeError, ValueError) as error:
        message = f"Slack SDK rejected planned Block Kit: {error}"
        raise DrawInvariantError(message) from error


def _audit_length(value: str, limit: int, role: str, *, allow_empty: bool = True) -> None:
    if (not allow_empty and not value) or len(value) > limit:
        range_description = f"1-{limit}" if not allow_empty else f"at most {limit}"
        message = f"Slack {role} must contain {range_description} characters"
        raise DrawInvariantError(message)


def _element_action_id(value: scene.SlackButton | scene.SlackSelect) -> str | None:
    if value.action is not None:
        return value.action.action
    if value.route is not None:
        return value.route.route_id
    return value.action_id if isinstance(value, scene.SlackSelect) else None


def _audit_option(value: scene.SlackOption, limits: SlackLimits) -> None:
    _audit_length(value.label.content, limits.components.option_label, "option label", allow_empty=False)
    _audit_length(value.value, limits.components.option_value, "option value", allow_empty=False)
    if value.description is not None:
        _audit_length(value.description.content, limits.components.option_description, "option description")


def _audit_element(value: scene.SlackElement, limits: SlackLimits) -> None:
    if isinstance(value, scene.SlackButton):
        _audit_length(value.label.content, limits.components.button_label, "button label", allow_empty=False)
        if value.value is not None:
            _audit_length(value.value, limits.components.button_value, "button value")
        if value.url is not None:
            _audit_length(value.url, limits.components.url, "button URL")
    elif isinstance(value, scene.SlackSelect):
        if value.placeholder is not None:
            _audit_length(value.placeholder.content, limits.components.placeholder, "select placeholder")
        if value.minimum < 0 or value.maximum < max(1, value.minimum):
            message = "Slack selector minimum and maximum are inconsistent"
            raise DrawInvariantError(message)
        if len(value.initial_values) > value.maximum:
            message = "Slack selector has more initial values than its maximum"
            raise DrawInvariantError(message)
        if len(value.options) > limits.components.select_options:
            message = f"Slack selector has more than {limits.components.select_options} options"
            raise DrawInvariantError(message)
        for option in value.options:
            _audit_option(option, limits)
    elif isinstance(value, scene.SlackTextInput | scene.SlackDatePicker | scene.SlackTimePicker):
        if value.placeholder is not None:
            _audit_length(value.placeholder.content, limits.components.placeholder, "input placeholder")
    elif isinstance(value, scene.SlackCheckboxes | scene.SlackRadioButtons):
        if not value.options or len(value.options) > limits.components.choice_options:
            message = f"Slack checkbox and radio groups require 1-{limits.components.choice_options} options"
            raise DrawInvariantError(message)
        for option in value.options:
            _audit_option(option, limits)
        selected = (
            value.initial_values
            if isinstance(value, scene.SlackCheckboxes)
            else (() if value.initial_value is None else (value.initial_value,))
        )
        available = {option.value for option in value.options}
        if any(item not in available for item in selected):
            message = "Slack checkbox or radio initial value is not in its option set"
            raise DrawInvariantError(message)
    action_id = (
        _element_action_id(value) if isinstance(value, scene.SlackButton | scene.SlackSelect) else value.action_id
    )
    if action_id is not None:
        _audit_length(action_id, limits.components.action_id, "action id", allow_empty=False)


def _audit_block(value: scene.SlackBlock, limits: SlackLimits) -> None:
    match value:
        case scene.SlackSection(text=text, fields=fields, accessory=accessory):
            if text is not None:
                _audit_length(text.content, limits.components.section_text, "section text")
            if len(fields) > limits.components.section_fields:
                message = f"Slack section has more than {limits.components.section_fields} fields"
                raise DrawInvariantError(message)
            for field in fields:
                _audit_length(field.content, limits.components.section_field_text, "section field")
            if accessory is not None:
                _audit_element(accessory, limits)
        case scene.SlackHeader(text=text):
            _audit_length(text.content, limits.components.header_text, "header text", allow_empty=False)
        case scene.SlackContext(elements=elements):
            if not elements or len(elements) > limits.components.context_elements:
                message = f"Slack context requires 1-{limits.components.context_elements} elements"
                raise DrawInvariantError(message)
        case scene.SlackActions(elements=elements, block_id=block_id):
            if not elements or len(elements) > limits.components.actions_elements:
                message = f"Slack actions require 1-{limits.components.actions_elements} elements"
                raise DrawInvariantError(message)
            if block_id is not None:
                _audit_length(block_id, limits.components.block_id, "block id", allow_empty=False)
            for element in elements:
                _audit_element(element, limits)
        case scene.SlackInput(block_id=block_id, element=element):
            _audit_length(block_id, limits.components.block_id, "block id", allow_empty=False)
            _audit_element(element, limits)
        case scene.SlackTable(rows=rows):
            if not rows or len(rows) > limits.components.table_rows:
                message = f"Slack table requires 1-{limits.components.table_rows} rows"
                raise DrawInvariantError(message)
            if any(not row or len(row) > limits.components.table_columns for row in rows):
                message = f"Slack table rows require 1-{limits.components.table_columns} cells"
                raise DrawInvariantError(message)
            if sum(len(cell.content) for row in rows for cell in row) > limits.components.table_text:
                message = f"Slack table text exceeds {limits.components.table_text} characters"
                raise DrawInvariantError(message)
        case scene.SlackCard(title=title, description=description, actions=actions):
            if title is not None:
                _audit_length(title.content, limits.components.card_title, "card title")
            if description is not None:
                _audit_length(description.content, limits.components.card_body, "card body")
            for action in actions:
                _audit_element(action, limits)
        case scene.SlackCarousel(cards=cards):
            if not cards or len(cards) > limits.components.carousel_cards:
                message = f"Slack carousel requires 1-{limits.components.carousel_cards} cards"
                raise DrawInvariantError(message)
            for card in cards:
                _audit_block(card, limits)
        case scene.SlackAlert(title=title, text=text):
            content = title.content if text is None else f"{title.content}\n{text.content}"
            _audit_length(content, limits.components.alert_text, "alert text", allow_empty=False)
        case scene.SlackDivider() | scene.SlackImage():
            pass


def _audit_surface(blocks: Sequence[scene.SlackBlock], *, surface: str, limits: SlackLimits) -> None:
    if len(blocks) > limits.blocks:
        message = f"Slack {surface} has {len(blocks)} blocks; limit is {limits.blocks}"
        raise DrawInvariantError(message)
    for block in blocks:
        _audit_block(block, limits)
    if surface != "modal" and any(isinstance(block, scene.SlackInput | scene.SlackAlert) for block in blocks):
        message = f"Slack {surface} cannot contain input or alert blocks"
        raise DrawInvariantError(message)
    if surface == "modal" and any(
        isinstance(block, scene.SlackTable | scene.SlackCard | scene.SlackCarousel) for block in blocks
    ):
        message = "Slack modal cannot contain table, card, or carousel blocks"
        raise DrawInvariantError(message)


class MessageRenderer(Renderer[scene.SlackMessage, MessagePayload]):
    """Draw planned Slack message scenes into SDK blocks."""

    def __init__(
        self,
        *,
        adapter: AdapterProfile[SlackSdkAdapter] = SLACK_SDK_343_ADAPTER,
        asset_resolver: AssetResolver | None = None,
    ) -> None:
        self.adapter = adapter
        self.asset_resolver = asset_resolver

    def draw(
        self,
        document: scene.Scene[scene.SlackMessage],
        *,
        plan: PlanResult[scene.SlackMessage] | None = None,
    ) -> MessagePayload:
        """Draw one planned Slack message."""
        body = _validate_scene(
            document,
            target="slack.block-kit.message",
            body_type=scene.SlackMessage,
            capability=AdapterCapability.RENDER_SLACK_MESSAGE,
            profile=self.adapter,
        )
        _audit_surface(body.blocks, surface="message", limits=SLACK_MESSAGE_LIMITS)
        if len(body.text) > SLACK_MESSAGE_LIMITS.fallback_text:
            message = "Slack message fallback text exceeds 40000 characters"
            raise DrawInvariantError(message)
        if not body.text and not body.blocks:
            message = "Slack message requires fallback text or blocks"
            raise DrawInvariantError(message)
        drawer = _Drawer(assets=document.assets, plan=plan, asset_resolver=self.asset_resolver)
        blocks = drawer.blocks(body.blocks)
        for block in blocks:
            _validate_sdk(block)
        return MessagePayload(body.text, blocks)


class ModalRenderer(Renderer[scene.SlackModalView, View]):
    """Draw planned Slack modal scenes into SDK views."""

    def __init__(self, *, adapter: AdapterProfile[SlackSdkAdapter] = SLACK_SDK_343_ADAPTER) -> None:
        self.adapter = adapter

    def draw(
        self,
        document: scene.Scene[scene.SlackModalView],
        *,
        plan: PlanResult[scene.SlackModalView] | None = None,
    ) -> View:
        """Draw one planned Slack modal view."""
        body = _validate_scene(
            document,
            target="slack.block-kit.modal",
            body_type=scene.SlackModalView,
            capability=AdapterCapability.RENDER_SLACK_MODAL,
            profile=self.adapter,
        )
        _audit_surface(body.blocks, surface="modal", limits=SLACK_MODAL_LIMITS)
        drawer = _Drawer(assets=document.assets, plan=plan, asset_resolver=None)
        result = View(
            type="modal",
            callback_id=body.callback_id,
            title=_plain(body.title, "modal title"),
            submit=_plain(body.submit, "modal submit"),
            close=_plain(body.close, "modal close"),
            blocks=drawer.blocks(body.blocks),
            private_metadata=body.private_metadata,
        )
        _validate_sdk(result)
        return result


class HomeRenderer(Renderer[scene.SlackHomeView, View]):
    """Draw planned Slack App Home scenes into SDK views."""

    def __init__(self, *, adapter: AdapterProfile[SlackSdkAdapter] = SLACK_SDK_343_ADAPTER) -> None:
        self.adapter = adapter

    def draw(
        self,
        document: scene.Scene[scene.SlackHomeView],
        *,
        plan: PlanResult[scene.SlackHomeView] | None = None,
    ) -> View:
        """Draw one planned Slack App Home view."""
        body = _validate_scene(
            document,
            target="slack.block-kit.home",
            body_type=scene.SlackHomeView,
            capability=AdapterCapability.RENDER_SLACK_HOME,
            profile=self.adapter,
        )
        _audit_surface(body.blocks, surface="home", limits=SLACK_HOME_LIMITS)
        drawer = _Drawer(assets=document.assets, plan=plan, asset_resolver=None)
        result = View(
            type="home",
            callback_id=body.callback_id,
            blocks=drawer.blocks(body.blocks),
            private_metadata=body.private_metadata,
        )
        _validate_sdk(result)
        return result


__all__ = ["AssetResolver", "HomeRenderer", "MessageRenderer", "ModalRenderer"]
