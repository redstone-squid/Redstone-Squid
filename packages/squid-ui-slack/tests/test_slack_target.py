"""Slack target identity and limit contracts."""

from dataclasses import replace

import pytest

import squid_ui.ext.slack as slack
from squid_ui.errors import LayoutInvariantError
from squid_ui.ext.slack import scene as slack_scene
from squid_ui.ext.slack.adapter import SlackAdapterCapability
from squid_ui.ext.slack.limits import SlackAxis
from squid_ui.ext.slack.target_types import SlackAdapter
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.resources import Axis, ResourceCost

SLACK_TEST_ADAPTER = AdapterProfile(
    SlackAdapter,
    "tests.slack",
    ">=3.43,<4",
    capabilities=frozenset(
        {
            SlackAdapterCapability.RENDER_SLACK_MESSAGE,
            SlackAdapterCapability.RENDER_SLACK_MODAL,
            SlackAdapterCapability.RENDER_SLACK_HOME,
        }
    ),
)


@pytest.mark.parametrize(
    ("target", "target_id", "body_type", "blocks"),
    [
        (slack.message_target(adapter=SLACK_TEST_ADAPTER), "slack.block-kit.message", slack_scene.SlackMessage, 50),
        (slack.modal_target(adapter=SLACK_TEST_ADAPTER), "slack.block-kit.modal", slack_scene.SlackModalView, 100),
        (slack.home_target(adapter=SLACK_TEST_ADAPTER), "slack.block-kit.home", slack_scene.SlackHomeView, 100),
    ],
)
def test_slack_target_identity(target, target_id: str, body_type: type, blocks: int) -> None:
    assert target.id == target_id
    assert target.version == 1
    assert target.body_type is body_type
    assert target.capacity(SlackAxis.BLOCKS) == blocks


def test_slack_target_reservation_changes_capacity_and_fingerprint() -> None:
    target = slack.message_target(adapter=SLACK_TEST_ADAPTER)
    reserved = target.reserve(ResourceCost({SlackAxis.BLOCKS: 7}))

    assert reserved.capacity(SlackAxis.BLOCKS) == 43
    assert reserved.fingerprint != target.fingerprint


def test_slack_target_rejects_unknown_reservation_axes() -> None:
    target = slack.message_target(adapter=SLACK_TEST_ADAPTER)

    with pytest.raises(LayoutInvariantError, match="no reservable resource"):
        target.reserve(ResourceCost({Axis.COMPONENTS: 1}))


def test_slack_target_fingerprint_covers_local_limits() -> None:
    target = slack.message_target(adapter=SLACK_TEST_ADAPTER)
    changed = slack.message_target(
        adapter=SLACK_TEST_ADAPTER,
        limits=replace(slack.SLACK_MESSAGE_LIMITS, fallback_text=39999),
    )

    assert changed.fingerprint != target.fingerprint
