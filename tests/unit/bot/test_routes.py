"""Production route identities and their compatibility aliases."""

from importlib import import_module, reload

import pytest

from squid.bot.routes import router, routes
from squid_ui_discord.routing import RouteHandle


@pytest.mark.parametrize(
    ("module_name", "route_name", "params", "canonical", "legacy"),
    [
        ("squid.bot.voting.controls", "poll_close", {}, "r:polls:close", "poll:close"),
        ("squid.bot.voting.controls", "poll_refresh", {}, "r:polls:refresh", "poll:refresh"),
        ("squid.bot.submission.ui.controls", "edit_build", {"build_id": 5}, "r:builds:5:edit", "edit:build:5"),
        (
            "squid.bot.submission.ui.controls",
            "recover_build_editor",
            {"build_id": 5},
            "r:builds:5:edit:recover",
            "r:builds:5:edit:recover",
        ),
        (
            "squid.bot.submission.consent_banner",
            "build_log_consent",
            {},
            "r:build-log-consents:new",
            "build_log:consent",
        ),
        (
            "squid.bot.give_redstoner",
            "remove_redstoner_role",
            {},
            "r:redstoner-roles:self:remove",
            "remove:role:redstoner",
        ),
    ],
)
def test_production_routes_build_canonical_ids_and_keep_legacy_ids(
    module_name: str,
    route_name: str,
    params: dict[str, object],
    canonical: str,
    legacy: str,
) -> None:
    route = getattr(import_module(module_name), route_name)
    assert route.id(**params) == canonical
    identity = route.route if isinstance(route, RouteHandle) else route
    assert identity.match(canonical) == params
    assert identity.match(legacy) == params


def test_route_namespace_is_an_ordinary_root_group() -> None:
    polls = import_module("squid.bot.voting.controls").polls
    builds = import_module("squid.bot.submission.ui.controls").builds
    redstoner_roles = import_module("squid.bot.give_redstoner").redstoner_roles
    assert routes.prefix == "r"
    assert polls.prefix == "r:polls"
    assert builds.prefix == "r:builds"
    assert redstoner_roles.prefix == "r:redstoner-roles"
    assert router.namespace == routes.prefix


def test_production_route_table_has_one_feature_owned_registration_per_identity() -> None:
    for module in (
        "squid.bot.voting.controls",
        "squid.bot.submission.ui.controls",
        "squid.bot.submission.consent_banner",
        "squid.bot.give_redstoner",
    ):
        import_module(module)

    descriptions = router.describe()
    assert {route.format for route in descriptions} == {
        "r:polls:close",
        "r:polls:refresh",
        "r:builds:{build_id:int}:edit",
        "r:builds:{build_id:int}:edit:recover",
        "r:builds:draft:{draft_id}:reopen",
        "r:builds:revision:{proposal_id}:reopen",
        "r:builds:inference:{run_id}:reopen",
        "r:build-log-consents:new",
        "r:redstoner-roles:self:remove",
    }
    assert len(descriptions) == 9
    assert all(route.middleware[0].endswith(".TraceRoutes") for route in descriptions)
    redstoner = next(route for route in descriptions if route.group_prefix == "r:redstoner-roles")
    assert redstoner.middleware[1].endswith(".OwnerGuildOnly")


def test_reloading_build_controls_keeps_imported_handles_current() -> None:
    controls = import_module("squid.bot.submission.ui.controls")
    imported = controls.edit_build
    old_handler = imported.__wrapped__
    identity = imported.route

    reload(controls)

    assert controls.edit_build is imported
    assert imported.route is identity
    assert imported.__wrapped__ is not old_handler
    resolved = router.resolve(imported.id(build_id=5))
    assert resolved is not None
    assert resolved[0].handler is imported.__wrapped__
