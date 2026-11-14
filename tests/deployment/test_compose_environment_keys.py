"""Check that Compose variables are accepted by the application environment audit.

Compose resolves `${SQUID_*}` itself, but then hands the same `.env` to every container through
`env_file`, so a Compose-only variable also reaches `_audit_unknown_environment_keys`.
With `SQUID_STRICT_UNKNOWN_KEYS` enabled, an unrecognized variable prevents startup.
"""

import re
from pathlib import Path

from squid.config import _INFRASTRUCTURE_ENVIRONMENT_KEYS, _known_environment_keys

PROJECT_ROOT = Path(__file__).parents[2]

COMPOSE_FILES = ("compose.yml", "compose.override.yml", "deploy/compose.production.yml", "deploy/compose.edge.yml")


def _interpolated_squid_keys(text: str) -> set[str]:
    return set(re.findall(r"\$\{(SQUID_[A-Z0-9_]+)", text))


def test_every_compose_interpolated_key_is_known_to_the_audit() -> None:
    accepted = _known_environment_keys() | _INFRASTRUCTURE_ENVIRONMENT_KEYS
    interpolated: set[str] = set()
    for relative_path in COMPOSE_FILES:
        interpolated |= _interpolated_squid_keys((PROJECT_ROOT / relative_path).read_text())

    assert interpolated  # A rename that broke the pattern would otherwise pass vacuously.
    assert sorted(interpolated - accepted) == []


def test_infrastructure_keys_are_not_application_settings() -> None:
    """The allowlist bypasses typo detection, so it must not shadow a real setting."""
    assert _INFRASTRUCTURE_ENVIRONMENT_KEYS.isdisjoint(_known_environment_keys())


def test_infrastructure_keys_are_documented_in_the_example_environment() -> None:
    """An operator cannot discover a Compose-only knob from the settings models."""
    example = (PROJECT_ROOT / ".env.example").read_text()
    documented = {name for name in _INFRASTRUCTURE_ENVIRONMENT_KEYS if re.search(rf"^#?\s*{name}=", example, re.M)}

    # SQUID_ENV_FILE selects the env file, so documenting it inside that file would be circular.
    assert documented >= _INFRASTRUCTURE_ENVIRONMENT_KEYS - {"SQUID_ENV_FILE"}
