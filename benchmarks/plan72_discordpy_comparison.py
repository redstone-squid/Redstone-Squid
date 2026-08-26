"""Compare discord.py LayoutView construction with Squid's warmed render paths.

Run with ``uv run python benchmarks/plan72_discordpy_comparison.py``. Class declaration,
scene planning, render-program compilation, state mutation and network delivery are outside
the timed regions. Every measured construction still returns a fresh mutable LayoutView.
"""

import gc
import json
import platform
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import partial

import discord

import squid_ui as sl
from squid_ui import scene
from squid_ui.primitives import ActionStyle, RoutedButton, Row
from squid_ui.scene import Codec
from squid_ui_discord import DISCORD_V2_DPY27, Everyone, MessageRoot
from squid_ui_discord.render_cache import RenderProgramCache
from squid_ui_discord.renderer import V2Renderer
from squid_ui_discord.testing import commit_render


@dataclass(frozen=True, slots=True)
class ConstructionResult:
    profile: str
    implementation: str
    controls: int
    total_nodes: int
    p50_ms: float
    p95_ms: float
    ratio_to_imperative: float
    samples: int


def _percentile(samples: list[int], fraction: float) -> int:
    ordered = sorted(samples)
    return ordered[int((len(ordered) - 1) * fraction)]


def _measure(factory, samples: int) -> tuple[int, int]:
    warm = factory()
    warm.stop()
    gc.collect()
    elapsed: list[int] = []
    gc_enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(samples):
            started = time.perf_counter_ns()
            view = factory()
            elapsed.append(time.perf_counter_ns() - started)
            view.stop()
    finally:
        if gc_enabled:
            gc.enable()
    return _percentile(elapsed, 0.5), _percentile(elapsed, 0.95)


