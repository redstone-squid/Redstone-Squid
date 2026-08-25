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
from typing import Any

import discord

from squid_discord.attachments import files_for
from squid_discord.composition import compose
from squid_discord.inspection import DiscordReservation, audit, cost, measure
from squid_discord.target import V2_TARGET, Target
from squid_layouts.assets import Asset
from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.document import DocumentLike
from squid_layouts.errors import ExistingLayoutError, LayoutError
from squid_layouts.palette import DEFAULT_PALETTE, Palette
from squid_layouts.planning.limits import LIMITS, V2Limits
from squid_layouts.planning.planner import EMPTY_RESERVATION
from squid_layouts.planning.target import ResourceCost
from squid_layouts.scene.model import PlanReport, PlanResult
from squid_layouts.text import NEUTRAL, Localization


class StaleReservationError(LayoutError):
    """The host changed after planning, so the fragment's budget no longer describes it."""


class FragmentOwnershipError(LayoutError):
    """A fragment was asked to carry state or dispatch that only a mount can own."""


@dataclass(slots=True)
class AttachedFragment:
    """The items a fragment put into a host view, and what it takes to undo that."""

    items: tuple[discord.ui.Item[Any], ...]
    view: discord.ui.LayoutView
    plan: PlanResult
    assets: tuple[Asset, ...]
    fingerprint: str

    @property
    def report(self) -> PlanReport:
        """The plan's degradation report, so a one-call contribution never hides one."""
        return self.plan.report

    def files(self) -> list[discord.File]:
        """Materialize fresh file wrappers; repeatable, because a sent file cannot be re-sent."""
        return files_for(self.assets)

    def attachments(self, host_files: Sequence[discord.File] = ()) -> list[discord.File]:
        """Merge the host's files with the fragment's, in that order.

        Exists because `[*host_files, *fragment.files()]` is the one line a host can forget,
        and forgetting it breaks `attachment://` references rather than raising.
        """
        return [*host_files, *self.files()]

    def stale(self) -> bool:
        """Whether the host has changed since this fragment was planned against it."""
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
    """A planned, drawn, sessionless Squid region waiting to be placed."""

    items: tuple[discord.ui.Item[Any], ...]
    plan: PlanResult
    assets: tuple[Asset, ...]
    reservation: DiscordReservation
    followed_by: tuple[discord.ui.Item[Any], ...] = ()
    _staging: discord.ui.LayoutView | None = field(default=None, repr=False)
    _attached: bool = field(default=False, repr=False)

    @property
    def report(self) -> PlanReport:
        """The plan's degradation report; `contribute` passes it through unchanged."""
        return self.plan.report

    def files(self) -> list[discord.File]:
        return files_for(self.assets)

    def release(self) -> tuple[discord.ui.Item[Any], ...]:
        """Hand the items over for manual placement, dropping the atomicity promise.

        Deliberately not called `detach`: the inverse of `attach` is
        `AttachedFragment.remove`, and a pair of names that look like inverses but are not is
        a trap. After this the caller owns ordering, preflight, and rollback.
        """
        self._claim()
        self._detach_all()
        return self.items

    def attach(self, view: discord.ui.LayoutView, *, attachments: int = 0) -> AttachedFragment:
        """Validate the complete prospective view, then append this fragment to it.

        Nothing is moved until the whole result is known to be legal, and a host subclass
        that raises from `add_item` gets everything rolled back.
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
    document: DocumentLike,
    *,
    alongside: discord.ui.LayoutView | None = None,
    reserve: ResourceCost = EMPTY_RESERVATION,
    followed_by: Sequence[discord.ui.Item[Any]] = (),
    attachments: int = 0,
    target: Target = V2_TARGET,
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
    composition = compose(
        document,
        target=target,
        chrome=chrome,
        localization=localization,
        palette=palette,
        strict=strict,
        reservation=reservation,
        positions=positions,
    )
    view = composition.view
    _reject_dispatchable(view)

    items = tuple(view.children)
    return Fragment(
        items=items,
        plan=composition.plan,
        assets=composition.assets,
        reservation=host,
        followed_by=trailing,
        _staging=view,
    )


def contribute(
    document: DocumentLike,
    *,
    to: discord.ui.LayoutView,
    followed_by: Sequence[discord.ui.Item[Any]] = (),
    reserve: ResourceCost = EMPTY_RESERVATION,
    attachments: int = 0,
    target: Target = V2_TARGET,
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


def _empty_reservation(attachments: int) -> DiscordReservation:
    return measure(discord.ui.LayoutView(timeout=None), attachments=attachments)


def _preflight(
    view: discord.ui.LayoutView,
    *,
    fragment_items: Sequence[discord.ui.Item[Any]],
    trailing: Sequence[discord.ui.Item[Any]],
    staging: discord.ui.LayoutView | None,
    reservation: DiscordReservation,
    assets: Sequence[Asset],
    limits: V2Limits = LIMITS,
) -> None:
    """Prove the whole prospective view is legal before one item moves."""
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
    components = reservation.cost.get("components") + addition_cost.get("components")
    text = reservation.cost.get("display_text") + addition_cost.get("display_text")
    attachments = reservation.cost.get("attachments") + len(assets)

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


def _reject_dispatchable(view: discord.ui.LayoutView) -> None:
    """Refuse any control the host would have to dispatch for us.

    Component-local callbacks are already refused during sessionless planning. This closes
    the native-item hatch: a `NativeItem` subtree can carry an arbitrary discord.py callback,
    which the host view would then own — the exact boundary the ownership rule draws.
    """
    for item in view.walk_children():
        if item.is_dispatchable():
            message = (
                f"{type(item).__name__} dispatches through its view, which a fragment does not own; "
                "use a routed control, a link button, or hand the whole message to a Mount"
            )
            raise FragmentOwnershipError(message)
