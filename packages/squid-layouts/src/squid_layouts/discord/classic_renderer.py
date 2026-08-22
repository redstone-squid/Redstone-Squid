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

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import discord
from discord.ui.select import BaseSelect

from squid_layouts.actions import ActionBinding
from squid_layouts.discord.attachments import attachment_assets
from squid_layouts.discord.presentation import DiscordPresentation
from squid_layouts.discord.renderer import RoutedItem, RoutedSelectItem
from squid_layouts.errors import DrawInvariantError
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
    SceneExtension,
    SceneLink,
    SceneRoutedButton,
    SceneRoutedSelect,
    SceneSelect,
)

type Control = SceneButton | SceneSelect
type Wire = Callable[[Control, ActionBinding], discord.ui.Item[Any]]
type ClassicViewFactory = Callable[[], discord.ui.View]

ALLOWED_SCHEMES = frozenset({"http", "https", "attachment"})
"""What Discord accepts in an embed URL. Anything else is rejected before it is sent."""


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
    ) -> None:
        self.limits = limits
        self.audit = audit
        self.view_factory = view_factory

    def draw(
        self,
        scene: SceneDocument,
        *,
        plan: PlanResult | None = None,
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

    def _body(self, scene: SceneDocument) -> SceneClassicMessage:
        if scene.protocol != SceneCodec.protocol:
            message = f"ClassicRenderer cannot draw scene protocol {scene.protocol}"
            raise DrawInvariantError(message)
        if scene.target != "discord.components-v1":
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
        if not body.rows:
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
        selects = sum(isinstance(control, SceneSelect | SceneRoutedSelect) for control in row.controls)
        if selects and len(row.controls) > 1:
            message = f"row {index} mixes a select with other controls; a select occupies its whole row"
            raise DrawInvariantError(message)
        if not selects and len(row.controls) > self.limits.row_buttons:
            message = f"row {index} holds {len(row.controls)} buttons; the maximum is {self.limits.row_buttons}"
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
                return discord.ui.Button(style=discord.ButtonStyle.link, label=label, url=url)
            case SceneRoutedButton(label=label, route_id=route_id):
                return RoutedItem(
                    style=getattr(discord.ButtonStyle, control.style.value),
                    label=label,
                    custom_id=route_id,
                    emoji=control.emoji,
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
            case SceneButton() | SceneSelect():
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
    )


def audit_classic_payload(
    *,
    content: str | None,
    embeds: Sequence[discord.Embed],
    view: discord.ui.View | None,
    attachments: int = 0,
    limits: ClassicLimits = CLASSIC_LIMITS,
) -> list[str]:
    """Every way this payload would be rejected, found before it is sent.

    Walks `Embed.to_dict()` because that is the wire shape, and cross-checks the aggregate
    against `Embed.__len__`, which already computes exactly Discord's definition — title,
    description, field names and values, footer text, author name. Re-deriving that sum here
    would be a second, drifting definition of the same rule.
    """
    problems: list[str] = []

    if content is not None and len(content) > limits.content:
        problems.append(f"content is {len(content)} characters; the limit is {limits.content}")
    if len(embeds) > limits.embeds:
        problems.append(f"{len(embeds)} embeds exceed {limits.embeds}")

    seen_urls: set[str] = set()
    for index, embed in enumerate(embeds):
        problems.extend(_audit_embed(embed, index, limits))
        url = embed.url
        if url is None:
            continue
        if url in seen_urls:
            # Discord renders only the first embed of a repeated URL, so a second one is
            # silently invisible rather than an error — which is worse than an error.
            problems.append(f"embed {index} repeats the URL {url!r}; Discord shows only the first")
        seen_urls.add(url)

    aggregate = sum(len(embed) for embed in embeds)
    if aggregate > limits.embed_text:
        problems.append(f"embed text totals {aggregate} characters; the limit is {limits.embed_text}")

    if view is not None:
        problems.extend(_audit_view(view, limits))
    if attachments > limits.attachments:
        problems.append(f"{attachments} attachments exceed {limits.attachments}")
    return problems


def _audit_embed(embed: discord.Embed, index: int, limits: ClassicLimits) -> list[str]:
    payload = embed.to_dict()
    problems: list[str] = []

    def check(value: object, cap: int, what: str) -> None:
        if isinstance(value, str) and len(value) > cap:
            problems.append(f"embed {index} {what} is {len(value)} characters; the limit is {cap}")

    check(payload.get("title"), limits.embed_title, "title")
    check(payload.get("description"), limits.embed_description, "description")
    check((payload.get("footer") or {}).get("text"), limits.embed_footer, "footer")
    check((payload.get("author") or {}).get("name"), limits.embed_author, "author")

    fields = payload.get("fields") or []
    if len(fields) > limits.embed_fields:
        problems.append(f"embed {index} has {len(fields)} fields; the limit is {limits.embed_fields}")
    for position, field in enumerate(fields):
        check(field.get("name"), limits.field_name, f"field {position} name")
        check(field.get("value"), limits.field_value, f"field {position} value")
        if not (field.get("name") or "").strip():
            problems.append(f"embed {index} field {position} has an empty name")
        if not (field.get("value") or "").strip():
            problems.append(f"embed {index} field {position} has an empty value")

    for key in ("url", "image", "thumbnail", "footer", "author"):
        raw = payload.get(key)
        candidate = raw if isinstance(raw, str) else (raw or {}).get("url") or (raw or {}).get("icon_url")
        if isinstance(candidate, str) and candidate:
            scheme = urlsplit(candidate).scheme
            if scheme not in ALLOWED_SCHEMES:
                problems.append(f"embed {index} {key} uses the unsupported URL scheme {scheme or '(none)'!r}")
    return problems


def _audit_view(view: discord.ui.View, limits: ClassicLimits) -> list[str]:
    problems: list[str] = []
    children = list(view.children)
    if len(children) > limits.controls:
        problems.append(f"{len(children)} view children exceed {limits.controls}")

    rows: dict[int, list[discord.ui.Item[Any]]] = {}
    for item in children:
        rows.setdefault(item.row if item.row is not None else -1, []).append(item)
    if len(rows) > limits.rows:
        problems.append(f"{len(rows)} action rows exceed {limits.rows}")
    for index, items in sorted(rows.items()):
        selects = sum(isinstance(item, BaseSelect) for item in items)
        if selects and len(items) > 1:
            problems.append(f"row {index} mixes a select with {len(items) - 1} other controls")
        if selects > 1:
            problems.append(f"row {index} holds {selects} selects; a row holds one")
        if not selects and len(items) > limits.row_buttons:
            problems.append(f"row {index} holds {len(items)} buttons; the limit is {limits.row_buttons}")

    seen: set[str] = set()
    for item in children:
        custom_id = getattr(item, "custom_id", None)
        if not isinstance(custom_id, str):
            continue
        if len(custom_id) > limits.custom_id:
            problems.append(f"custom id {custom_id!r} is {len(custom_id)} characters; the limit is {limits.custom_id}")
        if custom_id in seen:
            problems.append(f"custom id {custom_id!r} appears twice in one message")
        seen.add(custom_id)
    return problems
