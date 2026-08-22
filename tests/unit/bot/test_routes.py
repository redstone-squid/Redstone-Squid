"""Production route identities and their compatibility aliases."""

from importlib import import_module
from typing import Any

import pytest

from squid.bot.routes import (
    build_edit,
    build_log_consent,
    builds,
    poll_close,
    poll_refresh,
    polls,
    redstoner_roles,
    remove_redstoner_role,
    router,
    routes,
)


@pytest.mark.parametrize(
    ("route", "params", "canonical", "legacy"),
    [
        (poll_close, {}, "r:polls:close", "poll:close"),
        (poll_refresh, {}, "r:polls:refresh", "poll:refresh"),
        (build_edit, {"build_id": 5}, "r:builds:5:edit", "edit:build:5"),
        (build_log_consent, {}, "r:build-log-consents:new", "build_log:consent"),
        (remove_redstoner_role, {}, "r:redstoner-roles:self:remove", "remove:role:redstoner"),
    ],
)
def test_production_routes_build_canonical_ids_and_keep_legacy_ids(
    route: Any,
    params: dict[str, object],
    canonical: str,
    legacy: str,
) -> None:
    assert route.id(**params) == canonical
    assert route.match(canonical) == params
    assert route.match(legacy) == params


def test_route_namespace_is_an_ordinary_root_group() -> None:
    assert routes.prefix == "r"
    assert polls.prefix == "r:polls"
    assert builds.prefix == "r:builds"
    assert redstoner_roles.prefix == "r:redstoner-roles"
    assert router.namespace == routes.prefix


def test_production_route_table_has_one_feature_owned_registration_per_identity() -> None:
    for module in (
        "squid.bot.voting.controls",
        "squid.bot.submission.ui.components",
        "squid.bot.submission.consent_banner",
        "squid.bot.give_redstoner",
    ):
        import_module(module)

    descriptions = router.describe()
    assert {route.format for route in descriptions} == {
        "r:polls:close",
        "r:polls:refresh",
        "r:builds:{build_id:int}:edit",
        "r:build-log-consents:new",
        "r:redstoner-roles:self:remove",
    }
    assert len(descriptions) == 5
    assert all(route.middleware[0].endswith(".TraceRoutes") for route in descriptions)
    redstoner = next(route for route in descriptions if route.group_prefix == "r:redstoner-roles")
    assert redstoner.middleware[1].endswith(".OwnerGuildOnly")
