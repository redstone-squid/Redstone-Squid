"""Resource replacements participate in the action's staged writes.

The action reads its replacement immediately; other contexts see the committed value.
Rollback drops the replacement, and commit updates its dependency baseline.
"""

from typing import override

import pytest

import squid_ui as sl


class Panel(sl.Component):
    @sl.resource
    async def value(self) -> str:
        return "loaded"

    @override
    def render(self):
        return sl.paragraph("x")


async def loaded() -> Panel:
    panel = Panel()
    await panel.value.reload()
    assert panel.value.value == "loaded"
    return panel


async def test_a_rolled_back_action_does_not_keep_its_replacement() -> None:
    panel = await loaded()

    with pytest.raises(RuntimeError), sl.runtime.transaction():
        panel.value.replace("edited")
        message = "handler failed"
        raise RuntimeError(message)

    assert panel.value.value == "loaded"


async def test_an_action_reads_back_the_value_it_replaced() -> None:
    panel = await loaded()

    with sl.runtime.transaction():
        panel.value.replace("edited")
        assert panel.value.value == "edited"

    assert panel.value.value == "edited"


async def test_a_replacement_is_invisible_until_the_action_commits() -> None:
    """The dirty-read half: a concurrent render must not see a value that may yet vanish."""
    panel = await loaded()
    seen: list[str] = []

    with sl.runtime.transaction():
        panel.value.replace("edited")
        seen.append(_outside(panel))

    assert seen == ["loaded"], "another task's read sees the committed value, not the staged one"
    assert panel.value.value == "edited"


def _outside(panel: Panel) -> str:
    """Read in a copied context with only the transaction cleared, simulating an unrelated reader."""
    import contextvars

    from squid_reactivity.internals import CURRENT_TRANSACTION

    context = contextvars.copy_context()
    context.run(CURRENT_TRANSACTION.set, None)
    return context.run(lambda: panel.value.value)


async def test_the_last_replacement_in_an_action_is_the_one_that_lands() -> None:
    panel = await loaded()

    with sl.runtime.transaction():
        panel.value.replace("first")
        panel.value.replace("second")

    assert panel.value.value == "second"


async def test_replacing_outside_an_action_still_lands_immediately() -> None:
    panel = await loaded()

    panel.value.replace("edited")

    assert panel.value.value == "edited"


async def test_a_replacement_rebaselines_its_sources_only_when_it_commits() -> None:
    """Rollback must leave the resource watching what it watched before."""
    bus = sl.runtime.LocalTopicBus()
    topic = sl.runtime.Topic("thing", "1")

    class Watching(sl.Component):
        @sl.resource
        async def value(self) -> str:
            sl.runtime.watch(topic)
            return "loaded"

        @override
        def render(self):
            return sl.paragraph("x")

    panel = Watching()
    await panel.value.reload()

    with pytest.raises(RuntimeError), sl.runtime.transaction():
        panel.value.replace("edited")
        message = "handler failed"
        raise RuntimeError(message)

    bus.publish(topic)
    assert panel.value.pending, "the rolled-back action left the watch intact"


async def test_a_replacement_settles_sources_before_the_commit_becomes_irreversible() -> None:
    class DerivedSource(sl.Component):
        x = sl.state(0)

        @sl.computed
        def derived(self) -> int:
            if self.x == 1:
                message = "boom"
                raise RuntimeError(message)
            return self.x

        @sl.resource
        async def value(self) -> str:
            return str(self.derived)

        @override
        def render(self):
            return sl.paragraph("x")

    panel = DerivedSource()
    await panel.value.reload()

    with pytest.raises(RuntimeError, match="boom"), sl.runtime.transaction():
        panel.x = 1
        panel.value.replace("authoritative")

    assert panel.x == 0
    assert panel.value.value == "0"


async def test_a_replacement_survives_the_action_that_wrote_its_sources() -> None:
    """Baseline the replacement against source versions produced by the same commit.

    Using the old versions would mark the authoritative replacement pending immediately,
    allowing a later reload to overwrite it.
    """

    class Sourced(sl.Component):
        x = sl.state(0)

        @sl.resource
        async def value(self) -> str:
            return f"loaded-{self.x}"

        @override
        def render(self):
            return sl.paragraph("x")

    panel = Sourced()
    await panel.value.reload()
    assert panel.value.value == "loaded-0"

    with sl.runtime.transaction():
        panel.x = 1
        panel.value.replace("authoritative")

    assert panel.value.value == "authoritative"
    assert not panel.value.pending, "a settled replacement is not waiting on its loader"
