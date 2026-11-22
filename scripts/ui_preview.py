"""Serve an interactive local preview of a staged Discord screen."""

import asyncio
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import cast, override

import anyio
from aiohttp import web
from anyio.abc import TaskStatus
from bs4 import BeautifulSoup

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.showcase.catalogue import CataloguePreview
from squid.bot.showcase.review import Reviewer, ReviewerPanel, ReviewRoom
from squid_ui.ext.discord import scene
from squid_ui.ext.discord.preview import PREVIEW_CSS, DiscordPreviewRenderer
from squid_ui.ext.discord.testing import StagedUI, stage
from squid_ui.planning.result import PlanResult


class Counter(sd.Screen):
    count: int = sl.state(0)

    @override
    def render(self) -> sl.LayoutNode:
        return sl.section(
            sl.heading("Counter"),
            sl.paragraph(str(self.count)),
            sl.action_controls(sl.action_control("Add", self.add, key="add"), key="counter-actions"),
        )

    async def add(self, _event: sl.PressEvent) -> None:
        self.count += 1


def reviewer_panel() -> ReviewerPanel:
    room = ReviewRoom()
    alice = Reviewer(1, "Alice")
    room.seats = (alice, Reviewer(2, "Bob"))
    return ReviewerPanel(room, alice, simulated=True)


SCREENS: dict[str, Callable[[], sd.Screen]] = {
    "counter": Counter,
    "catalogue": CataloguePreview,
    "review": reviewer_panel,
}


class PreviewSession:
    def __init__(self, ui: StagedUI[sd.Screen]) -> None:
        self.ui = ui
        self.lock = asyncio.Lock()
        self.last_access = anyio.current_time()

    def display(self) -> tuple[str, set[str]]:
        plan = self.ui.root.plan
        if self.ui.root.finished or plan is None:
            raise web.HTTPGone(text="Preview ended. Reload to start again.")
        if not isinstance(plan.scene.body, scene.ComponentsV2):
            msg = "Preview requires a Components V2 screen"
            raise TypeError(msg)
        v2 = cast(PlanResult[scene.ComponentsV2], plan)
        markup = DiscordPreviewRenderer().draw(v2.scene, plan=v2)
        fragment = BeautifulSoup(markup, "html.parser")
        actions = set()
        for control in fragment.select("button, select"):
            key = control.get("data-squid-action")
            if (
                control.name == "button"
                and isinstance(key, str)
                and key in plan.bindings
                and key not in plan.form_bindings
                and not control.has_attr("disabled")
            ):
                actions.add(key)
            else:
                control["disabled"] = ""
        return str(fragment), actions


@asynccontextmanager
async def preview_app(
    factory: Callable[[], sd.Screen] | None = None, *, idle_seconds: float = 600
) -> AsyncIterator[web.Application]:
    screens = SCREENS if factory is None else {"custom": factory}
    sessions: dict[str, PreviewSession] = {}
    closing = anyio.Event()
    script = Path(__file__).with_suffix(".js").read_text()

    async def own_session(
        token: str, factory: Callable[[], sd.Screen], *, task_status: TaskStatus[PreviewSession]
    ) -> None:
        async with stage(factory(), owner=object()) as ui:
            session = sessions[token] = PreviewSession(ui)
            try:
                task_status.started(session)
                while not closing.is_set():
                    async with session.lock:
                        remaining = session.last_access + idle_seconds - anyio.current_time()
                        if remaining <= 0:
                            break
                    with anyio.move_on_after(remaining):
                        await closing.wait()
            finally:
                del sessions[token]

    async with anyio.create_task_group() as group:

        async def index(request: web.Request) -> web.Response:
            screen = request.query.get("screen", next(iter(screens)))
            if screen not in screens:
                raise web.HTTPNotFound(text="Unknown preview screen.")
            links = " · ".join(
                f'<a href="/?screen={name}">{name.title()}</a>' if name != screen else name.title() for name in screens
            )
            token = secrets.token_urlsafe(24)
            session = await group.start(own_session, token, screens[screen])
            fragment, _ = session.display()
            page = (
                '<!doctype html><meta charset="utf-8"><title>Discord preview</title>'
                f"<style>{PREVIEW_CSS} nav {{font: 1rem system-ui; padding: .75rem 0}}</style>"
                f'<nav aria-label="Preview screens">{links}</nav><main data-session="{token}" '
                f'data-generation="{session.ui.root.generation}">{fragment}</main>'
                '<p role="status"></p><script src="/preview.js"></script>'
            )
            return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-store"})

        async def action(request: web.Request) -> web.Response:
            token = request.match_info["token"]
            session = sessions.get(token)
            if session is None:
                raise web.HTTPGone(text="Preview ended. Reload to start again.")
            if request.content_type != "application/json":
                raise web.HTTPUnsupportedMediaType()
            try:
                data = await request.json()
            except (ValueError, UnicodeError) as error:
                raise web.HTTPBadRequest() from error
            if (
                not isinstance(data, dict)
                or type(data.get("generation")) is not int
                or not isinstance(data.get("action"), str)
            ):
                raise web.HTTPBadRequest()
            async with session.lock:
                if token not in sessions:
                    raise web.HTTPGone(text="Preview ended. Reload to start again.")
                session.last_access = anyio.current_time()
                fragment, actions = session.display()
                if data["generation"] == session.ui.root.generation:
                    if data["action"] not in actions:
                        raise web.HTTPBadRequest(text="This action is unavailable.")
                    await session.ui.press(data["action"])
                    fragment, _ = session.display()
                return web.Response(
                    text=fragment, content_type="text/html", headers={"X-Generation": str(session.ui.root.generation)}
                )

        async def javascript(request: web.Request) -> web.Response:
            return web.Response(text=script, content_type="application/javascript")

        app = web.Application()
        app.add_routes(
            [web.get("/", index), web.get("/preview.js", javascript), web.post("/preview/{token}/actions", action)]
        )
        try:
            yield app
        finally:
            closing.set()


async def main() -> None:
    async with preview_app() as app:
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            await web.TCPSite(runner, "127.0.0.1", 8000).start()
            print("Discord preview at http://127.0.0.1:8000", flush=True)
            await anyio.sleep_forever()
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        anyio.run(main)
