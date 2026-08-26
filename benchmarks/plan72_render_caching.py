"""Deterministic cold and unchanged render-cache latency evidence for Plan 72.

Run with ``uv run python benchmarks/plan72_render_caching.py``. The benchmark uses the
Discord test adapter so it measures component expansion, planning, constructor programs and
mount preflight without network latency or a live Discord process.
"""

import asyncio
import gc
import json
import time
from dataclasses import asdict, dataclass
from typing import Any

import squid_ui as sl
from squid_ui import Component, computed, state
from squid_ui.primitives import Panel, Text
from squid_ui.profiling import PresentationStatus
from squid_ui_discord import Everyone, MessageRoot
from squid_ui_discord.testing import commit_render, delivered_to, fake_message


class _Leaf(Component):
    source: int = state(0)

    def __init__(self, *, observed: bool) -> None:
        self.observed = observed
        self.renders = 0

    @computed
    def even(self) -> bool:
        return self.source % 2 == 0

    def render(self):
        self.renders += 1
        return Text(str(self.even)) if self.observed else ()


class _Root(Component):
    def __init__(self, components: int) -> None:
        self.leaves = tuple(_Leaf(observed=index == 0) for index in range(components))
        self.renders = 0

    def render(self):
        self.renders += 1
        return tuple(self.boundary(leaf, key=str(index)) for index, leaf in enumerate(self.leaves))


@dataclass(frozen=True, slots=True)
class RenderCachingResult:
    components: int
    cold_p95_ms: float
    unchanged_p95_ms: float
    unchanged_fraction: float
    cold_samples: int
    unchanged_samples: int


@dataclass(frozen=True, slots=True)
class ChangeScenarioResult:
    name: str
    components: int
    p50_ms: float
    p95_ms: float
    samples: int


def _p95(samples: list[int]) -> int:
    ordered = sorted(samples)
    return ordered[int((len(ordered) - 1) * 0.95)]


def _percentile(samples: list[int], fraction: float) -> int:
    ordered = sorted(samples)
    return ordered[int((len(ordered) - 1) * fraction)]


def _result(name: str, components: int, samples: list[int]) -> ChangeScenarioResult:
    return ChangeScenarioResult(
        name=name,
        components=components,
        p50_ms=_percentile(samples, 0.5) / 1_000_000,
        p95_ms=_percentile(samples, 0.95) / 1_000_000,
        samples=len(samples),
    )


def _commit_candidate(message_root: MessageRoot, candidate: Any) -> PresentationStatus:
    if message_root._same_as_live(candidate):
        message_root._suppress(candidate, None)
        return PresentationStatus.UNCHANGED
    message_root._commit(candidate)
    return PresentationStatus.WRITTEN


def _refresh(message_root: MessageRoot) -> PresentationStatus:
    tree = message_root.runtime.render(reuse_committed=True)
    return _commit_candidate(message_root, message_root._preflight(tree))


def _measure_changes(name: str, components: int, samples: int, prepare, operation) -> ChangeScenarioResult:
    operation()
    gc.collect()
    elapsed: list[int] = []
    gc_enabled = gc.isenabled()
    gc.disable()
    try:
        for index in range(samples):
            prepare(index)
            started = time.perf_counter_ns()
            operation()
            elapsed.append(time.perf_counter_ns() - started)
    finally:
        if gc_enabled:
            gc.enable()
    return _result(name, components, elapsed)


async def _measure_async_changes(
    name: str,
    components: int,
    samples: int,
    prepare,
    operation,
) -> ChangeScenarioResult:
    await operation()
    gc.collect()
    elapsed: list[int] = []
    gc_enabled = gc.isenabled()
    gc.disable()
    try:
        for index in range(samples):
            prepare(index)
            started = time.perf_counter_ns()
            await operation()
            elapsed.append(time.perf_counter_ns() - started)
    finally:
        if gc_enabled:
            gc.enable()
    return _result(name, components, elapsed)


