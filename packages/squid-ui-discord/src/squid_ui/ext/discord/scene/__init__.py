"""Discord scene vocabulary and wire-codec registration."""

from squid_ui.ext.discord.scene.codec import DIALECT
from squid_ui.ext.discord.scene.model import Button as Button
from squid_ui.ext.discord.scene.model import ClassicMessage as ClassicMessage
from squid_ui.ext.discord.scene.model import ClassicRow as ClassicRow
from squid_ui.ext.discord.scene.model import ComponentsV2 as ComponentsV2
from squid_ui.ext.discord.scene.model import Control as Control
from squid_ui.ext.discord.scene.model import Embed as Embed
from squid_ui.ext.discord.scene.model import EmbedAuthor as EmbedAuthor
from squid_ui.ext.discord.scene.model import EmbedField as EmbedField
from squid_ui.ext.discord.scene.model import EmbedFooter as EmbedFooter
from squid_ui.ext.discord.scene.model import EmbedMedia as EmbedMedia
from squid_ui.ext.discord.scene.model import EntitySelect as EntitySelect
from squid_ui.ext.discord.scene.model import Extension as Extension
from squid_ui.ext.discord.scene.model import File as File
from squid_ui.ext.discord.scene.model import Gallery as Gallery
from squid_ui.ext.discord.scene.model import GalleryItem as GalleryItem
from squid_ui.ext.discord.scene.model import Link as Link
from squid_ui.ext.discord.scene.model import Node as Node
from squid_ui.ext.discord.scene.model import Option as Option
from squid_ui.ext.discord.scene.model import Panel as Panel
from squid_ui.ext.discord.scene.model import PremiumButton as PremiumButton
from squid_ui.ext.discord.scene.model import RoutedButton as RoutedButton
from squid_ui.ext.discord.scene.model import RoutedSelect as RoutedSelect
from squid_ui.ext.discord.scene.model import Row as Row
from squid_ui.ext.discord.scene.model import Section as Section
from squid_ui.ext.discord.scene.model import Select as Select
from squid_ui.ext.discord.scene.model import Separator as Separator
from squid_ui.ext.discord.scene.model import Text as Text
from squid_ui.ext.discord.scene.model import Thumbnail as Thumbnail
from squid_ui.ext.discord.scene.model import Time as Time
from squid_ui.ext.discord.scene.model import ZonedTime as ZonedTime
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
    "Button",
    "ClassicMessage",
    "ClassicRow",
    "Codec",
    "CodecError",
    "ComponentsV2",
    "Control",
    "Embed",
    "EmbedAuthor",
    "EmbedField",
    "EmbedFooter",
    "EmbedMedia",
    "EntitySelect",
    "Extension",
    "File",
    "Gallery",
    "GalleryItem",
    "JsonValue",
    "Link",
    "Node",
    "Option",
    "Pager",
    "Panel",
    "PremiumButton",
    "RoutedButton",
    "RoutedSelect",
    "Row",
    "Scene",
    "Section",
    "Select",
    "Separator",
    "Text",
    "Thumbnail",
    "Time",
    "ZonedTime",
]
