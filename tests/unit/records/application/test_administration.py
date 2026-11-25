"""Staff record changes require live rebuild authority before pinning a version."""

from unittest.mock import Mock, create_autospec

import pytest

from squid.core.actors import ActingContext, Actor
from squid.core.errors import AuthorizationError
from squid.core.permissions.application.services import PermissionService
from squid.core.permissions.domain.catalogue import RECORD_ENTRY_REBUILD
from squid.core.records.application import RecordAdministrationService, RecordComputationService
from squid.core.records.domain import BuildKind


async def test_rebuild_denial_precedes_pin_and_computation() -> None:
    factory = Mock()
    computation = create_autospec(RecordComputationService, instance=True)
    permissions = create_autospec(PermissionService, instance=True)
    permissions.require.side_effect = AuthorizationError
    service = RecordAdministrationService(computation, permissions, uow_factory=factory)
    context = ActingContext(Actor(account_id=1), Actor(account_id=2))

    with pytest.raises(AuthorizationError):
        await service.rebuild(context=context, current_version_id=3, kinds=(BuildKind.DOOR,))

    permissions.require.assert_awaited_once_with(context.effective, RECORD_ENTRY_REBUILD)
    factory.assert_not_called()
    computation.rebuild.assert_not_awaited()
