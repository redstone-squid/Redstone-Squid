"""Resource-backed domain entity lookup."""

from dataclasses import dataclass

import anyio
import discord

import squid_ui as sl
import squid_ui_widgets as sp
from squid_discord import Everyone, Mount
from squid_discord.testing import commit_render, fake_interaction


@dataclass(frozen=True, slots=True)
class Entry:
    key: str
    label: str


def _lookup(
    items: tuple[Entry, ...],
    commits: list[tuple[Entry, ...]],
    *,
    picked: tuple[Entry, ...] = (),
    minimum: int = 0,
    maximum: int = 2,
) -> sp.Lookup[Entry]:
    async def committed(_event: sl.ActionEvent, values: tuple[Entry, ...]) -> None:
        commits.append(values)

    return sp.Lookup(
        lambda query: sl.sources.list_source(tuple(item for item in items if query.lower() in item.label.lower())),
        identity=lambda item: item.key,
        label=lambda item: item.label,
        description=lambda item: f"ID {item.key}",
        picked=picked,
        minimum=minimum,
        maximum=maximum,
        on_pick=committed,
        page_size=2,
    )


async def _search(mount: Mount, lookup: sp.Lookup[Entry], query: str) -> None:
    spec = sl.forms.FormSpec("Search", (sl.forms.TextField(key="query", label="Search"),))
    await mount.dispatch_submit(
        "lookup.search",
        fake_interaction(),
        spec,
        {"query": query},
        lookup._searched,
    )


async def test_lookup_searches_picks_resolved_items_and_removes_them() -> None:
    commits: list[tuple[Entry, ...]] = []
    lookup = _lookup((Entry("a", "Alpha"), Entry("b", "Beta")), commits)
    mount = Mount(lookup, access=Everyone(), timeout=None)

    await _search(mount, lookup, "a")

    assert isinstance(lookup.results.status, sl.resources.Ready)
    commit_render(mount)
    await mount.dispatch("lookup.results.a", fake_interaction())
    assert lookup.picked == (Entry("a", "Alpha"),)
    assert commits == [(Entry("a", "Alpha"),)]

    commit_render(mount)
    await mount.dispatch("lookup.remove.a", fake_interaction())
    assert lookup.picked == ()
    assert commits[-1] == ()


async def test_single_lookup_replaces_and_minimum_gates_removal() -> None:
    commits: list[tuple[Entry, ...]] = []
    first = Entry("a", "Alpha")
    lookup = _lookup((first, Entry("b", "Beta")), commits, picked=(first,), minimum=1, maximum=1)
    mount = Mount(lookup, access=Everyone(), timeout=None)

    await _search(mount, lookup, "Beta")
    commit_render(mount)
    await mount.dispatch("lookup.results", fake_interaction(), ["b"])

    assert lookup.picked == (Entry("b", "Beta"),)
    assert commits == [(Entry("b", "Beta"),)]
    rendered = commit_render(mount)
    remove = next(
        child for child in rendered.walk_children() if isinstance(child, discord.ui.Button) and child.label == "Remove"
    )
    assert remove.disabled


async def test_lookup_pages_with_the_query_source_and_renders_no_results() -> None:
    commits: list[tuple[Entry, ...]] = []
    lookup = _lookup((Entry("a", "Alpha"), Entry("b", "Beta"), Entry("c", "Gamma")), commits)
    mount = Mount(lookup, access=Everyone(), timeout=None)

    await _search(mount, lookup, "")
    assert lookup.query is None

    await _search(mount, lookup, "a")
    commit_render(mount)
    await mount.dispatch("lookup.next", fake_interaction())
    assert isinstance(lookup.results.status, sl.resources.Ready)
    assert lookup.results.value.loaded.window.items == (Entry("c", "Gamma"),)

    await _search(mount, lookup, "missing")
    assert "No results" in str(lookup.render())


async def test_lookup_drops_a_stale_query_completion() -> None:
    entered = {query: anyio.Event() for query in ("old", "new")}
    release = {query: anyio.Event() for query in ("old", "new")}
    new_settled = anyio.Event()

    class DelayedSource:
        capabilities = sl.sources.SourceCapabilities(
            backward=True,
            offsets=True,
            jumpable=True,
            count=sl.sources.CountPrecision.EXACT,
        )

        def __init__(self, query: str) -> None:
            self.query = query

        async def fetch(self, position: sl.sources.Position, _extent: int) -> sl.sources.Window[Entry]:
            entered[self.query].set()
            await release[self.query].wait()
            return sl.sources.Window(
                position,
                (Entry(self.query, self.query),),
                has_previous=False,
                has_next=False,
                total=1,
            )

    async def committed(_event: sl.ActionEvent, _picked: tuple[Entry, ...]) -> None:
        pass

    lookup = sp.Lookup(
        DelayedSource,
        identity=lambda item: item.key,
        label=lambda item: item.label,
        on_pick=committed,
    )

    async def settle_new() -> None:
        await lookup.results._load()
        new_settled.set()

    lookup.query = "old"
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(lookup.results._load)
        await entered["old"].wait()
        lookup.query = "new"
        tasks.start_soon(settle_new)
        await entered["new"].wait()
        release["new"].set()
        await new_settled.wait()
        release["old"].set()

    assert isinstance(lookup.results.status, sl.resources.Ready)
    assert lookup.results.value.query == "new"
    assert lookup.results.value.loaded.window.items == (Entry("new", "new"),)
