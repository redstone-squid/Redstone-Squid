"""Collect every Discord screen the test suite renders into a browsable HTML gallery.

The tests already build each screen with realistic fixture data, so running them is the cheapest way to
see what users see. This pytest plugin records each Components V2 render, drops exact repeats, and
writes a gallery grouped by screen. Use it to review layout changes before and after an edit.

Enable it with ``just ui-gallery`` or ``pytest -p tests.support.ui_gallery --ui-gallery=DIR``. The
output directory contains:

- ``index.html``: every distinct render, grouped by screen, with the tests that produced it.
- ``index.json``: each render's screen, module, file name, and tests, for scripts and diffs.
- ``renders/``: one standalone HTML page per render, plus a PNG when ``--ui-gallery-screenshots`` is
  given and Chrome or Chromium is installed.

The preview shows Discord Markdown literally and does not draw images from the network, so judge
structure rather than typography.
"""

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from html import escape
from pathlib import Path
from typing import Any, cast

import pytest

from squid_ui import scene
from squid_ui.html import PREVIEW_CSS, DiscordPreviewRenderer
from squid_ui.planning.result import PlanResult
from squid_ui_discord.message_root import MessageRoot

_BROWSERS = ("google-chrome", "chromium", "chromium-browser", "google-chrome-stable")


@dataclass
class Render:
    """One distinct Components V2 scene produced by a screen."""

    screen: str
    module: str
    stem: str
    tests: list[str]
    preview: str = field(repr=False)
    error: str | None = None


class GalleryRecorder:
    """Record renders from every message root while installed.

    ``MessageRoot._draw`` is the one step every Components V2 render passes through, including the
    test helpers that commit a render without delivering it, so the recorder wraps that private
    method. ``tests/unit/test_ui_gallery.py`` fails if the hook stops seeing renders.
    """

    def __init__(self, *, module_prefix: str = "") -> None:
        self.module_prefix = module_prefix
        self.current_test = ""
        self.renders: dict[tuple[str, str], Render] = {}
        self._renderer = DiscordPreviewRenderer()

    def install(self) -> Callable[[], None]:
        """Start recording and return a function that stops it."""
        original = MessageRoot._draw
        recorder = self

        def _draw(root: MessageRoot, *args: Any, **kwargs: Any) -> Any:
            candidate = original(root, *args, **kwargs)
            recorder.record(type(root.component), candidate.plan)
            return candidate

        MessageRoot._draw = _draw  # type: ignore[method-assign]

        def uninstall() -> None:
            MessageRoot._draw = original  # type: ignore[method-assign]

        return uninstall

    def record(self, component: type, plan: PlanResult) -> None:
        """Keep one render, unless the same screen already produced an identical scene."""
        if not isinstance(plan.scene.body, scene.ComponentsV2) or not component.__module__.startswith(
            self.module_prefix
        ):
            return
        digest = hashlib.sha256(scene.Codec.dumps(plan.scene).encode()).hexdigest()[:12]
        key = (component.__qualname__, digest)
        if (existing := self.renders.get(key)) is not None:
            if self.current_test not in existing.tests:
                existing.tests.append(self.current_test)
            return

        # The body check above does not narrow the plan's type parameter.
        v2_plan = cast(PlanResult[scene.ComponentsV2], plan)
        try:
            preview, error = self._renderer.draw(v2_plan.scene, plan=v2_plan), None
        except Exception as caught:
            preview, error = "", repr(caught)
        name = component.__qualname__.replace("<locals>.", "")
        self.renders[key] = Render(
            screen=name,
            module=component.__module__,
            stem=f"{name}-{digest}",
            tests=[self.current_test],
            preview=preview,
            error=error,
        )

    def by_screen(self) -> list[tuple[str, list[Render]]]:
        """Group renders by screen, sorted by module path, keeping each screen's renders in test order."""
        groups: dict[str, list[Render]] = {}
        for render in self.renders.values():
            groups.setdefault(f"{render.module}.{render.screen}", []).append(render)
        return sorted(groups.items())

    def write(self, directory: Path, *, screenshots: bool = False) -> None:
        """Write the gallery, replacing an earlier one in the same directory."""
        renders_dir = directory / "renders"
        if renders_dir.exists():
            shutil.rmtree(renders_dir)
        renders_dir.mkdir(parents=True)
        groups = self.by_screen()
        for _, renders in groups:
            for render in renders:
                (renders_dir / f"{render.stem}.html").write_text(_standalone(render.preview), encoding="utf-8")
        (directory / "index.json").write_text(
            json.dumps(
                [{k: v for k, v in asdict(r).items() if k != "preview"} for _, rs in groups for r in rs], indent=1
            ),
            encoding="utf-8",
        )
        (directory / "index.html").write_text(_index(groups), encoding="utf-8")
        if screenshots:
            _screenshot_all(renders_dir)


