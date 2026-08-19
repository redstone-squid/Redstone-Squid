"""Logical planning and mechanical Discord drawing."""

import discord
import pytest

from squid_layouts import (
    Button,
    LayoutInvariantError,
    Panel,
    Row,
    SceneCodec,
    Text,
    plan,
)
from squid_layouts.discord import DISCORD_V2, DiscordRenderer


async def _click(event) -> None: ...


def test_planner_extracts_callbacks_from_the_serializable_scene() -> None:
    result = plan(
        Panel((Text("hello"), Row((Button(label="Act", on_click=_click, key="act"),)))),
        target=DISCORD_V2,
    )

    assert result.bindings["act"].handler is _click
    encoded = SceneCodec.dumps(result.scene)
    assert "_click" not in encoded
    assert '"action":"act"' in encoded


def test_duplicate_action_keys_fail_before_drawing() -> None:
    with pytest.raises(LayoutInvariantError, match="duplicate action key"):
        plan(
            Row(
                (
                    Button(label="One", on_click=_click, key="same"),
                    Button(label="Two", on_click=_click, key="same"),
                )
            ),
            target=DISCORD_V2,
        )


def test_static_discord_renderer_matches_scene_structure() -> None:
    result = plan(Panel((Text("hello"),)), target=DISCORD_V2)
    view = DiscordRenderer().draw(result.scene, plan=result)

    assert isinstance(view, discord.ui.LayoutView)
    assert view.to_components()[0]["type"] == 17
