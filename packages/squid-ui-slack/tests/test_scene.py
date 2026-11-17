"""Slack scene model, codec, and schema contracts."""

import jsonschema
import pytest

from squid_ui import scene
from squid_ui.content.entity import ConversationType
from squid_ui.ext.slack import scene as slack_scene


def _document(body: slack_scene.SlackBody) -> scene.Scene[slack_scene.SlackBody]:
    target = {
        slack_scene.SlackMessage: "slack.block-kit.message",
        slack_scene.SlackModalView: "slack.block-kit.modal",
        slack_scene.SlackHomeView: "slack.block-kit.home",
    }[type(body)]
    return scene.Scene(scene.Codec.protocol, target, 1, body)


def test_slack_message_scene_round_trips_and_validates() -> None:
    action = slack_scene.SlackActionRef("approve")
    document = _document(
        slack_scene.SlackMessage(
            "Build review: approve or reject",
            (
                slack_scene.SlackHeader(slack_scene.SlackText("Build review", slack_scene.SlackTextKind.PLAIN)),
                slack_scene.SlackSection(
                    slack_scene.SlackText("*Piston door* by <@U123>"),
                    fields=(slack_scene.SlackText("Status\nPending"),),
                ),
                slack_scene.SlackActions(
                    (
                        slack_scene.SlackButton(
                            slack_scene.SlackText("Approve", slack_scene.SlackTextKind.PLAIN),
                            action=action,
                            value="approved",
                            style=slack_scene.SlackButtonStyle.PRIMARY,
                        ),
                        slack_scene.SlackSelect(
                            action=slack_scene.SlackActionRef("reviewers"),
                            kind=slack_scene.SlackSelectKind.USERS,
                            placeholder=slack_scene.SlackText("Reviewer", slack_scene.SlackTextKind.PLAIN),
                            initial_values=("U123",),
                        ),
                    )
                ),
                slack_scene.SlackTable(((slack_scene.SlackText("Metric"), slack_scene.SlackText("Value")),)),
                slack_scene.SlackCarousel((slack_scene.SlackCard(title=slack_scene.SlackText("Related build")),)),
            ),
        )
    )

    encoded = scene.Codec.to_dict(document)

    assert scene.Codec.loads(scene.Codec.dumps(document)) == document
    jsonschema.validate(encoded, scene.Codec.schema())


def test_slack_text_decode_rejects_non_boolean_flags() -> None:
    document = _document(slack_scene.SlackMessage("", (slack_scene.SlackHeader(slack_scene.SlackText("Heading")),)))
    raw = scene.Codec.to_dict(document)
    raw["body"]["blocks"][0]["text"]["emoji"] = 1

    with pytest.raises(scene.CodecError):
        scene.Codec.from_dict(raw)


def test_slack_limit_is_shared_by_codec_and_schema() -> None:
    document = _document(
        slack_scene.SlackMessage(
            "", (slack_scene.SlackActions((slack_scene.SlackButton(slack_scene.SlackText("Go"), url="https://x"),)),)
        )
    )
    raw = scene.Codec.to_dict(document)
    raw["body"]["blocks"][0]["elements"][0]["url"] = "x" * 3001

    with pytest.raises(scene.CodecError):
        scene.Codec.from_dict(raw)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(raw, scene.Codec.schema())


def test_slack_modal_and_home_scenes_round_trip() -> None:
    modal = _document(
        slack_scene.SlackModalView(
            "review",
            slack_scene.SlackText("Review", slack_scene.SlackTextKind.PLAIN),
            slack_scene.SlackText("Save", slack_scene.SlackTextKind.PLAIN),
            slack_scene.SlackText("Cancel", slack_scene.SlackTextKind.PLAIN),
            (
                slack_scene.SlackInput(
                    "review:notes",
                    slack_scene.SlackText("Notes", slack_scene.SlackTextKind.PLAIN),
                    slack_scene.SlackTextInput("notes", multiline=True),
                ),
                slack_scene.SlackInput(
                    "review:channel",
                    slack_scene.SlackText("Conversation", slack_scene.SlackTextKind.PLAIN),
                    slack_scene.SlackSelect(
                        action=slack_scene.SlackActionRef("conversation"),
                        kind=slack_scene.SlackSelectKind.CONVERSATIONS,
                        conversation_types=(ConversationType.WORKSPACE_PUBLIC,),
                    ),
                ),
                slack_scene.SlackAlert(slack_scene.SlackText("Check the details", slack_scene.SlackTextKind.PLAIN)),
            ),
        )
    )
    home = _document(slack_scene.SlackHomeView((slack_scene.SlackSection(slack_scene.SlackText("Welcome")),)))

    for document in (modal, home):
        assert scene.Codec.loads(scene.Codec.dumps(document)) == document
        jsonschema.validate(scene.Codec.to_dict(document), scene.Codec.schema())


def test_slack_scene_rejects_invalid_element_unions() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        slack_scene.SlackButton(slack_scene.SlackText("Broken", slack_scene.SlackTextKind.PLAIN))
    with pytest.raises(ValueError, match="action, route, or form action"):
        slack_scene.SlackSelect(kind=slack_scene.SlackSelectKind.USERS)
    with pytest.raises(ValueError, match="conversation_types"):
        slack_scene.SlackSelect(
            action=slack_scene.SlackActionRef("pick"),
            conversation_types=(ConversationType.WORKSPACE_PUBLIC,),
        )
