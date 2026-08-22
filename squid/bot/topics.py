"""The bot's exact topic vocabulary and live-resource binding helper."""

import weakref
from collections.abc import Awaitable, Callable

import squid_layouts as sl
from squid.posts.domain import ResourceKind

type ResourceTopic = tuple[ResourceKind, str]


def resource_topic(resource_kind: ResourceKind, resource_key: str) -> ResourceTopic:
    """Address one bot-owned resource consistently across publishers and subscribers."""
    return resource_kind, resource_key


def follow_resource[TargetT: object](
    bus: sl.TopicBus,
    reactor: sl.discord.Reactor,
    mount: sl.discord.Mount,
    topic: ResourceTopic,
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
