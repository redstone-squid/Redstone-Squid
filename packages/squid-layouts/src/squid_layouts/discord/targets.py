"""The registry that lets a durable snapshot name its target and get that target back.

A recovered mount must be rebuilt against the same budgets its stored render was fitted to.
An id alone is not enough: two profiles can share `discord.components-v1` and differ in
capabilities or limits, and rebuilding against the wrong one produces a message that is legal
only by luck. So a snapshot records the id, the version, and a fingerprint of the profile, and
recovery refuses anything it cannot resolve exactly.
"""

from squid_layouts.discord.target import CLASSIC_TARGET, V2_TARGET, Target
from squid_layouts.errors import LayoutInvariantError


class TargetRegistry:
    """Targets a durable record may name, by id.

    The built-in two are registered by default because every consumer has them. A custom
    target — one with tightened limits, or an extra capability — has to be registered
    explicitly: nothing else can reconstruct it from an id.

    Registering replaces, including over a built-in, because `Target.classic(limits=...)`
    deliberately keeps the built-in id — it is still a classic message, just a smaller one —
    and refusing the override would make customized limits unusable with durability. Nothing
    is lost by allowing it: `resolve` compares the recorded *fingerprint*, so a snapshot
    planned against the profile that was replaced is still refused by name.
    """

    def __init__(self, *targets: Target, builtins: bool = True) -> None:
        self._targets: dict[str, Target] = {}
        for target in (V2_TARGET, CLASSIC_TARGET) if builtins else ():
            self.register(target)
        for target in targets:
            self.register(target)

    def register(self, target: Target) -> None:
        """Make `target` resolvable under its id, replacing any profile already there."""
        self._targets[target.id] = target

    def resolve(
        self,
        target_id: str,
        version: int,
        fingerprint: str,
        adapter_capabilities: tuple[str, ...] | frozenset[str] | None = None,
    ) -> Target:
        """The exact target a snapshot recorded.

        Raises:
            LayoutInvariantError: The target is unregistered, or is registered with a
                different version or profile than the snapshot was planned against.
        """
        target = self._targets.get(target_id)
        if target is None:
            known = ", ".join(sorted(self._targets)) or "none"
            message = f"no target registered for {target_id!r} (registered: {known})"
            raise LayoutInvariantError(message)
        if target.version != version:
            message = f"target {target_id!r} is version {target.version}; the snapshot was planned at {version}"
            raise LayoutInvariantError(message)
        recorded = target.adapter_capabilities if adapter_capabilities is None else frozenset(adapter_capabilities)
        missing = recorded - target.adapter_capabilities
        if missing:
            names = ", ".join(sorted(missing))
            message = f"target {target_id!r} adapter no longer provides recorded capabilities: {names}"
            raise LayoutInvariantError(message)
        restricted = target.restrict_adapter_capabilities(recorded)
        if restricted.fingerprint != fingerprint:
            message = (
                f"target {target_id!r} no longer matches the profile this snapshot was planned against; "
                "its capabilities or limits changed, so the stored render was fitted to different budgets"
            )
            raise LayoutInvariantError(message)
        return restricted

    def __contains__(self, target_id: object) -> bool:
        return target_id in self._targets


DEFAULT_TARGETS = TargetRegistry()
"""The built-in targets, resolvable without any registration."""
