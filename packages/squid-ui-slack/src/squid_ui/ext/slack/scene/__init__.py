"""Slack scene vocabulary and wire-codec registration."""

from squid_ui.ext.slack.scene.codec import DIALECT
from squid_ui.ext.slack.scene.model import SlackActionRef as SlackActionRef
from squid_ui.ext.slack.scene.model import SlackActions as SlackActions
from squid_ui.ext.slack.scene.model import SlackAlert as SlackAlert
from squid_ui.ext.slack.scene.model import SlackAlertStyle as SlackAlertStyle
from squid_ui.ext.slack.scene.model import SlackAssetRef as SlackAssetRef
from squid_ui.ext.slack.scene.model import SlackBlock as SlackBlock
from squid_ui.ext.slack.scene.model import SlackBody as SlackBody
from squid_ui.ext.slack.scene.model import SlackButton as SlackButton
from squid_ui.ext.slack.scene.model import SlackButtonStyle as SlackButtonStyle
from squid_ui.ext.slack.scene.model import SlackCard as SlackCard
from squid_ui.ext.slack.scene.model import SlackCarousel as SlackCarousel
from squid_ui.ext.slack.scene.model import SlackCheckboxes as SlackCheckboxes
from squid_ui.ext.slack.scene.model import SlackContext as SlackContext
from squid_ui.ext.slack.scene.model import SlackDatePicker as SlackDatePicker
from squid_ui.ext.slack.scene.model import SlackDivider as SlackDivider
from squid_ui.ext.slack.scene.model import SlackElement as SlackElement
from squid_ui.ext.slack.scene.model import SlackHeader as SlackHeader
from squid_ui.ext.slack.scene.model import SlackHomeView as SlackHomeView
from squid_ui.ext.slack.scene.model import SlackImage as SlackImage
from squid_ui.ext.slack.scene.model import SlackInput as SlackInput
from squid_ui.ext.slack.scene.model import SlackInputElement as SlackInputElement
from squid_ui.ext.slack.scene.model import SlackMessage as SlackMessage
from squid_ui.ext.slack.scene.model import SlackModalView as SlackModalView
from squid_ui.ext.slack.scene.model import SlackNumberInput as SlackNumberInput
from squid_ui.ext.slack.scene.model import SlackOption as SlackOption
from squid_ui.ext.slack.scene.model import SlackRadioButtons as SlackRadioButtons
from squid_ui.ext.slack.scene.model import SlackRouteRef as SlackRouteRef
from squid_ui.ext.slack.scene.model import SlackSection as SlackSection
from squid_ui.ext.slack.scene.model import SlackSelect as SlackSelect
from squid_ui.ext.slack.scene.model import SlackSelectKind as SlackSelectKind
from squid_ui.ext.slack.scene.model import SlackTable as SlackTable
from squid_ui.ext.slack.scene.model import SlackText as SlackText
from squid_ui.ext.slack.scene.model import SlackTextInput as SlackTextInput
from squid_ui.ext.slack.scene.model import SlackTextKind as SlackTextKind
from squid_ui.ext.slack.scene.model import SlackTimePicker as SlackTimePicker
from squid_ui.scene import Asset as Asset
from squid_ui.scene import Body as Body
from squid_ui.scene import Codec as Codec
from squid_ui.scene import CodecError as CodecError
from squid_ui.scene import JsonValue as JsonValue
from squid_ui.scene import Pager as Pager
from squid_ui.scene import Scene as Scene

Codec.register(DIALECT)

__all__ = [
    "Asset",
    "Body",
    "Codec",
    "CodecError",
    "JsonValue",
    "Pager",
    "Scene",
    "SlackActionRef",
    "SlackActions",
    "SlackAlert",
    "SlackAlertStyle",
    "SlackAssetRef",
    "SlackBlock",
    "SlackBody",
    "SlackButton",
    "SlackButtonStyle",
    "SlackCard",
    "SlackCarousel",
    "SlackCheckboxes",
    "SlackContext",
    "SlackDatePicker",
    "SlackDivider",
    "SlackElement",
    "SlackHeader",
    "SlackHomeView",
    "SlackImage",
    "SlackInput",
    "SlackInputElement",
    "SlackMessage",
    "SlackModalView",
    "SlackNumberInput",
    "SlackOption",
    "SlackRadioButtons",
    "SlackRouteRef",
    "SlackSection",
    "SlackSelect",
    "SlackSelectKind",
    "SlackTable",
    "SlackText",
    "SlackTextInput",
    "SlackTextKind",
    "SlackTimePicker",
]
