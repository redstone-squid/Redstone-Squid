"""The registry that lets a durable snapshot name its target and get that target back.

A recovered mount must be rebuilt against the same budgets its stored render was fitted to.
A protocol id alone is not enough: two targets can share `discord.components-v1` and differ
in adapter, capabilities or limits, and rebuilding against the wrong one produces a message
that is legal only by luck. So a snapshot records the *triple* — both axes — plus the version
and a fingerprint, and recovery refuses anything it cannot resolve exactly.
"""

from squid_discord.target import DISCORD_V1_DPY27, DISCORD_V2_DPY27, Target
from squid_layouts.errors import LayoutInvariantError


class TargetRegistry:
    """Targets a durable record may name, by triple.

    The built-in two are registered by default because every consumer has them. A custom
    target — one with tightened limits, or an extra capability — has to be registered
    explicitly: nothing else can reconstruct it from a triple.

    Registering replaces, including over a built-in, because `classic(limits=...)`
    deliberately keeps the built-in triple — it is still a classic message over discord.py,
    just a smaller one — and refusing the override would make customized limits unusable
    with durability. Nothing is lost by allowing it: `resolve` compares the recorded
    *fingerprint*, so a snapshot planned against the target that was replaced is still
    refused by name.
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
        """The exact target a snapshot recorded.

        Raises:
            LayoutInvariantError: The target is unregistered, or is registered with a
                different version or profile than the snapshot was planned against.
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
