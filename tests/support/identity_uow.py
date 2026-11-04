"""Typed transaction scopes for identity service fixtures.

CLI and Minecraft scopes copy accounts for reads but only publish their authorization
repository. WebScope also publishes account changes when it commits.
"""

from copy import deepcopy

from tests.support.unit_of_work import PublishingFakeUnitOfWork


class AccountScope[AccountsT](PublishingFakeUnitOfWork[AccountsT]):
    @property
    def accounts(self) -> AccountsT:
        return self.state


class CliScope[RepositoryT, AccountsT](PublishingFakeUnitOfWork[RepositoryT]):
    def __init__(self, repository: RepositoryT, accounts: AccountsT) -> None:
        super().__init__(repository)
        self.accounts = deepcopy(accounts)

    @property
    def cli_authorization(self) -> RepositoryT:
        return self.state


class MinecraftScope[RepositoryT, AccountsT](PublishingFakeUnitOfWork[RepositoryT]):
    def __init__(self, repository: RepositoryT, accounts: AccountsT) -> None:
        super().__init__(repository)
        self.accounts = deepcopy(accounts)

    @property
    def minecraft_authorization(self) -> RepositoryT:
        return self.state


class WebScope[RepositoryT, AccountsT](PublishingFakeUnitOfWork[RepositoryT]):
    def __init__(self, repository: RepositoryT, accounts: AccountsT) -> None:
        super().__init__(repository)
        self._accounts = accounts
        self.accounts = deepcopy(accounts)

    @property
    def web_sessions(self) -> RepositoryT:
        return self.state

    async def commit(self) -> None:
        await super().commit()
        vars(self._accounts).update(vars(deepcopy(self.accounts)))
