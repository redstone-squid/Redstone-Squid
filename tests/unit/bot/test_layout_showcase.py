"""Public dogfood surface for the squid-layouts engine."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands

from squid.bot.layout_showcase import LayoutShowcase, LayoutShowcaseCog
from squid_layouts.discord import Mount
from squid_layouts.discord.testing import assert_within_limits, commit_render, fake_interaction


def _buttons(view: discord.ui.LayoutView) -> list[discord.ui.Button[Any]]:
    return [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]


def _texts(view: discord.ui.LayoutView) -> str:
    return "\n".join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))


def test_pagination_exhibit_uses_the_measured_budget() -> None:
    mount = Mount(LayoutShowcase(section="pagination", entries=200, locale="en"), timeout=None)
    view = commit_render(mount)

    assert "#011" in _texts(view)
    assert "#200" not in _texts(view)
    assert any(button.label == "Next" for button in _buttons(view))
    assert_within_limits(view)


def test_structural_exhibit_folds_the_oversized_action_surface() -> None:
    view = commit_render(Mount(LayoutShowcase(section="adaptation", entries=20, locale="en"), timeout=None))

    selects = [item for item in view.walk_children() if isinstance(item, discord.ui.Select)]
    assert [
        len(select.options) for select in selects if select.custom_id and "showcase-actions" in select.custom_id
    ] == [
        25,
        11,
    ]
    assert not any(button.label == "Action 36" for button in _buttons(view))
    assert_within_limits(view)


@pytest.mark.parametrize(
    ("section", "source_marker"),
    [
        ("tour", "class Counter(sl.Component)"),
        ("pagination", "return sl.List("),
        ("adaptation", 'return sl.Actions(actions, key="showcase-actions")'),
        ("composition", 'self.embed(self.left, key="left")'),
    ],
)
def test_each_exhibit_shows_its_author_facing_declaration(section: str, source_marker: str) -> None:
    view = commit_render(Mount(LayoutShowcase(section=section, entries=20, locale="en"), timeout=None))  # type: ignore[arg-type]
    content = _texts(view)

    assert "Declaration source" in content
    assert source_marker in content
    assert_within_limits(view)


async def test_composed_children_keep_independent_state_and_keys() -> None:
    component = LayoutShowcase(section="composition", entries=20, locale="en")
    mount = Mount(component, timeout=None)
    view = commit_render(mount)
    ids = {button.custom_id or "" for button in _buttons(view)}

    assert any("left.increment" in custom_id for custom_id in ids)
    assert any("right.increment" in custom_id for custom_id in ids)

    await mount.dispatch("left.increment", fake_interaction())

    assert component.left.count == 1
    assert component.right.count == 0


async def test_demo_command_and_controls_are_public() -> None:
    settings = SimpleNamespace(get_locale=AsyncMock(return_value=None))
    cog = LayoutShowcaseCog(cast(Any, SimpleNamespace(services=SimpleNamespace(settings=settings))))
    ctx = cast(
        commands.Context[Any],
        cast(
            Any,
            SimpleNamespace(
                interaction=None,
                guild=None,
                author=SimpleNamespace(id=7),
                send=AsyncMock(return_value=SimpleNamespace(id=1, flags=SimpleNamespace(components_v2=True))),
            ),
        ),
    )

    await LayoutShowcaseCog.demo.callback(cog, ctx, "tour", 20)  # type: ignore[arg-type]

    sent = cast(Any, ctx).send.await_args.kwargs
    assert sent["ephemeral"] is False
    assert isinstance(sent["view"], discord.ui.LayoutView)
    demo = next(command for command in cog.__cog_commands__ if command.qualified_name == "layout demo")
    assert demo.checks == []
