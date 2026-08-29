"""Pins that the renderer protocols are satisfied by the renderers. Nothing here runs.

A protocol nothing is ever checked against is how the original contravariance bug survived:
`Renderer.draw` took an unparameterized `scene.Scene`, so no renderer that narrowed to its
own body type could implement it, and the protocol sat declared and structurally dead for as
long as no file said `Renderer` in a parameter position. These assignments are that file.
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


# What `_BINDINGS` stores: a mount picks its renderer by dialect id at runtime, so both
# concrete renderers must be reachable through one erased type.
def accepts_either_mounted(value: MountedRenderer[scene.ComponentsV2] | MountedRenderer[scene.ClassicMessage]) -> None:
    del value


accepts_mounted_v2(V2Renderer())
accepts_mounted_classic(ClassicRenderer())
accepts_either_mounted(V2Renderer())
accepts_either_mounted(ClassicRenderer())

# The HTML renderer draws scenes but has no mount to wire controls to, so it is deliberately
# not a `MountedRenderer`. If this stops being an error, `wire` has gone optional in a way
# that lets an unwireable renderer into a live message root.
accepts_mounted_v2(HtmlRenderer())  # pyrefly: ignore[bad-argument-type]
