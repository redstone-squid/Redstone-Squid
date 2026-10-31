"""Installed error policy covers component rollback, visibility, and failed delivery."""

from typing import Any, override

import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid_ui_discord.delivery import DeliveryResult


class FailingPanel(sd.Screen):
    count: int = sl.state(0)

    def __init__(self, defer: sd.Deferral | None = None) -> None:
        self.defer = defer

    @override
    def render(self) -> sl.LayoutNode:
        return sl.stack(
            sl.paragraph(str(self.count)),
            sl.action_controls(sl.action_control("Fail", self.fail, key="fail"), key="actions"),
        )

    async def fail(self, event: sl.PressEvent) -> None:
        request = await sd.request(event)
        if self.defer is not None:
            await request.defer(self.defer)
        self.count += 1
        raise ValueError("failed change")


@pytest.mark.parametrize("defer", [None, "private", "public"])
async def test_component_failure_rolls_back_then_renders_and_observes(defer: sd.Deferral | None) -> None:
    seen: list[tuple[sd.Request[Any], Exception, DeliveryResult | None]] = []
    panel = FailingPanel(defer)
    owner = object()

    def render(request: sd.Request[Any], error: Exception) -> sl.DocumentLike[sl.ComponentsV2Target]:
        assert panel.count == 0
        assert request.owner is owner
        assert request.localization.locale == "fr"
        return sl.paragraph(f"Could not save: {error}")

    async def observe(request: sd.Request[Any], error: Exception, delivery: DeliveryResult | None) -> None:
        seen.append((request, error, delivery))

    config = sd.Config(
        defaults=sd.MessageRootDefaults(localization=sl.text.Localization(locale="fr")),
        errors=sd.ErrorPolicy(render=render, observe=observe),
    )
    async with sd.testing.stage(panel, owner=owner, config=config) as ui:
        interaction = await ui.press("fail")
        assert panel.count == 0
        assert len(seen) == 1
        request, error, delivery = seen[0]
        assert str(error) == "failed change"
        assert delivery is not None
        assert request.root is ui.root
        assert await sd.request(interaction.source) is request
        if defer is None:
            assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
        else:
            interaction.edit_original_response.assert_awaited_once()
            assert interaction.response.defer.await_args.kwargs["ephemeral"] is (defer == "private")
        interaction.followup.send.assert_not_awaited()


async def test_error_observer_runs_when_notice_delivery_fails() -> None:
    seen: list[tuple[Exception, DeliveryResult | None]] = []

    async def observe(_request: sd.Request[Any], error: Exception, delivery: DeliveryResult | None) -> None:
        seen.append((error, delivery))

    config = sd.Config(errors=sd.ErrorPolicy(render=lambda _req, _error: sl.paragraph("Failed"), observe=observe))
    async with sd.testing.stage(FailingPanel(), owner=object(), config=config) as ui:
        interaction = sd.testing.InteractionHarness(client=ui.interaction.client)
        interaction.response.send_message.error = sd.testing.http_error(403)
        with pytest.raises(Exception, match="403"):
            await ui.root.dispatch("fail", interaction.source)
        assert len(seen) == 1
        assert str(seen[0][0]) == "failed change"
        assert seen[0][1] is None


def test_installation_rejects_the_standalone_root_error_hook() -> None:
    async def hook(interaction: Any, error: Exception, source: str) -> None:
        pass

    with pytest.raises(TypeError, match=r"Config\.errors"):
        sd.Config(defaults=sd.MessageRootDefaults(on_error=hook))


async def test_error_notice_stays_private_after_a_public_response() -> None:
    class AnswerThenFail(FailingPanel):
        @override
        async def fail(self, event: sl.PressEvent) -> None:
            await (await sd.request(event)).respond("Started", audience="public")
            await super().fail(event)

    config = sd.Config(errors=sd.ErrorPolicy(render=lambda _req, _error: sl.paragraph("Failed")))
    async with sd.testing.stage(AnswerThenFail(), owner=object(), config=config) as ui:
        interaction = await ui.press("fail")
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is False
        assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
