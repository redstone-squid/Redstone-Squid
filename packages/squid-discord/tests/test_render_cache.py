import discord

import squid_discord.renderer as renderer_module
from squid_discord import DISCORD_V2_DPY27
from squid_discord.render_cache import RenderProgramCache
from squid_discord.renderer import V2Renderer
from squid_layouts.document import Document
from squid_layouts.planning.planner import plan
from squid_layouts.primitives import Text


def _text_plan(value: str):
    return plan(Document((Text(value),)), target=DISCORD_V2_DPY27)  # pyrefly: ignore[bad-argument-type]


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