def _alternating(samples: int, factory: Callable[[bool], discord.ui.LayoutView]) -> Callable[[], discord.ui.LayoutView]:
    variants = iter((False, True) * (samples // 2 + 2))

    def draw() -> discord.ui.LayoutView:
        return factory(next(variants))

    return draw


def _rows(controls: int) -> tuple[int, ...]:
    full, remainder = divmod(controls, 5)
    return (5,) * full + ((remainder,) if remainder else ())


def _label(index: int, alternate: bool) -> str:
    return "Changed" if index == 0 and alternate else f"Button {index + 1}"


def _decorated_view_type(controls: int, *, alternate: bool) -> type[discord.ui.LayoutView]:
    namespace: dict[str, object] = {}
    offset = 0
    for row_index, row_size in enumerate(_rows(controls)):
        row = discord.ui.ActionRow()
        namespace[f"row_{row_index}"] = row
        for column in range(row_size):
            index = offset + column

            async def callback(_self, _interaction, _button) -> None:
                pass

            callback.__name__ = f"button_{index}"
            decorated = row.button(label=_label(index, alternate), custom_id=f"button:{index}")(callback)
            namespace[callback.__name__] = decorated
        offset += row_size
    return type(f"Decorated{controls}{'Alternate' if alternate else ''}", (discord.ui.LayoutView,), namespace)


def _imperative_view(controls: int, alternate: bool) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    offset = 0
    for row_size in _rows(controls):
        row = discord.ui.ActionRow()
        for column in range(row_size):
            index = offset + column
            row.add_item(discord.ui.Button(label=_label(index, alternate), custom_id=f"button:{index}"))
        view.add_item(row)
        offset += row_size
    return view


def _decorated_instance(decorated: dict[bool, type[discord.ui.LayoutView]], alternate: bool) -> discord.ui.LayoutView:
    return decorated[alternate](timeout=None)


def _render_scene(
    renderer: V2Renderer,
    scenes: dict[bool, scene.Scene[scene.ComponentsV2]],
    alternate: bool,
) -> discord.ui.LayoutView:
    return renderer.view(scenes[alternate])


def _button_scene(controls: int, *, alternate: bool) -> scene.Scene[scene.ComponentsV2]:
    offset = 0
    rows: list[scene.Row] = []
    for row_size in _rows(controls):
        rows.append(
            scene.Row(
                tuple(
                    scene.RoutedButton(
                        _label(offset + column, alternate),
                        f"button:{offset + column}",
                        ActionStyle.SECONDARY,
                    )
                    for column in range(row_size)
                )
            )
        )
        offset += row_size
    return scene.Scene(Codec.protocol, DISCORD_V2_DPY27.id, 1, scene.ComponentsV2(tuple(rows)))


def _normalized(view: discord.ui.LayoutView) -> list[dict[str, object]]:
    return view.to_components()


class _ButtonRow(sl.Component):
    alternate: bool = sl.state(default=False)

    def __init__(self, offset: int, size: int) -> None:
        self.offset = offset
        self.size = size
        self.renders = 0

    def render(self) -> Row:
        self.renders += 1
        return Row(
            tuple(
                RoutedButton(
                    _label(self.offset + column, self.alternate),
                    f"button:{self.offset + column}",
                )
                for column in range(self.size)
            )
        )


class _ButtonRoot(sl.Component):
    def __init__(self, controls: int) -> None:
        offset = 0
        rows: list[_ButtonRow] = []
        for size in _rows(controls):
            rows.append(_ButtonRow(offset, size))
            offset += size
        self.rows = tuple(rows)
        self.renders = 0

    def render(self):
        self.renders += 1
        return tuple(self.boundary(row, key=str(index)) for index, row in enumerate(self.rows))


def _measure_pipeline(controls: int, samples: int) -> tuple[int, int]:
    root = _ButtonRoot(controls)
    message_root = MessageRoot(root, access=Everyone(), timeout=None)
    commit_render(message_root)
    root.rows[0].alternate = True
    candidate = message_root._preflight(message_root.runtime.render(reuse_committed=True))
    message_root._commit(candidate)  # pyrefly: ignore[bad-argument-type]
    elapsed: list[int] = []
    gc.collect()
    gc_enabled = gc.isenabled()
    gc.disable()
    try:
        for index in range(samples):
            root.rows[0].alternate = index % 2 == 1
            started = time.perf_counter_ns()
            candidate = message_root._preflight(message_root.runtime.render(reuse_committed=True))
            elapsed.append(time.perf_counter_ns() - started)
            message_root._commit(candidate)  # pyrefly: ignore[bad-argument-type]
    finally:
        if gc_enabled:
            gc.enable()
        message_root._teardown()
    assert root.renders == 1
    assert root.rows[0].renders == samples + 2
    assert all(row.renders == 1 for row in root.rows[1:])
    return _percentile(elapsed, 0.5), _percentile(elapsed, 0.95)


def _rich_imperative_view() -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    for panel_index in range(5):
        row = discord.ui.ActionRow(
            *(
                discord.ui.Button(
                    label=f"Button {panel_index * 5 + column + 1}",
                    custom_id=f"rich:{panel_index}:{column}",
                )
                for column in range(5)
            )
        )
        view.add_item(discord.ui.Container(discord.ui.TextDisplay(f"Panel {panel_index + 1}"), row))
    return view


def _rich_scene() -> scene.Scene[scene.ComponentsV2]:
    panels = tuple(
        scene.Panel(
            (
                scene.Text(f"Panel {panel_index + 1}"),
                scene.Row(
                    tuple(
                        scene.RoutedButton(
                            f"Button {panel_index * 5 + column + 1}",
                            f"rich:{panel_index}:{column}",
                        )
                        for column in range(5)
                    )
                ),
            )
        )
        for panel_index in range(5)
    )
    return scene.Scene(Codec.protocol, DISCORD_V2_DPY27.id, 1, scene.ComponentsV2(panels))


def _append_results(
    results: list[ConstructionResult],
    profile: str,
    controls: int,
    total_nodes: int,
    timings: dict[str, tuple[int, int]],
    samples: dict[str, int],
) -> None:
    baseline = timings["imperative"][1]
    for implementation, (p50, p95) in timings.items():
        results.append(
            ConstructionResult(
                profile,
                implementation,
                controls,
                total_nodes,
                p50 / 1_000_000,
                p95 / 1_000_000,
                p95 / baseline,
                samples[implementation],
            )
        )


def measure_comparison(
    *,
    sizes: tuple[int, ...] = (1, 5, 20, 30),
    samples: int = 1_000,
    pipeline_samples: int = 200,
) -> tuple[ConstructionResult, ...]:
    """Measure equivalent control construction and one rich 40-node V2 layout."""
    results: list[ConstructionResult] = []
    for controls in sizes:
        decorated = {alternate: _decorated_view_type(controls, alternate=alternate) for alternate in (False, True)}
        scenes = {alternate: _button_scene(controls, alternate=alternate) for alternate in (False, True)}
        renderer = V2Renderer(cache=RenderProgramCache())
        for value in scenes.values():
            renderer.view(value).stop()
        decorator_factory = _alternating(samples, partial(_decorated_instance, decorated))
        imperative_factory = _alternating(samples, partial(_imperative_view, controls))
        squid_factory = _alternating(samples, partial(_render_scene, renderer, scenes))

        expected = _normalized(_imperative_view(controls, alternate=False))
        decorated_view = decorated[False](timeout=None)
        squid_view = renderer.view(scenes[False])
        second_squid_view = renderer.view(scenes[False])
        assert _normalized(decorated_view) == expected
        assert _normalized(squid_view) == expected
        assert squid_view.total_children_count == controls + len(_rows(controls))
        assert squid_view is not second_squid_view
        assert squid_view.children[0] is not second_squid_view.children[0]
        decorated_view.stop()
        squid_view.stop()
        second_squid_view.stop()

        timings = {
            "decorators": _measure(decorator_factory, samples),
            "imperative": _measure(imperative_factory, samples),
            "squid_renderer": _measure(squid_factory, samples),
            "squid_pipeline": _measure_pipeline(controls, pipeline_samples),
        }
        _append_results(
            results,
            "buttons",
            controls,
            controls + len(_rows(controls)),
            timings,
            {
                "decorators": samples,
                "imperative": samples,
                "squid_renderer": samples,
                "squid_pipeline": pipeline_samples,
            },
        )
        assert renderer.cache.snapshot().hits >= samples
        assert renderer.cache.snapshot().certified == 2

    rich_scene = _rich_scene()
    rich_renderer = V2Renderer(cache=RenderProgramCache())
    rich_renderer.view(rich_scene).stop()
    rich_imperative = _rich_imperative_view()
    rich_squid = rich_renderer.view(rich_scene)
    assert rich_imperative.total_children_count == 40
    assert _normalized(rich_squid) == _normalized(rich_imperative)
    rich_imperative.stop()
    rich_squid.stop()
    _append_results(
        results,
        "rich_40_nodes",
        25,
        40,
        {
            "imperative": _measure(_rich_imperative_view, samples),
            "squid_renderer": _measure(lambda: rich_renderer.view(rich_scene), samples),
        },
        {"imperative": samples, "squid_renderer": samples},
    )
    return tuple(results)


def main() -> None:
    results = measure_comparison()
    print(
        json.dumps(
            {
                "environment": {
                    "python": sys.version.split()[0],
                    "platform": platform.platform(),
                    "discord_py": discord.__version__,
                },
                "results": [asdict(result) for result in results],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
