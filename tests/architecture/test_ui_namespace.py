"""Keep extension discovery working across separately installed source trees."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("first", ["squid_ui", "squid_ui.ext.discord", "squid_ui.ext.slack", "squid_ui.ext.html"])
def test_ui_packages_share_the_namespace_in_any_import_order(first: str, tmp_path: Path) -> None:
    script = f"""
import importlib
importlib.import_module({first!r})
import squid_ui
from squid_ui.ext import discord, slack, html
assert squid_ui.Document is importlib.import_module('squid_ui.authoring.document').Document
assert squid_ui.ext.__spec__.origin is None
for module in (discord, slack, html):
    assert module is importlib.import_module(module.__name__)
    assert module.__file__ is not None
"""
    subprocess.run([sys.executable, "-I", "-c", script], cwd=tmp_path, check=True)


def test_ui_discovers_extensions_in_a_separate_source_tree(tmp_path: Path) -> None:
    extension = tmp_path / "squid_ui" / "ext" / "namespace_probe"
    extension.mkdir(parents=True)
    (extension / "__init__.py").write_text("value = 42\n", encoding="utf-8")
    script = f"""
import sys
sys.path.insert(0, {str(tmp_path)!r})
import squid_ui
assert squid_ui.Document
assert 'squid_ui.ext' not in sys.modules
from squid_ui.ext import namespace_probe
assert namespace_probe.value == 42
assert squid_ui.ext.__spec__.origin is None
"""
    subprocess.run([sys.executable, "-I", "-c", script], cwd=tmp_path, check=True)
