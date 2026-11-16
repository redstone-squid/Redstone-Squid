"""Screens and persistent actions occupy one production endpoint registry."""

from importlib import import_module, reload
from typing import Any

import pytest

from squid.bot.routes import router


@pytest.mark.parametrize(
    ("module", "name", "params", "address"),
    [
        ("squid.bot.voting.controls", "close_poll", {}, "/polls/close"),
        ("squid.bot.voting.controls", "refresh_poll", {}, "/polls/refresh"),
        ("squid.bot.submission.ui.controls", "edit_build", {"build_id": 5}, "/builds/5/edit"),
        ("squid.bot.submission.consent_banner", "open_consent_prompt", {}, "/build-log-consents/new"),
        ("squid.bot.give_redstoner", "remove_own_redstoner_role", {}, "/redstoner-roles/self/remove"),
    ],
)
def test_production_actions_are_canonical_addresses(
    module: str, name: str, params: dict[str, Any], address: str
) -> None:
    endpoint = getattr(import_module(module), name)
    assert endpoint.address(**params) == address
    assert endpoint.custom_id(**params) == "ui:" + address
    match = router.resolve(address)
    assert match.endpoint is endpoint
    assert match.arguments == params


def test_reloading_build_controls_keeps_imported_handles_current() -> None:
    controls = import_module("squid.bot.submission.ui.controls")
    imported = controls.edit_build
    old_handler = imported.function
    reload(controls)
    assert controls.edit_build is imported
    assert imported.function is not old_handler
    assert router.resolve(imported.address(build_id=5)).endpoint is imported


def test_reloading_role_controls_replaces_middleware_without_duplicating_it() -> None:
    controls = import_module("squid.bot.give_redstoner")
    group = controls.redstoner_roles
    previous = group.middleware[0]
    reload(controls)
    assert controls.redstoner_roles is group
    assert len(group.middleware) == 1
    assert group.middleware[0] is not previous
