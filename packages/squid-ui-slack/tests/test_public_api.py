"""The public Slack convenience target composes with the planner and SDK renderer."""

import squid_ui as sl
import squid_ui_slack as ss
from squid_ui import testing


def test_convenience_target_renders_a_message_for_the_sdk() -> None:
    planned = sl.planning.plan(sl.paragraph("Ready for review."), target=ss.message())
    payload = ss.MessageRenderer().draw(planned.scene, plan=planned)
    assert payload.blocks[0].to_dict()["text"]["text"] == "Ready for review."


def test_slack_adapter_imports_without_other_transports_or_a_bot_framework() -> None:
    testing.assert_imports_without(["squid_ui_slack"], "slack_bolt", "discord", "squid_ui_discord")
