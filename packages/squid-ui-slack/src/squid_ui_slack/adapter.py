"""Verified Slack SDK adapter profile and boundary checks."""

from functools import cache
from importlib.metadata import PackageNotFoundError, version

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from squid_ui.errors import DrawInvariantError
from squid_ui.planning.adapter import AdapterCapability, AdapterProfile
from squid_ui.target_types import SlackSdk343Adapter, SlackSdkAdapter

SLACK_SDK_BEHAVIOR_CAPABILITIES = frozenset(
    {
        AdapterCapability.RENDER_SLACK_MESSAGE,
        AdapterCapability.RENDER_SLACK_MODAL,
        AdapterCapability.RENDER_SLACK_HOME,
    }
)

SLACK_SDK_343_ADAPTER = AdapterProfile(
    SlackSdk343Adapter,
    "slack-sdk",
    ">=3.43,<3.44",
    SLACK_SDK_BEHAVIOR_CAPABILITIES,
)


@cache
def _installed_slack_sdk() -> Version:
    """Return the installed Slack SDK version once per process."""
    return Version(version("slack-sdk"))


@cache
def _version_range(expression: str) -> SpecifierSet:
    """Parse and cache one adapter version constraint."""
    return SpecifierSet(expression)


def slack_sdk_adapter_profile(
    name: str,
    version_expression: str,
    *,
    capabilities: frozenset[AdapterCapability] = SLACK_SDK_BEHAVIOR_CAPABILITIES,
) -> AdapterProfile[SlackSdkAdapter]:
    """Declare an application-verified Slack SDK adapter profile."""
    return AdapterProfile(SlackSdkAdapter, name, version_expression, capabilities)


def require_slack_sdk_capability(
    profile: AdapterProfile[SlackSdkAdapter], capability: AdapterCapability, operation: str
) -> None:
    """Verify the selected profile and installed Slack SDK at a drawing boundary."""
    if capability not in profile.capabilities:
        message = f"adapter profile {profile.name!r} cannot {operation}; it lacks {capability!r}"
        raise DrawInvariantError(message)
    try:
        installed = _installed_slack_sdk()
        applicable = _version_range(profile.version_expression)
    except (PackageNotFoundError, InvalidVersion, InvalidSpecifier) as error:
        message = f"cannot verify Slack SDK for adapter profile {profile.name!r}: {error}"
        raise DrawInvariantError(message) from error
    if installed not in applicable:
        message = (
            f"adapter profile {profile.name!r} applies to Slack SDK {profile.version_expression}, "
            f"but {installed} is installed; supply a custom profile verified for this version"
        )
        raise DrawInvariantError(message)


__all__ = [
    "SLACK_SDK_343_ADAPTER",
    "SLACK_SDK_BEHAVIOR_CAPABILITIES",
    "require_slack_sdk_capability",
    "slack_sdk_adapter_profile",
]
