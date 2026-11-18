"""Dependency validation and setup ownership for managed extensions."""

import pytest

from squid.bot.extensions import ExtensionDependencyError, ExtensionManager, ExtensionSpec


def test_extension_graph_reports_missing_dependencies_and_cycles() -> None:
    recount = ExtensionSpec(id="recount", module="example.recount", requires=("votes",))
    missing = ExtensionSpec(id="votes", module="example.votes", requires=("reactions",))
    cyclic = ExtensionSpec(id="votes", module="example.votes", requires=("recount",))

    with pytest.raises(ExtensionDependencyError, match="recount -> votes -> reactions"):
        ExtensionManager((recount, missing))
    with pytest.raises(ExtensionDependencyError, match="recount -> votes -> recount"):
        ExtensionManager((recount, cyclic))


async def test_setup_gets_typed_exports_and_close_reverses_dependency_order() -> None:
    recount = ExtensionSpec(id="recount", module="example.recount", requires=("votes",))
    votes: ExtensionSpec[str] = ExtensionSpec(id="votes", module="example.votes")
    manager = ExtensionManager((recount, votes))
    events: list[tuple[str, str]] = []

    async def load(module: str) -> None:
        events.append(("load", module))
        if module == votes.module:
            ctx = manager.setup_context(votes)
            with pytest.raises(ExtensionDependencyError, match="does not require recount"):
                ctx.require(recount)
            ctx.publish("vote recount")
        else:
            assert manager.setup_context(recount).require(votes) == "vote recount"

    async def unload(module: str) -> None:
        events.append(("unload", module))

    await manager.start(load, unload)
    with pytest.raises(ExtensionDependencyError, match="no active setup"):
        manager.setup_context(recount)
    await manager.close(unload)
    assert events == [
        ("load", votes.module),
        ("load", recount.module),
        ("unload", recount.module),
        ("unload", votes.module),
    ]


async def test_failed_startup_rolls_back_loads_and_can_retry() -> None:
    recount = ExtensionSpec(id="recount", module="example.recount", requires=("votes",))
    votes: ExtensionSpec[str] = ExtensionSpec(id="votes", module="example.votes")
    manager = ExtensionManager((recount, votes))
    unloaded: list[str] = []

    async def load(module: str) -> None:
        if module == votes.module:
            manager.setup_context(votes).publish("vote recount")
        else:
            raise RuntimeError("recount setup failed")

    async def unload(module: str) -> None:
        unloaded.append(module)

    for _ in range(2):
        with pytest.raises(RuntimeError, match="recount setup failed"):
            await manager.start(load, unload)
        await manager.close(unload)

    assert unloaded == [votes.module, votes.module]
