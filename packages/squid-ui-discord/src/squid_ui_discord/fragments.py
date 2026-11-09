"""Squid regions contributed to a view somebody else owns.

One Discord message has one lifecycle and edit owner. A fragment is a value contributed to
that owner's next payload, never an independently mounted UI: the host keeps sending,
editing, callback registration, timeout and error policy, and Squid keeps measurement,
planning, drawing and degradation reporting for the region it contributed.

The invariant everything here serves is that *the validated prospective view is the final
view*. A host that appends its own rows after the Squid region hands them to `followed_by`
so they are costed and placed here, rather than after the preflight that was supposed to
prove they fit.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import discord

from squid_ui.assets import Asset
from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.document import DocumentLike
from squid_ui.errors import ExistingLayoutError, LayoutError
from squid_ui.palette import DEFAULT_PALETTE, Palette
from squid_ui.planning.limits import LIMITS, Axis, V2Limits
from squid_ui.planning.resources import EMPTY_COST, ResourceCost
from squid_ui.planning.result import PlanReport, PlanResult
from squid_ui.planning.target import Target
from squid_ui.scene.model import ComponentsV2
from squid_ui.target_types import ComponentsV2Target
from squid_ui.text import NEUTRAL, Localization
from squid_ui_discord.attachments import files_for
from squid_ui_discord.inspection import MessageReservation, audit, cost, measure
from squid_ui_discord.rendering import render_message
from squid_ui_discord.target import DISCORD_V2_DPY27

if TYPE_CHECKING:
    from squid_ui_discord.request import Request


class StaleReservationError(LayoutError):
    """The host changed after planning, so the fragment's budget no longer describes it."""


class FragmentOwnershipError(LayoutError):
    """A fragment was asked to carry state or dispatch that only a mount can own."""


@dataclass(slots=True)
class AttachedFragment:
    """The items a fragment put into a host view, and what it takes to undo that."""

    items: tuple[discord.ui.Item[Any], ...]
    view: discord.ui.LayoutView
    plan: PlanResult[ComponentsV2]
    assets: tuple[Asset, ...]
    fingerprint: str

    @property
    def report(self) -> PlanReport:
        """The plan's degradation report, so a one-call contribution never hides one."""
        return self.plan.report

    def build_files(self) -> list[discord.File]:
        """Materialize fresh file wrappers; repeatable, because a sent file cannot be re-sent."""
        return files_for(self.assets)

    def attachments(self, host_files: Sequence[discord.File] = ()) -> list[discord.File]:
        """Return host files followed by fresh wrappers for the fragment's upload assets.

        Include these files in delivery so the fragment's attachment:// references resolve.
        """
        return [*host_files, *self.build_files()]

    def stale(self) -> bool:
        """Compare the view's current measurements with its attachment-time fingerprint.

        Content changes preserving those measurements are not detected. The comparison
        omits external attachment counts, so a nonzero attachments argument at attach time
        makes this report stale even when the view has not changed.
        """
        return measure(self.view).fingerprint != self.fingerprint

    def remove(self) -> None:
        """Remove exactly the items this fragment inserted.

        Identity-based, so a host replacement carrying the same custom id is never mistaken
        for the item it replaced.
        """
        for item in self.items:
            if any(child is item for child in self.view.children):
                self.view.remove_item(item)


