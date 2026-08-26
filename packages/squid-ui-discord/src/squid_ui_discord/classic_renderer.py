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

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, cast

import discord

from squid_ui import scene
from squid_ui.errors import DrawInvariantError
from squid_ui.planning.adapter import AdapterCapability, AdapterProfile
from squid_ui.planning.limits import CLASSIC_LIMITS, ClassicLimits
from squid_ui.scene.model import PlanResult
from squid_ui.target_types import DiscordPyAdapter
from squid_ui_discord.adapter import DISCORD_PY_27_ADAPTER, require_discord_py_capability
from squid_ui_discord.attachments import attachment_assets
from squid_ui_discord.emoji import discord_emoji
from squid_ui_discord.inspection import audit_classic_payload
from squid_ui_discord.presentation import DiscordPresentation
from squid_ui_discord.render_cache import RenderProgramCache
from squid_ui_discord.renderer import RoutedItem, RoutedSelectItem
from squid_ui_discord.renderer import Wire as Wire
from squid_ui_discord.target import DISCORD_V1_DPY27

type Control = scene.Button | scene.Select | scene.EntitySelect
type ClassicViewFactory = Callable[[], discord.ui.View]


class _ClassicControlOp(StrEnum):
    LINK = "link"
    PREMIUM = "premium"
    ROUTED_BUTTON = "routed_button"
    ROUTED_SELECT = "routed_select"
    CONTROL = "control"
    EXTENSION = "extension"


@dataclass(frozen=True, slots=True)
class _ClassicControlInstruction:
    op: _ClassicControlOp
    source: scene.Control


@dataclass(frozen=True, slots=True)
class _ClassicEmbedInstruction:
    source: scene.Embed
    timestamp: datetime | None


