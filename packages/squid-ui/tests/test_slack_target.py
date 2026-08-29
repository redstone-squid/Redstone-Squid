"""Slack target identity and limit contracts."""

from dataclasses import replace

import pytest

from squid_ui import scene, slack
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.adapter import AdapterCapability, AdapterProfile
from squid_ui.planning.resources import Axis, ResourceCost
from squid_ui.target_types import SlackAdapter

SLACK_TEST_ADAPTER = AdapterProfile(
    SlackAdapter,
    "tests.slack",
    ">=3.43,<4",
    capabilities=frozenset(
        {
            AdapterCapability.RENDER_SLACK_MESSAGE,
            AdapterCapability.RENDER_SLACK_MODAL,
            AdapterCapability.RENDER_SLACK_HOME,
        }
    ),
)


@pytest.mark.parametrize(
    ("target", "target_id", "body_type", "blocks"),
    [
        (slack.message_target(adapter=SLACK_TEST_ADAPTER), "slack.block-kit.message", scene.SlackMessage, 50),
        (slack.modal_target(adapter=SLACK_TEST_ADAPTER), "slack.block-kit.modal", scene.SlackModalView, 100),
        (slack.home_target(adapter=SLACK_TEST_ADAPTER), "slack.block-kit.home", scene.SlackHomeView, 100),
    ],
)
def test_slack_target_identity(target, target_id: str, body_type: type, blocks: int) -> None:
    assert target.id == target_id
    assert target.version == 1
    assert target.body_type is body_type
    assert target.capacity(Axis.BLOCKS) == blocks


def test_slack_target_reservation_changes_capacity_and_fingerprint() -> None:
    target = slack.message_target(adapter=SLACK_TEST_ADAPTER)
    reserved = target.reserve(ResourceCost({Axis.BLOCKS: 7}))

    assert reserved.capacity(Axis.BLOCKS) == 43
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
