"""Owner-only interaction bench: durable routed buttons that hand a raw interaction to a named probe.

Development-only. `/tests` posts a stepper that walks every probe, every zero-parameter
route and the gone id; each step's Run button carries the real custom id, and a probe's is
`r:bench:{name}`, which the durable router dispatches to the probe registered as `name`. See
`docs/plans/interaction-testbench.md`.
"""

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import anyio
import discord
from discord import Interaction, app_commands
from discord.ext import commands
from discord.ext.commands import Context

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.routes._root import _feature_group, _feature_route, router
from squid.bot.ui import DISCORD_YELLOW, render_payload, text_node
from squid_ui.primitives import ActionStyle, Option, RoutedButton, RoutedSelect, Row, Text
from squid_ui_discord.message_root import AnyMessageRoot
from squid_ui_discord.response import AccessSetting, ResponseOverrides

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid

logger = logging.getLogger(__name__)

bench, _bench_created = _feature_group("bench")
probe_button = _feature_route(bench, "{name}")
"""The stepper's seed: `r:bench:{name}` runs the probe from a message it does not hold."""
probe_seed = _feature_route(bench, "{name}:seed")
"""The seed on a leased message: `r:bench:{name}:seed` runs the probe from its own message."""
probe_select = _feature_route(bench, "{name}:pick")
"""The select form of the stepper's seed; the chosen values reach the probe as `bench.values`."""
step_route = _feature_route(bench, "step:{index:int}")
"""Redraw the stepper at `index`; past the end wraps to the first step."""

GONE_CUSTOM_ID = "r:gone:bench"
"""An id in the router's namespace that nothing owns, so the stepper can press the gone hook."""


class OwnerOnly[BotT: commands.Bot](sd.routing.Middleware[BotT]):
    """Answers a press from anyone but a bot owner with a personal notice and stops there.

    Probes are arbitrary code behind a public button; this gate is what makes that public button safe.
    """

    async def dispatch(self, request: sd.routing.RouteRequest[BotT], proceed: sd.routing.RouteProceed) -> None:
        interaction = request.interaction
        if not await interaction.client.is_owner(interaction.user):
            await _scope(interaction).respond(interaction, text_node("Owner only."), audience="personal")
            return
        await proceed()


if _bench_created:
    bench.add_middleware(OwnerOnly())


def _scope(interaction: Interaction[Any]) -> sd.Scope[Any]:
    return interaction.client.app_ui


class Bench:
    """What a probe gets besides the interaction: the select's values and the lease on its message.

    `leased` is whether the press came from a message this probe already holds, which decides
    whether `lease` posts or edits.
    """

    def __init__(self, interaction: Interaction[RedstoneSquid], name: str, values: tuple[str, ...], *, leased: bool):
        self.interaction = interaction
        self.name = name
        self.values = values
        self.leased = leased

    def seed(self, name: str | None = None) -> RoutedButton:
        """The seed button for `name` (this probe by default), routed to `r:bench:{name}:seed`."""
        return RoutedButton("Seed", probe_seed.id(name=self.name if name is None else name), emoji="\N{SEEDLING}")

    async def lease(
        self,
        content: sd.contracts.DocumentContent | sl.Component[sl.ComponentsV2Target],
        *,
        access: AccessSetting | None = None,
    ) -> sd.ResponseResult:
        """Make `content` plus this probe's seed row the probe's message.

        From the stepper the message is posted, from a leased message it is edited in place. Live
        content is wrapped in `BenchFrame` and mounted under `access` (this owner alone by
        default); when that mount finishes, the seed is re-enabled by a bot-authority edit of
        the message the mount left behind. Editing live content over a mount that is still live
        is not intercepted: the old root's timeout would later disable the new content.
        """
        ui = _scope(self.interaction)
        seed = self.seed()
        seed_row = Row((seed,))
        overrides: ResponseOverrides = {}
        framed: sd.contracts.FacadeContent
        if isinstance(content, sl.Component):
            framed = BenchFrame(content, seed_row)
            overrides["access"] = sd.Owner(self.interaction.user.id) if access is None else access
        else:
            framed = (*_as_nodes(content), seed_row)
        message = self.interaction.message
        if self.leased and message is not None:
            result = await ui.edit(message, framed, **overrides)
        else:
            channel = self.interaction.channel
            assert channel is not None
            result = await ui.send(cast(sd.contracts.SendDestination, channel), framed, **overrides)
        if isinstance(result, sd.Presented):
            await _keep_seed_alive(result, seed.route_id)
        return result


