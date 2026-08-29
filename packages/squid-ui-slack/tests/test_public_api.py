"""Public namespace and package dependency contracts for the Slack adapter."""

import tomllib
from pathlib import Path

import squid_ui_slack as ss
from squid_ui.planning.adapter import AdapterCapability
from squid_ui.target_types import SlackSdk343Adapter


def test_package_exposes_targets_renderers_and_adapter_profile() -> None:
    assert ss.SLACK_MESSAGE_SDK343 is ss.target.SLACK_MESSAGE_SDK343
    assert ss.SLACK_MODAL_SDK343 is ss.target.SLACK_MODAL_SDK343
    assert ss.SLACK_HOME_SDK343 is ss.target.SLACK_HOME_SDK343
    assert ss.SLACK_SDK_343_ADAPTER.family is SlackSdk343Adapter
    assert (
        frozenset(
            {
                AdapterCapability.RENDER_SLACK_MESSAGE,
                AdapterCapability.RENDER_SLACK_MODAL,
                AdapterCapability.RENDER_SLACK_HOME,
            }
        )
        == ss.SLACK_SDK_BEHAVIOR_CAPABILITIES
    )
    assert {name for name in ss.__all__ if not hasattr(ss, name)} == set()


def test_convenience_targets_bind_each_surface_to_the_sdk() -> None:
    assert ss.message().id == "slack.block-kit.message"
    assert ss.modal().id == "slack.block-kit.modal"
    assert ss.home().id == "slack.block-kit.home"
    assert ss.message().adapter is ss.SLACK_SDK_343_ADAPTER


def test_distribution_is_an_sdk_only_leaf() -> None:
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["version"] == "0.1.0a1"
    assert project["dependencies"] == [
        "squid-ui==0.1.0a1",
        "slack-sdk>=3.43,<4",
        "packaging>=24,<27",
    ]
    assert "slack-bolt" not in " ".join(project["dependencies"])
