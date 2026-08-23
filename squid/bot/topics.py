"""The live-resource binding helper for Discord panels.

The vocabulary itself lives in `squid.topics`, because the worker publishes into it too and
must not import the Discord layer to do so.
"""

import weakref
from collections.abc import Awaitable, Callable

import squid_layouts as sl


def follow_resource[TargetT: object](
    bus: sl.TopicBus,
    reactor: sl.discord.Reactor,
    mount: sl.discord.Mount,
    topic: sl.Topic,
    target: TargetT,
    reload: Callable[[TargetT], Awaitable[None]],
) -> Callable[[], None]:
    """Re-fetch a component target before scheduling its mount for a topic refresh."""
    active = True

    def unsubscribe_reload() -> None:
        nonlocal active
        if not active:
            return
        active = False
        unsubscribe()

    def target_collected(_reference: weakref.ReferenceType[TargetT]) -> None:
        unsubscribe_reload()

    target_ref = weakref.ref(target, target_collected)

    async def refresh(changed: sl.Topic) -> None:
        if (current := target_ref()) is None:
            unsubscribe_reload()
            return
        await reload(current)

    unsubscribe = bus.subscribe(topic, refresh, label=f"reload:{mount.id}")
    unfollow = reactor.follow(mount, topic)

    async def finish(finished: sl.discord.Mount) -> None:
        unsubscribe_reload()

    mount.on_finish(finish)

    def stop() -> None:
        unsubscribe_reload()
        unfollow()

    return stop