def _as_nodes(content: sd.contracts.DocumentContent) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
    if isinstance(content, sl.Document):
        return tuple(content.children)
    if isinstance(content, Sequence):
        return tuple(content)
    return (content,)


class BenchFrame(sl.Component[sl.ComponentsV2Target]):
    """A leased live component with the seed row drawn after it, so the mount redraws the row itself."""

    def __init__(self, child: sl.Component[sl.ComponentsV2Target], seed_row: Row) -> None:
        self.child = child
        self.seed_row = seed_row

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        return (self.boundary(self.child, key="child"), self.seed_row)


async def _keep_seed_alive(presented: sd.Presented, custom_id: str) -> None:
    """Re-enable the seed after the mount's terminal disable-edit, now if it already happened."""
    message = presented.delivery.message
    if message is None:
        return

    async def revive(_root: AnyMessageRoot) -> None:
        try:
            await revive_seed(message, custom_id)
        except discord.HTTPException:
            logger.exception("bench: could not revive seed %s on message %s", custom_id, message.id)

    presented.root.on_finish(revive)
    # A hook registered on a finished root never fires.
    if presented.root.finished:
        await revive(presented.root)


async def revive_seed(message: discord.Message, custom_id: str) -> None:
    """Re-enable the one item carrying `custom_id` on the message as Discord holds it now, nothing else.

    Raises `discord.HTTPException` from the fetch or the edit.
    """
    fresh = await message.channel.fetch_message(message.id)
    view = discord.ui.LayoutView.from_message(fresh, timeout=None)
    if not isinstance(view, discord.ui.LayoutView) or not enable_item(view, custom_id):
        return
    await fresh.edit(view=view)


def enable_item(view: discord.ui.LayoutView, custom_id: str) -> bool:
    """Clear `disabled` on the item with `custom_id`; whether one was found."""
    for item in view.walk_children():
        if getattr(item, "custom_id", None) == custom_id and hasattr(item, "disabled"):
            item.disabled = False  # pyrefly: ignore[missing-attribute]
            return True
    return False


