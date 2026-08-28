"""Declarative component opening policy built on :class:`Invocation`."""

from collections.abc import Hashable
from functools import cache
from typing import ClassVar, Self

from squid_ui.runtime.component import Component
from squid_ui.target_types import ComponentsV2Target
from squid_ui_discord.access import Owner
from squid_ui_discord.invocation import Invocation, Visibility
from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.message_root_contracts import ExpiryPolicy, PauseUpdates, RenewEphemeral
from squid_ui_discord.runtime import InvocationSource
from squid_ui_discord.session_specs import ScopeKind, SessionOptions, SessionSpec
from squid_ui_discord.sessions import DEFAULT_ADMISSION, AdmissionSpec


class Screen(Component[ComponentsV2Target]):
    """A component whose class declares how and where each instance is shown."""

    session: ClassVar[str | None] = None
    scope: ClassVar[ScopeKind] = ScopeKind.USER
    admission: ClassVar[AdmissionSpec] = DEFAULT_ADMISSION
    capacity: ClassVar[int | None] = None
    quota: ClassVar[int | None] = None
    domain: ClassVar[str | None] = None
    visibility: ClassVar[Visibility] = "personal"
    timeout: ClassVar[float | None] = 180
    expiry: ClassVar[ExpiryPolicy | None] = None
    follow_topics: ClassVar[bool] = False
    options: ClassVar[SessionOptions] = {}

    opening: Invocation
    """The invocation that prepared and showed this instance."""

    async def prepare(self) -> None:
        """Load invocation-specific state before this screen is opened or rendered."""

    @classmethod
    @cache
    def spec(cls) -> SessionSpec:
        """Derive and cache this screen's reusable session recipe."""
        if cls.session is None:
            message = f"{cls.__name__} declares no session; show() mounts it directly"
            raise TypeError(message)
        options: SessionOptions = {}
        options.update(cls.options)
        options["timeout"] = cls.timeout
        if cls.expiry is not None:
            options["expiry"] = cls.expiry
        return SessionSpec(
            cls.session,
            scope=cls.scope,
            admission=cls.admission,
            capacity=cls.capacity,
            quota=cls.quota,
            domain=cls.domain,
            options=options,
        )

    async def show(
        self,
        source_or_invocation: InvocationSource | Invocation,
        /,
        parent: MessageRoot | None = None,
        wait: bool = False,
        key: Hashable | None = None,
    ) -> Self | None:
        """Prepare and show this instance under its class's policy."""
        if hasattr(self, "opening"):
            message = f"{type(self).__name__} has already been shown"
            raise RuntimeError(message)
        invocation = (
            source_or_invocation
            if isinstance(source_or_invocation, Invocation)
            else await Invocation.of(source_or_invocation)
        )
        self.opening = invocation
        await self.prepare()
        options = self._opening_options(invocation)
        if self.session is None:
            await invocation.mount(
                self,
                access=Owner(invocation.user.id),
                visibility=self.visibility,
                **options,
            )
            return self
        result = await invocation.open(
            self,
            self.spec(),
            visibility=self.visibility,
            parent=parent,
            wait=wait,
            key=key,
            **options,
        )
        return self if result else None

    @classmethod
    def _opening_options(cls, invocation: Invocation) -> SessionOptions:
        scheduler = invocation.runtime.scheduler if cls.follow_topics else None
        options: SessionOptions = {"scheduler": scheduler}
        if cls.session is None:
            options.update(cls.options)
            options["timeout"] = cls.timeout
        if cls.expiry is not None:
            expiry = cls.expiry
            if isinstance(expiry, RenewEphemeral) and scheduler is None:
                expiry = PauseUpdates(expiry.warning)
            options["expiry"] = expiry
        return options


__all__ = ["Screen"]