@dataclass(slots=True)
class Fragment:
    """A planned, drawn, sessionless region waiting for one attach or release attempt.

    A failed reservation or preflight check consumes the fragment; re-plan before
    trying again. Failed insertion restores staging so that attempt can be retried.
    """

    items: tuple[discord.ui.Item[Any], ...]
    plan: PlanResult[ComponentsV2]
    assets: tuple[Asset, ...]
    reservation: MessageReservation
    followed_by: tuple[discord.ui.Item[Any], ...] = ()
    _staging: discord.ui.LayoutView | None = field(default=None, repr=False)
    _attached: bool = field(default=False, repr=False)

    @property
    def report(self) -> PlanReport:
        """The plan's degradation report; `contribute` passes it through unchanged."""
        return self.plan.report

    def build_files(self) -> list[discord.File]:
        """Materialize fresh file wrappers for this planned fragment."""
        return files_for(self.assets)

    def release(self) -> tuple[discord.ui.Item[Any], ...]:
        """Release rendered items for manual placement, consuming this fragment.

        The caller takes responsibility for ordering, preflight, and rollback. followed_by
        items are not included in the returned tuple.
        """
        self._claim()
        self._detach_all()
        return self.items

    def attach(self, view: discord.ui.LayoutView, *, attachments: int = 0) -> AttachedFragment:
        """Check the reservation and prospective view, then append the region and trailing items.

        Uses attachments for host files beyond this fragment's assets. Checks run before
        insertion; if add_item fails, successfully inserted items are removed and the
        fragment's items are restaged. A reservation or preflight failure requires replanning.
        """
        staging = self._staging
        self._claim()
        current = measure(view, attachments=attachments)
        if current.fingerprint != self.reservation.fingerprint:
            message = (
                "the host view changed after this fragment was planned; re-plan against the "
                "current view rather than applying a budget calculated for a different one"
            )
            raise StaleReservationError(message)

        _preflight(
            view,
            fragment_items=self.items,
            trailing=self.followed_by,
            staging=staging,
            reservation=current,
            assets=self.assets,
        )

        self._detach_all()
        added: list[discord.ui.Item[Any]] = []
        try:
            for item in (*self.items, *self.followed_by):
                view.add_item(item)
                added.append(item)
        except Exception:
            for item in reversed(added):
                view.remove_item(item)
            self._restage()
            raise

        return AttachedFragment(
            items=tuple(added),
            view=view,
            plan=self.plan,
            assets=self.assets,
            fingerprint=measure(view, attachments=attachments).fingerprint,
        )

    def _claim(self) -> None:
        if self._attached:
            message = "this fragment has already been placed; plan a new one for another view"
            raise FragmentOwnershipError(message)
        self._attached = True

    def _detach_all(self) -> None:
        staging = self._staging
        if staging is None:
            return
        for item in self.items:
            staging.remove_item(item)
        self._staging = None

    def _restage(self) -> None:
        """Return the items to renderer ownership after a failed attachment."""
        staging = discord.ui.LayoutView(timeout=None)
        for item in self.items:
            staging.add_item(item)
        self._staging = staging
        self._attached = False


def fragment(
    document: DocumentLike[ComponentsV2Target, Request],
    *,
    alongside: discord.ui.LayoutView | None = None,
    reserve: ResourceCost = EMPTY_COST,
    followed_by: Sequence[discord.ui.Item[Any]] = (),
    attachments: int = 0,
    target: Target = DISCORD_V2_DPY27,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    palette: Palette = DEFAULT_PALETTE,
    strict: bool = False,
    positions: Mapping[str, Any] | None = None,
) -> Fragment:
    """Plan a sessionless document into what a host view leaves unspent.

    `alongside` is measured; `reserve` and `followed_by` describe room the host will need but
    has not claimed yet. An already-invalid host raises before anything is planned, because
    fragment composition cannot repair arbitrary host content or choose which of its items
    should be lost.
    """
    host = measure(alongside, attachments=attachments) if alongside is not None else _empty_reservation(attachments)
    host.raise_if_invalid()

    trailing = tuple(followed_by)
    reservation = host.cost + reserve + cost(*trailing)
    rendered = render_message(
        document,
        target=target,
        chrome=chrome,
        localization=localization,
        palette=palette,
        strict=strict,
        reservation=reservation,
        positions=positions,
    )
    view = rendered.view
    _reject_dispatchable(view)

    items = tuple(view.children)
    return Fragment(
        items=items,
        plan=rendered.plan,
        assets=rendered.assets,
        reservation=host,
        followed_by=trailing,
        _staging=view,
    )


