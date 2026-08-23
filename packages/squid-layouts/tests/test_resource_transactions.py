"""A resource's replaced value belongs to the action that replaced it.

`Resource.replace` is the application saying "this is the value now". That makes it a write,
and every other write in the package stages in the transaction's overlay: readable by the
action that made it, invisible to everyone else until commit, and gone if the action fails.
"""

import pytest

import squid_layouts as sl


class Panel(sl.Component):
    @sl.resource
    async def value(self) -> str:
        return "loaded"

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
    """Read the resource as a task with no transaction of its own would."""
    import contextvars

    from squid_layouts.runtime.reactivity import _CURRENT

    context = contextvars.copy_context()
    context.run(_CURRENT.set, None)
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

        def render(self):
            return sl.paragraph("x")

    panel = DerivedSource()
    await panel.value.reload()

    with pytest.raises(RuntimeError, match="boom"), sl.runtime.transaction():
        panel.x = 1
        panel.value.replace("authoritative")

    assert panel.x == 0
    assert panel.value.value == "0"
