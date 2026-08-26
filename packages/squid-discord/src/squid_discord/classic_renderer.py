"""Mechanical drawing of resolved classic Discord messages, and the audit that proves them.

Two facts shape everything here, and neither is obvious.

`discord.ui.View` rejects `ActionRow` items outright, and an ActionRow-only `LayoutView` is
not a substitute: `ActionRow._is_v2()` returns True by deliberate upstream design, so such a
view sets the Components V2 flag on the message even though its payload is byte-identical to a
classic one. That flag is irreversible. So rows become explicit `row=` indices on bare
`Button` and `Select` items in a real `View`.

And almost nothing about an embed is checked before it reaches Discord. discord.py enforces
`len(embeds) > 10` and the 25-child cap on a view; the 6,000-character aggregate and every
per-value cap are server-only. A payload that would come back as a 400 naming nothing useful
therefore has to be caught here, by walking what will actually be sent.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

import discord

from squid_discord.adapter import DISCORD_PY_27_ADAPTER, require_discord_py_capability
from squid_discord.attachments import attachment_assets
from squid_discord.emoji import discord_emoji
from squid_discord.inspection import audit_classic_payload
from squid_discord.presentation import DiscordPresentation
from squid_discord.renderer import RoutedItem, RoutedSelectItem
from squid_discord.target import CLASSIC_TARGET
from squid_layouts.errors import DrawInvariantError
from squid_layouts.interactions import ActionBinding
from squid_layouts.planning.adapter import ADAPTER_RENDER_CLASSIC, AdapterProfile
from squid_layouts.planning.limits import CLASSIC_LIMITS, ClassicLimits
from squid_layouts.scene.codec import SceneCodec
from squid_layouts.scene.model import (
    PlanResult,
    SceneButton,
    SceneClassicMessage,
    SceneClassicRow,
    SceneControl,
    SceneDocument,
    SceneEmbed,
    SceneEntitySelect,
    SceneExtension,
    SceneLink,
    ScenePremiumButton,
    SceneRoutedButton,
    SceneRoutedSelect,
    SceneSelect,
)
from squid_layouts.target_types import DiscordPyAdapter

type Control = SceneButton | SceneSelect | SceneEntitySelect
type Wire = Callable[[Control, ActionBinding], discord.ui.Item[Any]]
type ClassicViewFactory = Callable[[], discord.ui.View]


class StaticClassicView(discord.ui.View):
    """A rendered classic view with no interaction session or timeout."""

    def __init__(self) -> None:
        super().__init__(timeout=None)


class ClassicRenderer:
    """Draw a resolved classic scene. It makes no layout decisions and repairs nothing."""

    def __init__(
        self,
        *,
        limits: ClassicLimits = CLASSIC_LIMITS,
        audit: bool = True,
        view_factory: ClassicViewFactory = StaticClassicView,
        always_view: bool = False,
        adapter: AdapterProfile[DiscordPyAdapter] = DISCORD_PY_27_ADAPTER,
    ) -> None:
        require_discord_py_capability(adapter, ADAPTER_RENDER_CLASSIC, "render a classic message")
        self.limits = limits
        self.audit = audit
        self.view_factory = view_factory
        self.always_view = always_view
        """Build a view even for a document with no controls.

        A static render of pure prose needs no view and should send none. A *mounted* one
        does: the view is what owns the mount's timeout and what discord.py stores, so a
        screen that currently shows no buttons would otherwise never time out.
        """

    def draw(
        self,
        scene: SceneDocument[SceneClassicMessage],
        *,
        plan: PlanResult[SceneClassicMessage] | None = None,
        wire: Wire | None = None,
    ) -> DiscordPresentation:
        body = self._body(scene)
        embeds = tuple(self._embed(embed, index) for index, embed in enumerate(body.embeds))
        view = self._view(body, plan=plan, wire=wire)
        assets = () if plan is None else attachment_assets(plan)
        presentation = DiscordPresentation.classic(
            content=body.content,
            embeds=embeds,
            view=view,
            assets=assets,
        )
        if self.audit:
            problems = audit_classic_payload(
                content=body.content, embeds=embeds, view=view, attachments=len(assets), limits=self.limits
            )
            if problems:
                message = "classic drawing violated its planned limits: " + "; ".join(problems)
                raise DrawInvariantError(message)
        return presentation

    def _body(self, scene: SceneDocument[SceneClassicMessage]) -> SceneClassicMessage:
        if scene.protocol != SceneCodec.protocol:
            message = f"ClassicRenderer cannot draw scene protocol {scene.protocol}"
            raise DrawInvariantError(message)
        if scene.target != CLASSIC_TARGET.id:
            message = f"ClassicRenderer cannot draw target {scene.target!r}"
            raise DrawInvariantError(message)
        if scene.target_version != 1:
            message = f"ClassicRenderer cannot draw Discord target version {scene.target_version}"
            raise DrawInvariantError(message)
        if not isinstance(scene.body, SceneClassicMessage):
            message = f"ClassicRenderer cannot draw a {type(scene.body).__name__} body"
            raise DrawInvariantError(message)
        return scene.body

    def _embed(self, node: SceneEmbed, index: int) -> discord.Embed:
        embed = discord.Embed(
            title=node.title,
            url=node.url,
            description=node.description,
            colour=None if node.colour is None else discord.Colour(node.colour),
            timestamp=None if node.timestamp is None else datetime.fromisoformat(node.timestamp),
        )
        for field in node.fields:
            embed.add_field(name=field.name, value=field.value, inline=field.inline)
        if node.footer is not None:
            embed.set_footer(text=node.footer.text, icon_url=node.footer.icon_url)
        if node.author is not None:
            embed.set_author(name=node.author.name, url=node.author.url, icon_url=node.author.icon_url)
        if node.image is not None:
            embed.set_image(url=node.image.url)
        if node.thumbnail is not None:
            embed.set_thumbnail(url=node.thumbnail.url)
        if len(embed.fields) != len(node.fields):
            message = f"embed {index} lost fields between the scene and discord.py"
            raise DrawInvariantError(message)
        return embed

    def _view(
        self,
        body: SceneClassicMessage,
        *,
        plan: PlanResult | None,
        wire: Wire | None,
    ) -> discord.ui.View | None:
        if not body.rows and not self.always_view:
            return None
        view = self.view_factory()
        for index, row in enumerate(body.rows):
            for item in self._row(row, index, plan=plan, wire=wire):
                item.row = index
                try:
                    view.add_item(item)
                except ValueError as error:
                    # Planning already satisfied every cap this can raise for. Reaching it
                    # means the plan and the drawing disagree, which is a bug rather than a
                    # document to degrade.
                    message = f"row {index} could not be placed after planning: {error}"
                    raise DrawInvariantError(message) from error
        return view

    def _row(
        self,
        row: SceneClassicRow,
        index: int,
        *,
        plan: PlanResult | None,
        wire: Wire | None,
    ) -> list[discord.ui.Item[Any]]:
        selects = sum(
            isinstance(control, SceneSelect | SceneRoutedSelect | SceneEntitySelect) for control in row.controls
        )
        if selects and len(row.controls) > 1:
            message = f"row {index} mixes a select with other controls; a select occupies its whole row"
            raise DrawInvariantError(message)
        if not selects and len(row.controls) > self.limits.components.row_buttons:
            message = (
                f"row {index} holds {len(row.controls)} buttons; the maximum is {self.limits.components.row_buttons}"
            )
            raise DrawInvariantError(message)
        if not row.controls:
            message = f"row {index} is empty; planning should not have produced it"
            raise DrawInvariantError(message)
        return [self._control(control, index, plan=plan, wire=wire) for control in row.controls]

    def _control(
        self,
        control: SceneControl,
        row: int,
        *,
        plan: PlanResult | None,
        wire: Wire | None,
    ) -> discord.ui.Item[Any]:
        match control:
            case SceneLink(label=label, url=url):
                return discord.ui.Button(
                    style=discord.ButtonStyle.link,
                    label=label,
                    url=url,
                    emoji=discord_emoji(control.emoji),
                    disabled=control.disabled,
                )
            case ScenePremiumButton(sku_id=sku_id):
                return discord.ui.Button(sku_id=sku_id)
            case SceneRoutedButton(label=label, route_id=route_id):
                return RoutedItem(
                    style=getattr(discord.ButtonStyle, control.style.value),
                    label=label,
                    custom_id=route_id,
                    emoji=discord_emoji(control.emoji),
                    disabled=control.disabled,
                )
            case SceneRoutedSelect(options=options, route_id=route_id):
                return RoutedSelectItem(
                    options=[_option(option) for option in options],
                    custom_id=route_id,
                    placeholder=control.placeholder,
                    min_values=control.min_values,
                    max_values=control.max_values,
                    disabled=control.disabled,
                )
            case SceneButton() | SceneSelect() | SceneEntitySelect():
                if plan is None or wire is None:
                    message = "interactive scene controls require a mounted Discord frontend"
                    raise TypeError(message)
                binding = plan.bindings.get(control.action)
                if binding is None:
                    message = f"scene action {control.action!r} has no binding"
                    raise DrawInvariantError(message)
                return wire(control, binding)
            case SceneExtension(kind=kind):
                message = f"a classic message cannot draw the Discord extension {kind!r} in row {row}"
                raise DrawInvariantError(message)


def _option(option: object) -> discord.SelectOption:
    return discord.SelectOption(
        label=option.label,  # type: ignore[attr-defined]
        value=option.value,  # type: ignore[attr-defined]
        description=option.description,  # type: ignore[attr-defined]
        default=option.default,  # type: ignore[attr-defined]
        emoji=discord_emoji(option.emoji),  # type: ignore[attr-defined]
    )
