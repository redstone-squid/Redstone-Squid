"""Static conformance of concrete renderers to their scene and payload protocols.

Assignments exercise generic compatibility that runtime rendering tests do not check.
"""

from squid_ui import scene
from squid_ui.html import DiscordPreviewRenderer
from squid_ui.html import Renderer as HtmlRenderer
from squid_ui.renderer import Renderer
from squid_ui_discord.classic_renderer import ClassicRenderer
from squid_ui_discord.message_payload import MessagePayload
from squid_ui_discord.renderer import MountedRenderer, V2Renderer


def accepts_v2_renderer(value: Renderer[scene.ComponentsV2, MessagePayload]) -> None:
    del value


def accepts_classic_renderer(value: Renderer[scene.ClassicMessage, MessagePayload]) -> None:
    del value


def accepts_html_renderer(value: Renderer[scene.HtmlBody, str]) -> None:
    del value


def accepts_discord_preview_renderer(value: Renderer[scene.ComponentsV2, str]) -> None:
    del value


accepts_v2_renderer(V2Renderer())
accepts_classic_renderer(ClassicRenderer())
accepts_html_renderer(HtmlRenderer())
accepts_discord_preview_renderer(DiscordPreviewRenderer())


def accepts_mounted_v2(value: MountedRenderer[scene.ComponentsV2]) -> None:
    del value


def accepts_mounted_classic(value: MountedRenderer[scene.ClassicMessage]) -> None:
    del value


# Both mounted renderer specializations must fit the union used for dialect selection.
def accepts_either_mounted(value: MountedRenderer[scene.ComponentsV2] | MountedRenderer[scene.ClassicMessage]) -> None:
    del value


accepts_mounted_v2(V2Renderer())
accepts_mounted_classic(ClassicRenderer())
accepts_either_mounted(V2Renderer())
accepts_either_mounted(ClassicRenderer())

# The HTML renderer uses different scene and payload types and provides no Discord control wiring.
accepts_mounted_v2(HtmlRenderer())  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]
