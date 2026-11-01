"""Hosts accept portable and matching contexts, and reject unrelated ones."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_SOURCE = """\
import squid_ui as sl
import squid_ui_discord as sd

class HtmlContext:
    pass

async def check(
    wrong: sl.Component[sl.ComponentsV2Target, HtmlContext],
    wrong_classic: sl.Component[sl.ClassicTarget, HtmlContext],
    wrong_node: sl.LayoutNode[sl.ComponentsV2Target, HtmlContext],
    wrong_classic_node: sl.LayoutNode[sl.ClassicTarget, HtmlContext],
    portable: sl.Component[sl.ComponentsV2Target],
    matching: sl.Component[sl.ComponentsV2Target, sd.Request],
    request: sd.Request,
    runtime: sd.DiscordUIRuntime,
) -> None:
    sd.MessageRoot(portable, access=sd.Everyone())
    sd.MessageRoot(matching, access=sd.Everyone())
    sd.render_static(portable)
    sd.render_static(matching)
    runtime.mount(portable, access=sd.Everyone())
    runtime.mount(matching, access=sd.Everyone())
    await request.respond(matching)
    sd.MessageRoot(wrong, access=sd.Everyone())  # reject
    sd.MessageRoot(wrong, access=sd.Everyone(), target=sd.DISCORD_V2_DPY27)  # reject
    sd.MessageRoot(wrong, access=sd.Everyone(), config=sd.MessageRootConfig())  # reject
    sd.MessageRoot(wrong_classic, access=sd.Everyone(), target=sd.DISCORD_V1_DPY27)  # reject
    sd.owner_message_root(wrong, 1)  # reject
    sd.MessageRootDefaults().mount(wrong, access=sd.Everyone())  # reject
    runtime.mount(wrong, access=sd.Everyone())  # reject
    sd.render_message(wrong_node)  # reject
    sd.render_static(wrong)  # reject
    sd.render_item(wrong_node)  # reject
    sd.classic.render_message(wrong_classic_node)  # reject
    sd.classic.render_static(wrong_classic)  # reject
    await request.respond(wrong)  # reject
    navigator = sd.StackNavigator(matching)
    navigator.push(portable)
    navigator.push(wrong)  # reject
    sd.MessageRoot(sd.StackNavigator(wrong), access=sd.Everyone())  # reject
"""


def test_host_contexts_are_checked_before_dispatch(tmp_path: Path) -> None:
    checker = shutil.which("pyrefly")
    if checker is None:
        pytest.skip("requires the development type checker")
    source = tmp_path / "host_contexts.py"
    source.write_text(_SOURCE)
    config = Path(__file__).resolve().parents[3] / "pyproject.toml"
    result = subprocess.run(
        [checker, "check", "--config", str(config), "--output-format=min-text", str(source)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = result.stdout + result.stderr
    expected = {index for index, line in enumerate(_SOURCE.splitlines(), 1) if "# reject" in line}
    actual = {int(line) for line in re.findall(r"host_contexts\.py:(\d+):", output)}
    assert result.returncode == 1, output
    assert actual == expected, output
