"""Dependency-leaf static vocabulary for render targets and adapter families.

Two lattices of empty classes read only by the type checker. A node is `Renderable[T]` for the
widest marker it draws on and a target's `render_target` is the narrowest, so a
`Renderable[DiscordTarget]` primitive fits both Discord dialects while a
`Renderable[ComponentsV2Target]` one is rejected by the classic target. Adapter markers work the
same way through `AdapterProfile[AdapterT]`: a target built for `DiscordPy27Adapter` satisfies a
parameter that asks for `DiscordPyAdapter`.
"""


class RenderTarget:
    """Root of the target lattice; a `Renderable[RenderTarget]` draws on every dialect."""


class DiscordTarget(RenderTarget):
    """Either Discord message dialect; the primitives both share (`Text`, `Button`, `LinkButton`) accept this."""


class ComponentsV2Target(DiscordTarget):
    """The dialect `planning.v2` compiles to (`scene.ComponentsV2`); `File` and `Sep` accept only this."""


class ClassicTarget(DiscordTarget):
    """The embed-and-rows dialect `planning.classic` compiles to (`scene.ClassicMessage`)."""


class HtmlTarget(RenderTarget):
    """The dialect `html.target` compiles to (`scene.HtmlBody`)."""


class SlackTarget(RenderTarget):
    """Any of the three Block Kit surfaces."""


class SlackMessageTarget(SlackTarget):
    """Block Kit posted as a channel message (`scene.SlackMessage`)."""


class SlackModalTarget(SlackTarget):
    """Block Kit inside a modal view (`scene.SlackModalView`)."""


class SlackHomeTarget(SlackTarget):
    """Block Kit on an App Home tab (`scene.SlackHomeView`)."""


class DiscordAdapter:
    """Root of the Discord adapter lattice, for a target that takes any Discord library."""


class DiscordPyAdapter(DiscordAdapter):
    """Any discord.py version; `MessageRoot` and the Discord targets are bounded by this."""


class DiscordPy27Adapter(DiscordPyAdapter):
    """The profile `squid_ui_discord` ships and verifies against: discord.py 2.7."""


class HtmlAdapter:
    """Adapter family for the HTML renderer; unrefined, since it depends on no library."""


class SlackAdapter:
    """Root of the Slack adapter lattice, for a target that takes any Slack library."""


class SlackSdkAdapter(SlackAdapter):
    """Any Slack Python SDK version; the Slack renderers are bounded by this."""


class SlackSdk343Adapter(SlackSdkAdapter):
    """The profile `squid_ui_slack` ships and verifies against: Slack Python SDK 3.43."""


class Renderable[RenderTargetT = RenderTarget]:
    """A value whose accepted protocol target is tracked by the type checker.

    `_accepts_target` makes `RenderTargetT` contravariant: a node typed for a wide marker is
    accepted by every target under it.
    """

    __slots__ = ()
    """Empty, so a `slots=True` node subclass really is slotted.

    A base without `__slots__` grants every subclass a `__dict__`, which defeats the
    `@dataclass(frozen=True, slots=True)` on every node in the package.
    """

    def _accepts_target(self, target: RenderTargetT, /) -> None:
        del target
