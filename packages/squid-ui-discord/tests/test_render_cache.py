import gc
import weakref

import discord

import squid_ui as sl
import squid_ui_discord.classic_renderer as classic_renderer_module
import squid_ui_discord.renderer as renderer_module
from squid_ui import Component, state
from squid_ui.document import Document
from squid_ui.interactions import ActionBinding
from squid_ui.planning.planner import plan
from squid_ui.primitives import Text
from squid_ui.scene import ClassicMessage, Codec, Embed
from squid_ui.scene import Scene as RenderedScene
from squid_ui.semantic import ActionControl, ActionControls
from squid_ui_discord import DISCORD_V1_DPY27, DISCORD_V2_DPY27, Everyone, MessageRoot
from squid_ui_discord.classic_renderer import ClassicRenderer
from squid_ui_discord.render_cache import RenderProgramCache
from squid_ui_discord.renderer import StaticView, V2Renderer
from squid_ui_discord.testing import delivered_to, fake_message


def _text_plan(value: str):
    return plan(Document((Text(value),)), target=DISCORD_V2_DPY27)  # pyrefly: ignore[bad-argument-type]


def _classic_document(value: str) -> RenderedScene[ClassicMessage]:
    return RenderedScene(
        protocol=Codec.protocol,
        target=DISCORD_V1_DPY27.id,
        target_version=1,
        body=ClassicMessage(embeds=(Embed(title=value),)),
    )


def test_v2_program_hits_return_fresh_discord_objects_and_skip_certified_audit(monkeypatch) -> None:
    cache = RenderProgramCache()
    renderer = V2Renderer(cache=cache)
    result = _text_plan("hello")
    audits = 0
    conform = renderer_module.conform

    def counted(*args, **kwargs):
        nonlocal audits
        audits += 1
        return conform(*args, **kwargs)

    monkeypatch.setattr(renderer_module, "conform", counted)

    first = renderer.draw(result.scene, plan=result)
    second = renderer.draw(result.scene, plan=result)

    assert isinstance(first.view, discord.ui.LayoutView)
    assert isinstance(second.view, discord.ui.LayoutView)
    assert first.view is not second.view
    assert first.view.children[0] is not second.view.children[0]
    assert audits == 1
    assert cache.snapshot().hits == 1
    assert cache.snapshot().misses == 1
    assert cache.snapshot().certified == 1


def test_v2_program_cache_is_bounded_and_eviction_only_changes_work() -> None:
    cache = RenderProgramCache(1)
    renderer = V2Renderer(cache=cache)
    first = _text_plan("first")
    second = _text_plan("second")

    renderer.draw(first.scene, plan=first)
    renderer.draw(second.scene, plan=second)
    revisited = renderer.draw(first.scene, plan=first)

    assert isinstance(revisited.view, discord.ui.LayoutView)
    text = revisited.view.children[0]
    assert isinstance(text, discord.ui.TextDisplay)
    assert text.content == "first"
    assert cache.snapshot().entries == 1
    assert cache.snapshot().misses == 3
    assert cache.snapshot().evictions == 2


def test_custom_v2_factory_keeps_final_audits_on_program_hits(monkeypatch) -> None:
    cache = RenderProgramCache()
    renderer = V2Renderer(cache=cache, view_factory=lambda: StaticView())
    result = _text_plan("custom")
    audits = 0
    conform = renderer_module.conform

    def counted(*args, **kwargs):
        nonlocal audits
        audits += 1
        return conform(*args, **kwargs)

    monkeypatch.setattr(renderer_module, "conform", counted)

    renderer.draw(result.scene, plan=result)
    renderer.draw(result.scene, plan=result)

    assert audits == 2
    assert cache.snapshot().hits == 1
    assert cache.snapshot().certified == 0


def test_shared_render_program_does_not_retain_authored_callbacks() -> None:
    class Handler:
        async def run(self, _event) -> None:
            pass

    owner = Handler()
    retained = weakref.ref(owner)
    document = ActionControls((ActionControl("run", "Run", owner.run),), key="tools")
    result = plan(document, target=DISCORD_V2_DPY27)  # pyrefly: ignore[bad-argument-type]
    cache = RenderProgramCache()

    def wire(node, _binding: ActionBinding) -> discord.ui.Item:
        return discord.ui.Button(label=node.label, custom_id="render-cache-test")

    presentation = V2Renderer(cache=cache, audit=False).draw(result.scene, plan=result, wire=wire)
    assert len(cache) == 1

    del presentation, result, document, owner
    gc.collect()

    assert retained() is None


