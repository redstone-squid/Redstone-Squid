"""Permission-gated runtime edits resolve their audit actor through Squid accounts."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from squid.bot.admin_cog import Admin
from squid.core.accounts.domain import IdentityProvider
from squid.core.actors import ActingContext, Actor
from squid.core.edits.domain import EditValues, LatestRevision, Revision
from squid.core.errors import AuthorizationError
from squid.core.permissions.domain.catalogue import SETTINGS_RUNTIME_EDIT, SETTINGS_RUNTIME_VIEW
from squid.core.settings.application.edits import RUNTIME_TARGET
from squid.core.settings.domain.runtime import RuntimeSettings


@pytest.mark.parametrize("revision", [None, 1])
@pytest.mark.parametrize("linked", [True, False])
async def test_runtime_edit_uses_internal_account_or_refuses_unlinked_owner(
    linked: bool,
    revision: int | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorize = AsyncMock()
    monkeypatch.setattr("squid.bot.admin_cog.enforce", authorize)
    account = SimpleNamespace(id=17, needs_consent_refresh=False) if linked else None
    accounts = SimpleNamespace(get_account_by_identity=AsyncMock(return_value=account))
    edits = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                receipt=SimpleNamespace(after=EditValues.of({"welcome_forward_chance": 0.25})),
                outcome=SimpleNamespace(revisions=(Revision(RUNTIME_TARGET, 2),)),
            )
        )
    )
    settings = SimpleNamespace()
    owner = SimpleNamespace(
        bot=SimpleNamespace(
            services=SimpleNamespace(accounts=accounts, settings=settings, edits=edits),
            actors=SimpleNamespace(resolve=AsyncMock(return_value=Actor(account_id=17))),
        )
    )
    request = SimpleNamespace(user=SimpleNamespace(id=999999), client=owner.bot)

    callback = cast(Any, Admin)._runtime_settings
    await callback(owner, request, "welcome_forward_chance", value="0.25", revision=revision)

    authorize.assert_awaited_once_with(request, SETTINGS_RUNTIME_EDIT)
    accounts.get_account_by_identity.assert_awaited_once_with(IdentityProvider.DISCORD, "999999")
    if linked:
        (command,) = edits.execute.call_args.args
        actor = edits.execute.call_args.kwargs["context"].effective
        assert command.values.unpack() == {"welcome_forward_chance": 0.25}
        assert command.expected == (
            LatestRevision(RUNTIME_TARGET) if revision is None else Revision(RUNTIME_TARGET, revision),
        )
        assert actor.account_id == 17
    else:
        edits.execute.assert_not_awaited()


@pytest.mark.parametrize(("value", "node"), [(None, SETTINGS_RUNTIME_VIEW), ("false", SETTINGS_RUNTIME_EDIT)])
async def test_runtime_command_checks_permission_before_reading_settings(
    value: str | None,
    node: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorize = AsyncMock(side_effect=AuthorizationError)
    monkeypatch.setattr("squid.bot.admin_cog.enforce", authorize)
    callback = cast(Any, Admin)._runtime_settings
    request = SimpleNamespace()
    with pytest.raises(AuthorizationError):
        await callback(SimpleNamespace(), request, "welcome_enabled", value=value)
    authorize.assert_awaited_once_with(request, node)


async def test_runtime_inspection_includes_json_type_constraints_and_example(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("squid.bot.admin_cog.enforce", AsyncMock())
    settings = RuntimeSettings()
    owner = SimpleNamespace(
        bot=SimpleNamespace(
            services=SimpleNamespace(settings=SimpleNamespace(runtime_snapshot=AsyncMock(return_value=(settings, 7))))
        )
    )
    with ActingContext.direct(Actor(account_id=17)).scope():
        result = await cast(Any, Admin)._runtime_settings(owner, SimpleNamespace(), "welcome_forward_chance")
    rendered = str(result)
    assert '"type": "number"' in rendered
    assert '"minimum": 0' in rendered
    assert '"maximum": 1' in rendered
    assert '"default": 0.1' in rendered
    assert "revision 7" in rendered
