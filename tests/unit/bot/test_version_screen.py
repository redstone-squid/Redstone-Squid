"""The version catalogue screen."""

from dataclasses import dataclass
from typing import Any, cast

from squid.bot.version_tracking import VersionScreen
from squid.versions.application import VersionService
from squid.versions.domain import Edition, MinecraftVersion
from squid_ui.testing import RecordingResponder, labels, submit, submit_event


class VersionRecorder(VersionService):
    def __init__(self) -> None:
        self.list_calls: list[tuple[Edition, int | None]] = []
        self.add_calls: list[tuple[str, Edition | None]] = []

    async def list_display(self, edition: Edition, *, limit: int | None = None) -> list[str]:
        self.list_calls.append((edition, limit))
        return ["1.21", "1.20"] if edition == "Java" else ["1.21.90"]

    async def add(self, version_string: str, *, edition: Edition | None = None) -> MinecraftVersion:
        self.add_calls.append((version_string, edition))
        return MinecraftVersion("Java", 1, 22, 0)


class Authorizer:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls = 0

    async def __call__(self) -> bool:
        self.calls += 1
        return self.allowed


@dataclass(frozen=True)
class ScreenHarness:
    screen: VersionScreen
    versions: VersionRecorder
    authorizer: Authorizer


def make_screen(*, allowed: bool = True) -> ScreenHarness:
    versions = VersionRecorder()
    authorizer = Authorizer(allowed)
    return ScreenHarness(
        VersionScreen(versions, can_create=allowed, authorize_create=authorizer),
        versions,
        authorizer,
    )


async def test_version_catalogue_loads_both_editions_into_a_browser() -> None:
    harness = make_screen()
    screen = harness.screen
    await screen.on_load()

    assert screen._browser is not None
    assert [item.edition for item in cast(Any, screen._browser.source).items] == ["Java", "Java", "Bedrock"]
    assert "Add version" in labels(screen.render())


async def test_creation_rechecks_permission_and_refreshes() -> None:
    harness = make_screen()
    screen = harness.screen
    await screen.on_load()
    responder = RecordingResponder()

    await submit(screen, "add-version", {"edition": "Java", "version": "1.22"}, responder=responder)

    assert harness.versions.add_calls == [("1.22", "Java")]
    assert harness.versions.list_calls == [("Java", None), ("Bedrock", None)] * 2
    assert harness.authorizer.calls == 1
    assert len(responder.notices) == 1


async def test_revoked_creation_permission_prevents_the_write() -> None:
    harness = make_screen(allowed=False)
    responder = RecordingResponder()

    await harness.screen._add(
        # The control is intentionally absent when initial authorization fails; drive the handler
        # with the public event factory to verify the mandatory authorization recheck.
        submit_event({"edition": "Java", "version": "1.22"}, responder=responder)
    )

    assert harness.versions.add_calls == []
    assert harness.authorizer.calls == 1
    assert len(responder.notices) == 1
