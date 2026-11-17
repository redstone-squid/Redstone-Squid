"""Html scene vocabulary and wire-codec registration."""

from squid_ui.ext.html.scene.codec import DIALECT
from squid_ui.ext.html.scene.model import HtmlActionRef as HtmlActionRef
from squid_ui.ext.html.scene.model import HtmlAssetRef as HtmlAssetRef
from squid_ui.ext.html.scene.model import HtmlAttribute as HtmlAttribute
from squid_ui.ext.html.scene.model import HtmlAttributeName as HtmlAttributeName
from squid_ui.ext.html.scene.model import HtmlAttributeValue as HtmlAttributeValue
from squid_ui.ext.html.scene.model import HtmlBody as HtmlBody
from squid_ui.ext.html.scene.model import HtmlColourRef as HtmlColourRef
from squid_ui.ext.html.scene.model import HtmlElement as HtmlElement
from squid_ui.ext.html.scene.model import HtmlFormRef as HtmlFormRef
from squid_ui.ext.html.scene.model import HtmlNode as HtmlNode
from squid_ui.ext.html.scene.model import HtmlRouteRef as HtmlRouteRef
from squid_ui.ext.html.scene.model import HtmlTag as HtmlTag
from squid_ui.ext.html.scene.model import HtmlText as HtmlText
from squid_ui.ext.html.scene.model import HtmlTimeRef as HtmlTimeRef
from squid_ui.ext.html.scene.model import HtmlUrlRef as HtmlUrlRef
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
    "HtmlActionRef",
    "HtmlAssetRef",
    "HtmlAttribute",
    "HtmlAttributeName",
    "HtmlAttributeValue",
    "HtmlBody",
    "HtmlColourRef",
    "HtmlElement",
    "HtmlFormRef",
    "HtmlNode",
    "HtmlRouteRef",
    "HtmlTag",
    "HtmlText",
    "HtmlTimeRef",
    "HtmlUrlRef",
    "JsonValue",
    "Pager",
    "Scene",
]