def measure_case(
    components: int,
    *,
    cold_samples: int = 7,
    unchanged_samples: int = 50,
) -> RenderCachingResult:
    """Measure one component count with fresh cold owners and one warmed owner."""
    cold: list[int] = []
    for _ in range(cold_samples):
        message_root = MessageRoot(_Root(components), access=Everyone(), timeout=None)
        started = time.perf_counter_ns()
        commit_render(message_root)
        cold.append(time.perf_counter_ns() - started)
        message_root._teardown()

    root = _Root(components)
    message_root = MessageRoot(root, access=Everyone(), timeout=None)
    commit_render(message_root)
    unchanged: list[int] = []
    gc_enabled = gc.isenabled()
    gc.disable()
    try:
        for index in range(unchanged_samples):
            root.leaves[0].source = (index + 1) * 2
            started = time.perf_counter_ns()
            tree = message_root.runtime.render(reuse_committed=True)
            candidate = message_root._preflight(tree)
            assert message_root._same_as_live(candidate)
            message_root._suppress(candidate, None)
            unchanged.append(time.perf_counter_ns() - started)
    finally:
        if gc_enabled:
            gc.enable()
        message_root._teardown()

    cold_p95 = _p95(cold)
    unchanged_p95 = _p95(unchanged)
    return RenderCachingResult(
        components=components,
        cold_p95_ms=cold_p95 / 1_000_000,
        unchanged_p95_ms=unchanged_p95 / 1_000_000,
        unchanged_fraction=unchanged_p95 / cold_p95,
        cold_samples=cold_samples,
        unchanged_samples=unchanged_samples,
    )


class _ValueLeaf(Component):
    value: int = state(0)

    def __init__(self, *, visible: bool) -> None:
        self.visible = visible
        self.renders = 0

    def render(self):
        self.renders += 1
        return Text(str(self.value)) if self.visible else ()


class _ValueRoot(Component):
    def __init__(self, components: int, visible: int) -> None:
        self.leaves = tuple(_ValueLeaf(visible=index < visible) for index in range(components))
        self.renders = 0

    def render(self):
        self.renders += 1
        return tuple(self.boundary(leaf, key=str(index)) for index, leaf in enumerate(self.leaves))


class _BranchLeaf(Component):
    alternate: bool = state(default=False)

    def __init__(self) -> None:
        self.renders = 0

    def render(self):
        self.renders += 1
        if self.alternate:
            return Panel(children=(Text("alternate"),))
        return Text("primary")


class _TextLeaf(Component):
    long: bool = state(default=False)

    def __init__(self) -> None:
        self.renders = 0

    def render(self) -> Text:
        self.renders += 1
        return Text("x" * (4_500 if self.long else 2_000))


class _MountedChild(Component):
    def __init__(self) -> None:
        self.mounts = 0
        self.unmounts = 0

    def render(self) -> Text:
        return Text("mounted")

    def on_mount(self) -> None:
        self.mounts += 1

    def on_unmount(self) -> None:
        self.unmounts += 1


class _MountingLeaf(Component):
    mounted: bool = state(default=False)

    def __init__(self) -> None:
        self.child = _MountedChild()
        self.renders = 0

    def render(self):
        self.renders += 1
        return self.boundary(self.child, key="child") if self.mounted else Text("unmounted")


class _ResourceLeaf(Component):
    key: int = state(0)

    def __init__(self) -> None:
        self.loads = 0
        self.renders = 0

    @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
    async def value(self) -> str:
        self.loads += 1
        return f"resource:{self.key}"

    def render(self) -> Text:
        self.renders += 1
        status = self.value.status
        assert isinstance(status, sl.resources.Ready)
        return Text(status.value)


def _root_with_special(components: int, special: Component) -> _Root:
    root = _Root(components)
    root.leaves = (special, *root.leaves[1:])
    return root


