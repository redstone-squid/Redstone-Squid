"""Resolve durable snapshots to registered targets with matching planning settings.

Recovery checks the target triple, version, limits, and recorded adapter capabilities
so a restored render uses the budgets under which it was planned.
A triple identifies the dialect and adapter profile together. Two targets can share
that identity while using different limits, so the fingerprint must match as well.
"""

from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.target import Target
from squid_ui_discord.target import DISCORD_V1_DPY27, DISCORD_V2_DPY27


class TargetRegistry:
    """Targets available for durable recovery, indexed by target triple.

    Built-in classic and Components V2 targets are registered by default. Registering
    replaces any target with the same triple; older snapshots still need to pass the
    version and fingerprint checks before they can use the replacement.

    Replacement lets an application register tighter limits for a built-in target,
    whose triple stays the same. Snapshots made with the old limits are then refused
    by the fingerprint check rather than silently planned against a different budget.
    """

    def __init__(self, *targets: Target, builtins: bool = True) -> None:
        self._targets: dict[str, Target] = {}
        for target in (DISCORD_V2_DPY27, DISCORD_V1_DPY27) if builtins else ():
            self.register(target)
        for target in targets:
            self.register(target)

    def register(self, target: Target) -> None:
        """Make `target` resolvable under its triple, replacing any already there."""
        self._targets[target.triple] = target

    def resolve(
        self,
        triple: str,
        version: int,
        fingerprint: str,
        adapter_capabilities: tuple[str, ...] | frozenset[str] | None = None,
    ) -> Target:
        """Resolve the recorded target and restrict it to its recorded adapter capabilities.

        Extra currently available capabilities are excluded before comparing fingerprints.

        Raises:
            LayoutInvariantError: The target is absent, required capabilities are missing,
                or the version or restricted fingerprint differs from the snapshot.
        """
        target = self._targets.get(triple)
        if target is None:
            known = ", ".join(sorted(self._targets)) or "none"
            message = f"no target registered for {triple!r} (registered: {known})"
            raise LayoutInvariantError(message)
        if target.version != version:
            message = f"target {triple!r} is version {target.version}; the snapshot was planned at {version}"
            raise LayoutInvariantError(message)
        recorded = target.adapter_capabilities if adapter_capabilities is None else frozenset(adapter_capabilities)
        missing = recorded - target.adapter_capabilities
        if missing:
            names = ", ".join(sorted(missing))
            message = f"target {triple!r} adapter no longer provides recorded capabilities: {names}"
            raise LayoutInvariantError(message)
        restricted = target.restrict_adapter_capabilities(recorded)
        if restricted.fingerprint != fingerprint:
            message = (
                f"target {triple!r} no longer matches the profile this snapshot was planned against; "
                "its capabilities or limits changed, so the stored render was fitted to different budgets"
            )
            raise LayoutInvariantError(message)
        return restricted

    def __contains__(self, triple: object) -> bool:
        return triple in self._targets


DEFAULT_TARGETS = TargetRegistry()
"""The built-in targets, resolvable without any registration."""