def contribute(
    document: DocumentLike[ComponentsV2Target, Request],
    *,
    to: discord.ui.LayoutView,
    followed_by: Sequence[discord.ui.Item[Any]] = (),
    reserve: ResourceCost = EMPTY_COST,
    attachments: int = 0,
    target: Target = DISCORD_V2_DPY27,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    palette: Palette = DEFAULT_PALETTE,
    strict: bool = False,
    positions: Mapping[str, Any] | None = None,
) -> AttachedFragment:
    """Plan a fragment against `to`, place it, and append the host's trailing items.

    The host view is named once. Measuring against one view and attaching to another would
    void every guarantee here, and the two-step `fragment()` + `attach()` form takes it twice.

    This never sends: delivery stays with the owner of the message.
    """
    planned = fragment(
        document,
        alongside=to,
        reserve=reserve,
        followed_by=followed_by,
        attachments=attachments,
        target=target,
        chrome=chrome,
        localization=localization,
        palette=palette,
        strict=strict,
        positions=positions,
    )
    return planned.attach(to, attachments=attachments)


def _empty_reservation(attachments: int) -> MessageReservation:
    return measure(discord.ui.LayoutView(timeout=None), attachments=attachments)


def _preflight(
    view: discord.ui.LayoutView,
    *,
    fragment_items: Sequence[discord.ui.Item[Any]],
    trailing: Sequence[discord.ui.Item[Any]],
    staging: discord.ui.LayoutView | None,
    reservation: MessageReservation,
    assets: Sequence[Asset],
    limits: V2Limits = LIMITS,
) -> None:
    """Check ownership, budgets, IDs, and local item limits before inserting into the host."""
    for item in fragment_items:
        if item.view is not None and item.view is not staging:
            message = "a fragment item is already parented in another view"
            raise FragmentOwnershipError(message)
    for item in trailing:
        if item.view is not None and item.view is not view:
            message = f"trailing item {type(item).__name__} already belongs to another view"
            raise FragmentOwnershipError(message)

    additions = (*fragment_items, *trailing)
    addition_cost = cost(*additions)
    components = reservation.cost.get(Axis.COMPONENTS) + addition_cost.get(Axis.COMPONENTS)
    text = reservation.cost.get(Axis.DISPLAY_TEXT) + addition_cost.get(Axis.DISPLAY_TEXT)
    attachments = reservation.cost.get(Axis.ATTACHMENTS) + len(assets)

    problems: list[str] = []
    if components > limits.total_components:
        problems.append(f"{components} components exceed {limits.total_components}")
    if text > limits.total_text:
        problems.append(f"total display text {text} > {limits.total_text}")
    if attachments > limits.attachments:
        problems.append(f"{attachments} attachments exceed {limits.attachments}")

    seen = {site.custom_id for site in reservation.custom_ids}
    for item in additions:
        for custom_id in _walk_ids(item):
            if custom_id in seen:
                problems.append(f"custom id {custom_id!r} is already used in this message")
            seen.add(custom_id)

    # Local structure and string limits, on the contributed items only: repairing the host's
    # own content here would violate its ownership of the message.
    for item in additions:
        probe = discord.ui.LayoutView(timeout=None)
        probe.add_item(item)
        problems.extend(audit(probe, limits=limits).messages)
        probe.remove_item(item)

    if problems:
        raise ExistingLayoutError(problems)


def _walk_ids(item: object) -> list[str]:
    """Every custom id in one item's subtree, including a section's accessory."""
    found: list[str] = []
    custom_id = getattr(item, "custom_id", None)
    if isinstance(custom_id, str):
        found.append(custom_id)
    for child in getattr(item, "children", None) or ():
        found.extend(_walk_ids(child))
    accessory = getattr(item, "accessory", None)
    if accessory is not None:
        found.extend(_walk_ids(accessory))
    return found


def _reject_dispatchable(view: discord.ui.view.BaseView) -> None:
    """Refuse any control the host would have to dispatch for us.

    Component-local callbacks are already refused during sessionless planning. This closes
    the native-item hatch: a `NativeItem` subtree can carry an arbitrary discord.py callback,
    which the host view would then own — the exact boundary the ownership rule draws.
    """
    for item in view.walk_children():
        if item.is_dispatchable():
            message = (
                f"{type(item).__name__} dispatches through its view, which a fragment does not own; "
                "use a routed control, a link button, or hand the whole message to a MessageRoot"
            )
            raise FragmentOwnershipError(message)