def _standalone(preview: str) -> str:
    return f'<!doctype html><html><head><meta charset="utf-8"><style>{PREVIEW_CSS}</style></head><body>{preview}</body></html>'


_INDEX_CSS = """
body{margin:0;padding:24px;background:#1e1f22;color:#dbdee1;font:14px/1.4 system-ui,sans-serif}
h1{margin-top:0}a{color:#00a8fc}table{border-collapse:collapse;margin-bottom:32px}
td,th{padding:4px 12px;border-bottom:1px solid #3f4147;text-align:left}td.n{text-align:right}
.screen{margin:32px 0}.renders{display:flex;flex-wrap:wrap;gap:24px;align-items:flex-start}
.render{width:560px}.meta{margin:6px 0;color:#949ba4;font-size:12px}.error{color:#f23f43}
details summary{cursor:pointer;color:#949ba4;font-size:12px}
"""


def _index(groups: list[tuple[str, list[Render]]]) -> str:
    rows = "".join(
        f'<tr><td><a href="#{escape(name)}">{escape(renders[0].screen)}</a></td><td>{escape(renders[0].module)}</td>'
        f'<td class="n">{len(renders)}</td></tr>'
        for name, renders in groups
    )
    sections = "".join(_screen_section(name, renders) for name, renders in groups)
    return (
        f'<!doctype html><html><head><meta charset="utf-8"><title>UI gallery</title>'
        f"<style>{PREVIEW_CSS}{_INDEX_CSS}</style></head><body><h1>UI gallery</h1>"
        "<p>Every distinct Components V2 render produced by the selected tests.</p>"
        "<table><tr><th>Screen</th><th>Module</th><th>Renders</th></tr>"
        f"{rows}</table>{sections}</body></html>"
    )


def _screen_section(name: str, renders: list[Render]) -> str:
    cards = "".join(
        f'<div class="render"><div class="meta"><a href="renders/{escape(r.stem)}.html">open</a></div>'
        + (f'<div class="error">{escape(r.error)}</div>' if r.error else r.preview)
        + "<details><summary>"
        + f"{len(r.tests)} test{'s' if len(r.tests) != 1 else ''}</summary>"
        + "".join(f"<div>{escape(test)}</div>" for test in r.tests)
        + "</details></div>"
        for r in renders
    )
    return (
        f'<section class="screen" id="{escape(name)}"><h2>{escape(renders[0].screen)}</h2>'
        f'<div class="meta">{escape(name)}</div><div class="renders">{cards}</div></section>'
    )


def _screenshot_all(directory: Path) -> None:
    browser = os.environ.get("UI_GALLERY_BROWSER") or next(filter(None, map(shutil.which, _BROWSERS)), None)
    if browser is None:
        message = "--ui-gallery-screenshots needs Chrome or Chromium on PATH, or UI_GALLERY_BROWSER"
        raise pytest.UsageError(message)

    def capture(page: Path) -> None:
        # Discord messages are at most about 560px wide. Headless Chrome cannot size the window to
        # its content, so the height is generous and the PNG has empty space below short messages.
        subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--window-size=620,1400",
                f"--screenshot={page.with_suffix('.png')}",
                page.resolve().as_uri(),
            ],
            check=True,
            capture_output=True,
        )

    # Each capture starts a browser process, so run them side by side.
    with ThreadPoolExecutor() as pool:
        list(pool.map(capture, sorted(directory.glob("*.html"))))


_recorder_key = pytest.StashKey[tuple[GalleryRecorder, Callable[[], None]]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("ui-gallery")
    group.addoption("--ui-gallery", metavar="DIR", help="write an HTML gallery of rendered Discord screens to DIR")
    group.addoption(
        "--ui-gallery-module",
        default="squid.",
        metavar="PREFIX",
        help="only record components whose module starts with PREFIX (default: squid.)",
    )
    group.addoption(
        "--ui-gallery-screenshots", action="store_true", help="also save a PNG of each render with headless Chrome"
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--ui-gallery") is None:
        return
    recorder = GalleryRecorder(module_prefix=config.getoption("--ui-gallery-module"))
    config.stash[_recorder_key] = (recorder, recorder.install())


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    if (installed := item.config.stash.get(_recorder_key, None)) is not None:
        installed[0].current_test = item.nodeid


def pytest_sessionfinish(session: pytest.Session) -> None:
    config = session.config
    if (installed := config.stash.get(_recorder_key, None)) is None:
        return
    recorder, uninstall = installed
    uninstall()
    recorder.write(Path(config.getoption("--ui-gallery")), screenshots=config.getoption("--ui-gallery-screenshots"))


def pytest_terminal_summary(terminalreporter: Any, config: pytest.Config) -> None:
    if (installed := config.stash.get(_recorder_key, None)) is not None:
        index = Path(config.getoption("--ui-gallery")) / "index.html"
        terminalreporter.write_line(f"UI gallery: {len(installed[0].renders)} distinct renders in {index}")
