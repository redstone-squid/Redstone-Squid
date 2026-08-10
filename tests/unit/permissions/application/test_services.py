"""Authorization application service tests."""

from collections.abc import Sequence

from whenever import Instant

from squid.permissions.application import AuthorizationService
from squid.permissions.domain import GlobalAdministrator


class FakeGlobalAdministratorStore:
    def __init__(self) -> None:
        self.administrators: dict[int, GlobalAdministrator] = {}

    async def contains(self, account_id: int) -> bool:
        return account_id in self.administrators

    async def list(self) -> Sequence[GlobalAdministrator]:
        return tuple(self.administrators.values())

    async def grant(self, account_id: int, granted_by_account_id: int) -> GlobalAdministrator:
        return self.administrators.setdefault(
            account_id,
            GlobalAdministrator(account_id, granted_by_account_id, Instant.now()),
        )

    async def revoke(self, account_id: int) -> bool:
        return self.administrators.pop(account_id, None) is not None


async def test_global_administrator_membership_is_idempotent() -> None:
    store = FakeGlobalAdministratorStore()
    service = AuthorizationService(store)

    granted = await service.grant_global_administrator(20, granted_by_account_id=10)
    repeated = await service.grant_global_administrator(20, granted_by_account_id=11)

    assert repeated == granted
    assert await service.is_global_administrator(20)
    assert await service.list_global_administrators() == (granted,)
    assert await service.revoke_global_administrator(20)
    assert not await service.revoke_global_administrator(20)
    assert not await service.is_global_administrator(20)