def test_classic_program_hits_return_fresh_objects_and_skip_certified_audit(monkeypatch) -> None:
    cache = RenderProgramCache()
    renderer = ClassicRenderer(always_view=True, cache=cache)
    document = _classic_document("hello")
    audits = 0
    audit = classic_renderer_module.audit_classic_payload

    def counted(*args, **kwargs):
        nonlocal audits
        audits += 1
        return audit(*args, **kwargs)

    monkeypatch.setattr(classic_renderer_module, "audit_classic_payload", counted)

    first = renderer.draw(document)
    second = renderer.draw(document)

    assert isinstance(first.view, discord.ui.View)
    assert isinstance(second.view, discord.ui.View)
    assert first.view is not second.view
    assert first.embeds[0] is not second.embeds[0]
    assert audits == 1
    assert cache.snapshot().hits == 1
    assert cache.snapshot().misses == 1
    assert cache.snapshot().certified == 1


async def test_message_root_reuses_a_revisited_scene_program() -> None:
    class Switching(Component[sl.ComponentsV2Target]):
        value: str = state("first")

        def render(self) -> Text:
            return Text(self.value)

    component = Switching()
    message_root = MessageRoot(component, access=Everyone(), timeout=None)
    await message_root.send(delivered_to(fake_message()))
    component.value = "second"
    await message_root.refresh()
    component.value = "first"
    await message_root.refresh()

    snapshot = message_root.snapshot().render_cache
    assert snapshot.hits == 1
    assert snapshot.misses == 2
    await message_root.finish(disable=False)
    assert len(message_root.render_cache) == 0


async def test_explicit_render_cache_shares_programs_without_sharing_frontend_objects() -> None:
    class Static(Component[sl.ComponentsV2Target]):
        def render(self) -> Text:
            return Text("shared")

    cache = RenderProgramCache()
    first = MessageRoot(Static(), access=Everyone(), timeout=None, render_cache=cache)
    second = MessageRoot(Static(), access=Everyone(), timeout=None, render_cache=cache)

    await first.send(delivered_to(fake_message()))
    await second.send(delivered_to(fake_message()))

    assert first._view is not second._view
    assert cache.snapshot().hits == 1
    assert cache.snapshot().misses == 1
    await first.finish(disable=False)
    assert len(cache) == 1


def test_100_component_unchanged_hot_path_meets_latency_budget() -> None:
    from benchmarks.plan72_render_caching import measure_case

    result = measure_case(100, cold_samples=10, unchanged_samples=50)

    assert result.unchanged_p95_ms <= 2
    assert result.unchanged_fraction <= 0.25


async def test_representative_change_benchmarks_exercise_their_expected_paths() -> None:
    from benchmarks.plan72_render_caching import measure_change_scenarios

    results = await measure_change_scenarios(20, samples=2)

    assert [result.name for result in results] == [
        "1_leaf_change",
        "10_leaf_change",
        "conditional_branch_swap",
        "planner_text_limit_crossing",
        "atomic_resource_resolution",
        "subtree_mount_unmount",
    ]
    assert all(result.components == 20 for result in results)
    assert all(result.samples == 2 for result in results)
    assert all(result.p50_ms >= 0 and result.p95_ms >= result.p50_ms for result in results)


async def test_atomic_resource_pipeline_benchmark_reports_separate_phase_evidence() -> None:
    from benchmarks.plan72_render_caching import measure_resource_pipeline

    result = await measure_resource_pipeline(20, samples=2)

    assert result.render_passes_per_operation == 1
    assert result.leaf_renders_per_operation == 1
    assert result.loads_per_operation == 1
    assert not result.scheduler_included
    assert result.planner_reuse == "incremental"
    assert result.planner_states_explored == 1
    phases = {phase.name: phase for phase in result.phases}
    assert phases.keys() >= {"runtime_render", "resource_settle.atomic", "preflight", "planner", "renderer", "commit"}
    assert phases["runtime_render"].calls_per_operation == 1
    assert all(phase.p50_ms >= 0 and phase.p95_ms >= phase.p50_ms for phase in phases.values())


def test_discordpy_comparison_benchmarks_equivalent_fresh_layouts() -> None:
    from benchmarks.plan72_discordpy_comparison import measure_comparison

    results = measure_comparison(sizes=(1, 5), samples=2, pipeline_samples=2)

    assert {(result.profile, result.implementation) for result in results} == {
        ("buttons", "decorators"),
        ("buttons", "imperative"),
        ("buttons", "squid_renderer"),
        ("buttons", "squid_pipeline"),
        ("rich_40_nodes", "imperative"),
        ("rich_40_nodes", "squid_renderer"),
    }
    assert all(result.samples == 2 for result in results)
    assert all(result.p50_ms >= 0 and result.p95_ms >= result.p50_ms for result in results)
    assert all(result.ratio_to_imperative > 0 for result in results)
