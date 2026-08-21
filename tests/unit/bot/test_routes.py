"""Production route identities and their compatibility aliases."""

from typing import Any

import pytest

from squid.bot.routes import build_edit, build_log_consent, poll_close, poll_refresh, remove_redstoner_role


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