def _measure_leaf_changes(components: int, changed: int, samples: int) -> ChangeScenarioResult:
    root = _ValueRoot(components, changed)
    message_root = MessageRoot(root, access=Everyone(), timeout=None)
    commit_render(message_root)

    def prepare(index: int) -> None:
        value = index + 1
        for leaf in root.leaves[:changed]:
            leaf.value = value

    try:
        result = _measure_changes(
            f"{changed}_leaf_change", components, samples, prepare, lambda: _refresh(message_root)
        )
        assert root.renders == 1
        assert all(leaf.renders == samples + 1 for leaf in root.leaves[:changed])
        assert all(leaf.renders == 1 for leaf in root.leaves[changed:])
        return result
    finally:
        message_root._teardown()


def _measure_branch_swap(components: int, samples: int) -> ChangeScenarioResult:
    leaf = _BranchLeaf()
    root = _root_with_special(components, leaf)
    message_root = MessageRoot(root, access=Everyone(), timeout=None)
    commit_render(message_root)

    def prepare(index: int) -> None:
        leaf.alternate = index % 2 == 0

    try:
        result = _measure_changes(
            "conditional_branch_swap", components, samples, prepare, lambda: _refresh(message_root)
        )
        assert root.renders == 1
        assert leaf.renders == samples + 1
        return result
    finally:
        message_root._teardown()


def _measure_text_crossing(components: int, samples: int) -> ChangeScenarioResult:
    leaf = _TextLeaf()
    root = _root_with_special(components, leaf)
    message_root = MessageRoot(root, access=Everyone(), timeout=None)
    commit_render(message_root)

    def prepare(index: int) -> None:
        leaf.long = index % 2 == 0

    try:
        result = _measure_changes(
            "planner_text_limit_crossing", components, samples, prepare, lambda: _refresh(message_root)
        )
        assert root.renders == 1
        assert leaf.renders == samples + 1
        assert message_root._plan is not None
        return result
    finally:
        message_root._teardown()


def _measure_subtree_lifecycle(components: int, samples: int) -> ChangeScenarioResult:
    leaf = _MountingLeaf()
    root = _root_with_special(components, leaf)
    message_root = MessageRoot(root, access=Everyone(), timeout=None)
    commit_render(message_root)

    def prepare(index: int) -> None:
        leaf.mounted = index % 2 == 0

    try:
        result = _measure_changes("subtree_mount_unmount", components, samples, prepare, lambda: _refresh(message_root))
        assert root.renders == 1
        assert leaf.renders == samples + 1
        assert leaf.child.mounts == (samples + 1) // 2
        assert leaf.child.unmounts == samples // 2
        return result
    finally:
        message_root._teardown()


async def _measure_resource_resolution(components: int, samples: int) -> ChangeScenarioResult:
    leaf = _ResourceLeaf()
    root = _root_with_special(components, leaf)
    message_root = MessageRoot(root, access=Everyone(), timeout=None)
    await message_root.send(delivered_to(fake_message()))

    def prepare(index: int) -> None:
        leaf.key = index + 1

    async def operation() -> PresentationStatus:
        candidate = await message_root._stage_loaded(preflight=True, reuse_committed=True)
        return _commit_candidate(message_root, candidate)

    try:
        result = await _measure_async_changes("atomic_resource_resolution", components, samples, prepare, operation)
        assert root.renders == 2
        assert leaf.loads == samples + 1
        assert leaf.renders == 2 * (samples + 1)
        return result
    finally:
        message_root._teardown()


async def measure_change_scenarios(
    components: int = 1_000,
    *,
    samples: int = 50,
) -> tuple[ChangeScenarioResult, ...]:
    """Measure representative warmed changes through the mocked mount pipeline."""
    return (
        _measure_leaf_changes(components, 1, samples),
        _measure_leaf_changes(components, 10, samples),
        _measure_branch_swap(components, samples),
        _measure_text_crossing(components, samples),
        await _measure_resource_resolution(components, samples),
        _measure_subtree_lifecycle(components, samples),
    )


def main() -> None:
    unchanged = [measure_case(components) for components in (1, 100, 1_000)]
    changes = asyncio.run(measure_change_scenarios())
    print(
        json.dumps(
            {
                "unchanged": [asdict(result) for result in unchanged],
                "changes": [asdict(result) for result in changes],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