@dataclass(frozen=True, slots=True)
class _ClassicProgram:
    content: str | None
    embeds: tuple[_ClassicEmbedInstruction, ...]
    rows: tuple[tuple[_ClassicControlInstruction, ...], ...]
    opaque: bool = False


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
        cache: RenderProgramCache | None = None,
    ) -> None:
        require_discord_py_capability(adapter, AdapterCapability.RENDER_CLASSIC, "render a classic message")
        self.limits = limits
        self.audit = audit
        self.view_factory = view_factory
        self.always_view = always_view
        self.cache = cache if cache is not None else RenderProgramCache()
        """Build a view even for a document with no controls.

        A static render of pure prose needs no view and should send none. A *mounted* one
        does: the view is what owns the mount's timeout and what discord.py stores, so a
        screen that currently shows no buttons would otherwise never time out.
        """

    def draw(
        self,
        document: scene.Scene[scene.ClassicMessage],
        *,
        plan: PlanResult[scene.ClassicMessage] | None = None,
        wire: Wire | None = None,
    ) -> DiscordPresentation:
        body = self._body(document)
        key = self._cache_key(document, plan)
        cached = self.cache.get(key)
        if cached is None:
            program = self._compile(body)
            certified = False
        else:
            stored, certified = cached
            if not isinstance(stored, _ClassicProgram):
                message = "render program cache returned the wrong program type"
                raise DrawInvariantError(message)
            program = stored
        embeds = tuple(self._embed(embed, index) for index, embed in enumerate(program.embeds))
        view = self._view(program, plan=plan, wire=wire)
        assets = () if plan is None else attachment_assets(plan)
        presentation = DiscordPresentation.classic(
            content=program.content,
            embeds=embeds,
            view=view,
            assets=assets,
        )
        certifiable = self.view_factory is StaticClassicView and wire is None and not program.opaque
        if self.audit and (not certified or not certifiable):
            problems = audit_classic_payload(
                content=program.content, embeds=embeds, view=view, attachments=len(assets), limits=self.limits
            )
            if problems:
                message = "classic drawing violated its planned limits: " + "; ".join(problems)
                raise DrawInvariantError(message)
        self.cache.put(key, program, certified=certified or (self.audit and certifiable))
        return presentation

    def _cache_key(
        self,
        document: scene.Scene[scene.ClassicMessage],
        plan: PlanResult[scene.ClassicMessage] | None,
    ) -> Hashable:
        fingerprint = plan.report.scene_fingerprint if plan is not None else scene.Codec.fingerprint(document)
        factory = "static" if self.view_factory is StaticClassicView else "custom"
        return "discord.classic", fingerprint, self.limits, factory, self.always_view

    def _body(self, document: scene.Scene[scene.ClassicMessage]) -> scene.ClassicMessage:
        if document.protocol != scene.Codec.protocol:
            message = f"ClassicRenderer cannot draw scene protocol {document.protocol}"
            raise DrawInvariantError(message)
        if document.target != DISCORD_V1_DPY27.id:
            message = f"ClassicRenderer cannot draw target {document.target!r}"
            raise DrawInvariantError(message)
        if document.target_version != 1:
            message = f"ClassicRenderer cannot draw Discord target version {document.target_version}"
            raise DrawInvariantError(message)
        if not isinstance(document.body, scene.ClassicMessage):
            message = f"ClassicRenderer cannot draw a {type(document.body).__name__} body"
            raise DrawInvariantError(message)
        return document.body

    def _compile(self, body: scene.ClassicMessage) -> _ClassicProgram:
        embeds = tuple(
            _ClassicEmbedInstruction(
                embed,
                None if embed.timestamp is None else datetime.fromisoformat(embed.timestamp),
            )
            for embed in body.embeds
        )
        rows: list[tuple[_ClassicControlInstruction, ...]] = []
        opaque = False
        for index, row in enumerate(body.rows):
            selects = sum(
                isinstance(control, scene.Select | scene.RoutedSelect | scene.EntitySelect) for control in row.controls
            )
            if selects and len(row.controls) > 1:
                message = f"row {index} mixes a select with other controls; a select occupies its whole row"
                raise DrawInvariantError(message)
            if not selects and len(row.controls) > self.limits.components.row_buttons:
                message = (
                    f"row {index} holds {len(row.controls)} buttons; "
                    f"the maximum is {self.limits.components.row_buttons}"
                )
                raise DrawInvariantError(message)
            if not row.controls:
                message = f"row {index} is empty; planning should not have produced it"
                raise DrawInvariantError(message)
            instructions: list[_ClassicControlInstruction] = []
            for control in row.controls:
                match control:
                    case scene.Link():
                        op = _ClassicControlOp.LINK
                    case scene.PremiumButton():
                        op = _ClassicControlOp.PREMIUM
                    case scene.RoutedButton():
                        op = _ClassicControlOp.ROUTED_BUTTON
                    case scene.RoutedSelect():
                        op = _ClassicControlOp.ROUTED_SELECT
                    case scene.Button() | scene.Select() | scene.EntitySelect():
                        op = _ClassicControlOp.CONTROL
                    case scene.Extension():
                        op = _ClassicControlOp.EXTENSION
                        opaque = True
                instructions.append(_ClassicControlInstruction(op, control))
            rows.append(tuple(instructions))
        return _ClassicProgram(body.content, embeds, tuple(rows), opaque)

    def _embed(self, instruction: _ClassicEmbedInstruction, index: int) -> discord.Embed:
        node = instruction.source
        embed = discord.Embed(
            title=node.title,
            url=node.url,
            description=node.description,
            colour=None if node.colour is None else discord.Colour(node.colour),
            timestamp=instruction.timestamp,
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
        program: _ClassicProgram,
        *,
        plan: PlanResult | None,
        wire: Wire | None,
    ) -> discord.ui.View | None:
        if not program.rows and not self.always_view:
            return None
        view = self.view_factory()
        for index, row in enumerate(program.rows):
            for instruction in row:
                item = self._control(instruction, index, plan=plan, wire=wire)
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

    def _control(
        self,
        instruction: _ClassicControlInstruction,
        row: int,
        *,
        plan: PlanResult | None,
        wire: Wire | None,
    ) -> discord.ui.Item[Any]:
        control = instruction.source
        match instruction.op:
            case _ClassicControlOp.LINK:
                node = cast(scene.Link, control)
                return discord.ui.Button(
                    style=discord.ButtonStyle.link,
                    label=node.label,
                    url=node.url,
                    emoji=discord_emoji(node.emoji),
                    disabled=node.disabled,
                )
            case _ClassicControlOp.PREMIUM:
                return discord.ui.Button(sku_id=cast(scene.PremiumButton, control).sku_id)
            case _ClassicControlOp.ROUTED_BUTTON:
                node = cast(scene.RoutedButton, control)
                return RoutedItem(
                    style=getattr(discord.ButtonStyle, node.style.value),
                    label=node.label,
                    custom_id=node.route_id,
                    emoji=discord_emoji(node.emoji),
                    disabled=node.disabled,
                )
            case _ClassicControlOp.ROUTED_SELECT:
                node = cast(scene.RoutedSelect, control)
                return RoutedSelectItem(
                    options=[_option(option) for option in node.options],
                    custom_id=node.route_id,
                    placeholder=node.placeholder,
                    min_values=node.min_values,
                    max_values=node.max_values,
                    disabled=node.disabled,
                )
            case _ClassicControlOp.CONTROL:
                if plan is None or wire is None:
                    message = "interactive scene controls require a mounted Discord frontend"
                    raise TypeError(message)
                binding = plan.bindings.get(control.action)
                if binding is None:
                    message = f"scene action {control.action!r} has no binding"
                    raise DrawInvariantError(message)
                return wire(control, binding)
            case _ClassicControlOp.EXTENSION:
                kind = cast(scene.Extension, control).kind
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