type ProbeFunction = Callable[[Interaction[RedstoneSquid], Bench], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Probe:
    name: str
    purpose: str
    run: ProbeFunction


PROBES: dict[str, Probe] = {}
"""Every registered probe by name; `probe` adds to it at import."""


def probe(name: str, purpose: str) -> Callable[[ProbeFunction], ProbeFunction]:
    """Register `name` in `PROBES`; a repeat name replaces the earlier probe, which is what a reload does."""

    def register(function: ProbeFunction) -> ProbeFunction:
        PROBES[name] = Probe(name, purpose, function)
        return function

    return register


async def run_probe(
    interaction: Interaction[RedstoneSquid], name: str, values: tuple[str, ...] = (), *, leased: bool = False
) -> None:
    """Time the probe, then reply `name · ok · N ms` personally if it left the interaction unanswered.

    A probe the watchdog deferred and that then stayed silent gets no summary either: from here
    that is indistinguishable from an answer. Exceptions propagate to the router's error hook.
    """
    spec = PROBES.get(name)
    if spec is None:
        await _scope(interaction).respond(
            interaction,
            text_node(f"No probe named `{name}` exists any more.", accent_colour=DISCORD_YELLOW),
            audience="personal",
        )
        return
    started = time.perf_counter()
    await spec.run(interaction, Bench(interaction, name, values, leased=leased))
    elapsed = (time.perf_counter() - started) * 1000
    if not interaction.response.is_done():
        await _scope(interaction).respond(
            interaction, text_node(f"`{name}` · ok · {elapsed:.0f} ms"), audience="personal"
        )


@bench.route(probe_button)
async def press_step_seed(interaction: Interaction[RedstoneSquid], name: str) -> None:
    try:
        await run_probe(interaction, name)
    finally:
        await advance_after(interaction, probe_button.id(name=name))


@bench.route(probe_seed)
async def press_leased_seed(interaction: Interaction[RedstoneSquid], name: str) -> None:
    await run_probe(interaction, name, leased=True)


@bench.select(probe_select)
async def pick_step_seed(interaction: Interaction[RedstoneSquid], values: tuple[str, ...], name: str) -> None:
    try:
        await run_probe(interaction, name, values)
    finally:
        await advance_after(interaction, probe_select.id(name=name))


@bench.route(step_route)
async def show_step(interaction: Interaction[RedstoneSquid], index: int) -> None:
    """Redraw the stepper at `index` as this press's answer."""
    handle = sd.delivery.handle_from(interaction)
    if handle is not None:
        await handle.write(render_payload(step_nodes(index)))


# --- Probes -------------------------------------------------------------------------------------


@probe("raise", "Raise inside a routed handler; expect the error card.")
async def raise_error(interaction: Interaction[RedstoneSquid], bench: Bench) -> None:
    message = "bench"
    raise RuntimeError(message)


@probe("slow", "Sleep past the watchdog, then respond through the managed deferral.")
async def respond_slowly(interaction: Interaction[RedstoneSquid], bench: Bench) -> None:
    await anyio.sleep(router.acknowledgement_timeout + 1)
    await _scope(interaction).respond(interaction, text_node("Late, but here."), audience="personal")


@probe("personal", "A personal reply on a raw interaction.")
async def reply_personally(interaction: Interaction[RedstoneSquid], bench: Bench) -> None:
    await _scope(interaction).respond(interaction, text_node("Only you can see this."), audience="personal")


@probe("locale", "What the request resolves for this member and guild.")
async def report_locale(interaction: Interaction[RedstoneSquid], bench: Bench) -> None:
    request = await sd.request(interaction)
    lines = (
        f"locale: `{request.locale}`",
        f"interaction locale: `{interaction.locale}` · guild locale: `{interaction.guild_locale}`",
        f"user: `{request.user.id}` · guild: `{request.guild.id if request.guild else None}`",
    )
    await request.respond(text_node("\n".join(lines)), audience="personal")


@probe("form", "Open a modal; the submit arrives as its own interaction.")
async def open_form(interaction: Interaction[RedstoneSquid], bench: Bench) -> None:
    async def submitted(submit: discord.Interaction[Any], values: dict[str, object]) -> None:
        await _scope(submit).respond(submit, text_node(f"form · {values!r}"), audience="personal")

    request = await sd.request(interaction)
    form = sl.forms.FormSpec("Bench form", (sl.forms.TextField(key="text", label="Anything", maximum=100),))
    await request.form(form, on_submit=submitted)


@probe("echo", "Reply with the select's values; the pick route's probe.")
async def echo_values(interaction: Interaction[RedstoneSquid], bench: Bench) -> None:
    await _scope(interaction).respond(interaction, text_node(f"values: {bench.values!r}"), audience="personal")


@probe("lease", "Lease a static message; the seed on it re-leases in place.")
async def lease_static(interaction: Interaction[RedstoneSquid], bench: Bench) -> None:
    origin = "the leased message" if bench.leased else "the stepper"
    await bench.lease(Text(f"Leased at <t:{int(time.time())}:T> from {origin}."))


class Closable(sl.Component[sl.ComponentsV2Target]):
    """One Close button that finishes the mount."""

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        return (
            sl.paragraph("Close finishes this mount; the seed below must come back enabled."),
            sl.action_controls(sl.action_control("Close", self._close, key="close"), key="actions"),
        )

    async def _close(self, event: sl.PressEvent) -> None:
        await event.finish()


@probe("finish", "Lease a live component; Close finishes it and the seed must survive.")
async def lease_live(interaction: Interaction[RedstoneSquid], bench: Bench) -> None:
    if bench.leased:
        # Re-leasing over a live mount is the hazard `lease` documents; pressing the seed
        # here only proves it came back.
        return
    await bench.lease(Closable())


# --- The command --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Step:
    """One thing the stepper can press; `control` carries the real custom id."""

    label: str
    purpose: str
    control: RoutedButton | RoutedSelect

    @property
    def custom_id(self) -> str:
        return self.control.route_id


def steps() -> tuple[Step, ...]:
    """Every probe, the select over them, every zero-parameter button route, and the gone id, in that order.

    Built at press time, so a probe or route added since the message was posted is reached
    by pressing Next.
    """
    probes = tuple(PROBES.values())
    listed = [
        Step(p.name, p.purpose, RoutedButton("Run", probe_button.id(name=p.name), style=ActionStyle.PRIMARY))
        for p in probes
    ]
    if "echo" in PROBES:
        listed.append(
            Step(
                "echo (select)",
                "Pick any values; the select route hands them to echo.",
                RoutedSelect(
                    tuple(Option(p.name, p.name, description=p.purpose[:100]) for p in probes),
                    probe_select.id(name="echo"),
                    placeholder="Pick values for echo",
                    max_values=len(probes),
                ),
            )
        )
    listed.extend(
        Step(
            d.format,
            "A zero-parameter route, pressed from a message that is not its card.",
            RoutedButton("Run", sd.routing.Route(d.format).id(), style=ActionStyle.PRIMARY),
        )
        for d in router.describe()
        if not d.params and d.component is sd.routing.RouteComponent.BUTTON and d.group_prefix != bench.prefix
    )
    listed.append(
        Step(GONE_CUSTOM_ID, "An id nothing owns.", RoutedButton("Run", GONE_CUSTOM_ID, style=ActionStyle.DANGER))
    )
    return tuple(listed)


def step_nodes(index: int) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
    """The stepper at `index`, wrapped into range; the Run control, then Next and Restart."""
    listed = steps()
    index %= len(listed)
    step = listed[index]
    nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = [
        Text(f"## Interaction bench\nStep {index + 1}/{len(listed)} — `{step.label}`\n{step.purpose}")
    ]
    buttons: list[RoutedButton] = []
    if isinstance(step.control, RoutedButton):
        buttons.append(step.control)
    else:
        nodes.append(step.control)
    buttons.append(RoutedButton("Next", step_route.id(index=index + 1), emoji="\N{BLACK RIGHT-POINTING TRIANGLE}"))
    buttons.append(RoutedButton("Restart", step_route.id(index=0)))
    nodes.append(Row(tuple(buttons)))
    return tuple(nodes)


async def advance_after(interaction: Interaction[Any], custom_id: str) -> None:
    """Redraw the stepper one past the step whose control is `custom_id`, with the bot's own authority.

    Runs after a probe whether it returned or raised, so the press that produced the error
    card still moves on. A failed edit is logged: it must not mask what the probe raised.
    """
    message = interaction.message
    listed = steps()
    index = next((i for i, step in enumerate(listed) if step.custom_id == custom_id), None)
    if message is None or index is None:
        return
    try:
        await sd.delivery.handle_for(message).write(render_payload(step_nodes(index + 1)))
    except discord.HTTPException:
        logger.exception("bench: could not advance the stepper on message %s", message.id)


def custom_id_nodes(custom_id: str) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
    """One button carrying `custom_id` verbatim; outside `r:` nothing in this bot answers it."""
    return (Text(f"`{custom_id}`"), Row((RoutedButton(custom_id[:80], custom_id),)))


class BenchCog[BotT: RedstoneSquid](sd.Cog[BotT]):
    """`/tests`: post the stepper, or one button with a chosen custom id."""

    # pyrefly: ignore[bad-override]  # MaybeCoro[bool] covers a coroutine; pyrefly drops the parameter
    async def cog_check(self, ctx: Context[BotT]) -> bool:
        return ctx.bot.development_mode and await ctx.bot.is_owner(ctx.author)

    @commands.hybrid_command(name="tests")
    @app_commands.describe(custom_id="A custom id to put on one button instead of the stepper.")
    async def tests(self, ctx: Context[BotT], custom_id: str | None = None) -> None:
        """Post the interaction bench (owner only)."""
        nodes = step_nodes(0) if custom_id is None else custom_id_nodes(custom_id)
        # Public on purpose: an ephemeral message dies with the client session, and the
        # bench is meant to be pressed after restarts.
        await self.ui.respond(ctx, nodes)

    @tests.autocomplete("custom_id")
    async def complete_custom_id(self, interaction: Interaction[BotT], current: str) -> list[app_commands.Choice[str]]:
        formats = sorted({d.format for d in router.describe()} | {a for d in router.describe() for a in d.aliases})
        return [app_commands.Choice(name=f, value=f) for f in formats if current.lower() in f.lower()][:25]


async def setup(bot: RedstoneSquid) -> None:
    await bot.add_cog(BenchCog(bot))
