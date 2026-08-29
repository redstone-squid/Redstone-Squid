"""Slack scene model, codec, and schema contracts."""

import jsonschema
import pytest

from squid_ui import scene
from squid_ui.entity import ConversationType


def _document(body: scene.SlackBody) -> scene.Scene[scene.SlackBody]:
    target = {
        scene.SlackMessage: "slack.block-kit.message",
        scene.SlackModalView: "slack.block-kit.modal",
        scene.SlackHomeView: "slack.block-kit.home",
    }[type(body)]
    return scene.Scene(scene.Codec.protocol, target, 1, body)


def test_slack_message_scene_round_trips_and_validates() -> None:
    action = scene.SlackActionRef("approve")
    document = _document(
        scene.SlackMessage(
            "Build review: approve or reject",
            (
                scene.SlackHeader(scene.SlackText("Build review", scene.SlackTextKind.PLAIN)),
                scene.SlackSection(
                    scene.SlackText("*Piston door* by <@U123>"),
                    fields=(scene.SlackText("Status\nPending"),),
                ),
                scene.SlackActions(
                    (
                        scene.SlackButton(
                            scene.SlackText("Approve", scene.SlackTextKind.PLAIN),
                            action=action,
                            value="approved",
                            style=scene.SlackButtonStyle.PRIMARY,
                        ),
                        scene.SlackSelect(
                            action=scene.SlackActionRef("reviewers"),
                            kind=scene.SlackSelectKind.USERS,
                            placeholder=scene.SlackText("Reviewer", scene.SlackTextKind.PLAIN),
                            initial_values=("U123",),
                        ),
                    )
                ),
                scene.SlackTable(((scene.SlackText("Metric"), scene.SlackText("Value")),)),
                scene.SlackCarousel((scene.SlackCard(title=scene.SlackText("Related build")),)),
            ),
        )
    )

    encoded = scene.Codec.to_dict(document)

    assert scene.Codec.loads(scene.Codec.dumps(document)) == document
    jsonschema.validate(encoded, scene.Codec.schema())


def test_slack_modal_and_home_scenes_round_trip() -> None:
    modal = _document(
        scene.SlackModalView(
            "review",
            scene.SlackText("Review", scene.SlackTextKind.PLAIN),
            scene.SlackText("Save", scene.SlackTextKind.PLAIN),
            scene.SlackText("Cancel", scene.SlackTextKind.PLAIN),
            (
                scene.SlackInput(
                    "review:notes",
                    scene.SlackText("Notes", scene.SlackTextKind.PLAIN),
                    scene.SlackTextInput("notes", multiline=True),
                ),
                scene.SlackInput(
                    "review:channel",
                    scene.SlackText("Conversation", scene.SlackTextKind.PLAIN),
                    scene.SlackSelect(
                        action=scene.SlackActionRef("conversation"),
                        kind=scene.SlackSelectKind.CONVERSATIONS,
                        conversation_types=(ConversationType.WORKSPACE_PUBLIC,),
                    ),
                ),
                scene.SlackAlert(scene.SlackText("Check the details", scene.SlackTextKind.PLAIN)),
            ),
        )
    )
    home = _document(scene.SlackHomeView((scene.SlackSection(scene.SlackText("Welcome")),)))

    for document in (modal, home):
        assert scene.Codec.loads(scene.Codec.dumps(document)) == document
        jsonschema.validate(scene.Codec.to_dict(document), scene.Codec.schema())


def test_slack_scene_rejects_invalid_element_unions() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        scene.SlackButton(scene.SlackText("Broken", scene.SlackTextKind.PLAIN))
    with pytest.raises(ValueError, match="action or route"):
        scene.SlackSelect(kind=scene.SlackSelectKind.USERS)
    with pytest.raises(ValueError, match="conversation_types"):
        scene.SlackSelect(
            action=scene.SlackActionRef("pick"),
            conversation_types=(ConversationType.WORKSPACE_PUBLIC,),
        )
