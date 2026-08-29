"""Declarative component opening policy built on :class:`Invocation`."""

from collections.abc import Hashable
from typing import ClassVar, Self, cast

from squid_ui.runtime.component import Component
from squid_ui.target_types import ComponentsV2Target
from squid_ui_discord.access import AccessPolicy, Owner
from squid_ui_discord.invocation import Invocation, Private, Visibility
from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.message_root_contracts import ExpiryPolicy, PauseUpdates, RenewEphemeral
from squid_ui_discord.runtime import InvocationSource
from squid_ui_discord.session_specs import ScopeKind, SessionOptions, SessionSpec
from squid_ui_discord.sessions import DEFAULT_ADMISSION, AdmissionSpec

_DEDICATED_ROOT_OPTIONS = frozenset({"expiry", "localization", "scheduler", "timeout"})
"""Options owned by named Screen policy or by the invocation."""


class Screen(Component[ComponentsV2Target]):
    """A component whose class declares how and where each instance is shown.

    Each instance accepts one call to :meth:`show`. Invocation-dependent loading belongs in
    :meth:`~squid_ui.runtime.component.Component.on_load`, after :attr:`opening` is available and
    only if the opening reaches delivery.
    """

    session_name: ClassVar[str | None] = None
    scope: ClassVar[ScopeKind] = ScopeKind.USER
    admission: ClassVar[AdmissionSpec] = DEFAULT_ADMISSION
    capacity: ClassVar[int | None] = None
    quota: ClassVar[int | None] = None
    domain: ClassVar[str | None] = None

    access: ClassVar[AccessPolicy | None] = None
    """A fixed access policy, or `None` to admit only the user who opens the screen."""

    visibility: ClassVar[Visibility] = "personal"
    timeout: ClassVar[float | None] = 180
    expiry: ClassVar[ExpiryPolicy | None] = None
    follow_topics: ClassVar[bool] = False
    root_options: ClassVar[SessionOptions] = {}

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        cls._validate_policy()

    @property
    def opening(self) -> Invocation:
        """The invocation resolved by this instance's call to :meth:`show`."""
        try:
            opening = self.__dict__["_screen_opening"]
        except KeyError:
            message = f"{type(self).__name__}.opening is unavailable before show() resolves its invocation"
            raise RuntimeError(message) from None
        return cast(Invocation, opening)

    def resolve_access(self, opening: Invocation) -> AccessPolicy:
        """Resolve this instance's access policy for `opening`."""
        return Owner(opening.user.id) if self.access is None else self.access

    async def show(
        self,
        source_or_invocation: InvocationSource | Invocation,
        /,
        *,
        parent: MessageRoot | None = None,
        key: Hashable | None = None,
        wait: bool = False,
    ) -> Self | None:
        """Show this instance under its declared root and optional session policy."""
        self._validate_open_arguments(parent=parent, key=key)
        self._claim_show()
        invocation = (
            source_or_invocation
            if isinstance(source_or_invocation, Invocation)
            else await Invocation.of(source_or_invocation)
        )
        object.__setattr__(self, "_screen_opening", invocation)
        access = self.resolve_access(invocation)
        if not callable(getattr(access, "check", None)):
            message = f"{type(self).__name__}.resolve_access() returned {type(access).__name__}, not an access policy"
            raise TypeError(message)
        options = self._message_root_options(invocation)
        if self.session_name is None:
            await invocation.mount(
                self,
                access=access,
                visibility=self.visibility,
                wait=wait,
                **options,
            )
            return self
        result = await invocation.open(
            self,
            self._session_spec(access),
            visibility=self.visibility,
            parent=parent,
            wait=wait,
            key=key,
            **options,
        )
        return self if result else None

    def _claim_show(self) -> None:
        if self.__dict__.get("_screen_show_called", False):
            message = f"{type(self).__name__}.show() has already been called for this instance"
            raise RuntimeError(message)
        object.__setattr__(self, "_screen_show_called", True)

    def _session_spec(self, access: AccessPolicy) -> SessionSpec:
        session_name = self.session_name
        if session_name is None:  # guarded by show; keeps the invariant local for type narrowing
            message = f"{type(self).__name__} declares no session_name"
            raise TypeError(message)
        return SessionSpec(
            session_name,
            scope=self.scope,
            admission=self.admission,
            capacity=self.capacity,
            quota=self.quota,
            domain=self.domain,
            access=lambda _context: access,
        )

    def _message_root_options(self, invocation: Invocation) -> SessionOptions:
        scheduler = invocation.runtime.scheduler if self.follow_topics else None
        options: SessionOptions = {}
        options.update(self.root_options)
        options["timeout"] = self.timeout
        options["scheduler"] = scheduler
        if self.expiry is not None:
            expiry = self.expiry
            if isinstance(expiry, RenewEphemeral) and scheduler is None:
                expiry = PauseUpdates(expiry.warning)
            options["expiry"] = expiry
        return options

    def _validate_open_arguments(self, *, parent: MessageRoot | None, key: Hashable | None) -> None:
        if self.session_name is None and parent is not None:
            message = f"{type(self).__name__} declares no session_name, so parent= cannot apply"
            raise TypeError(message)
        if self.session_name is None and key is not None:
            message = f"{type(self).__name__} declares no session_name, so key= cannot apply"
            raise TypeError(message)
        if parent is not None and key is not None:
            message = "parent= attaches to its existing session and cannot be combined with key="
            raise TypeError(message)

    @classmethod
    def _validate_policy(cls) -> None:
        if cls.session_name is not None and (not isinstance(cls.session_name, str) or not cls.session_name):
            message = f"{cls.__name__}.session_name must be a non-empty string or None"
            raise TypeError(message)
        if not isinstance(cls.scope, ScopeKind):
            message = f"{cls.__name__}.scope must be a ScopeKind"
            raise TypeError(message)
        if cls.capacity is not None and cls.capacity <= 0:
            message = f"{cls.__name__}.capacity must be positive or None"
            raise ValueError(message)
        if cls.quota is not None and cls.quota <= 0:
            message = f"{cls.__name__}.quota must be positive or None"
            raise ValueError(message)
        if cls.domain == "":
            message = f"{cls.__name__}.domain must be a non-empty string or None"
            raise ValueError(message)
        if cls.session_name is None:
            session_policy = {
                "scope": cls.scope is not ScopeKind.USER,
                "admission": cls.admission != DEFAULT_ADMISSION,
                "capacity": cls.capacity is not None,
                "quota": cls.quota is not None,
                "domain": cls.domain is not None,
            }
            unused = tuple(name for name, configured in session_policy.items() if configured)
            if unused:
                message = (
                    f"{cls.__name__} declares no session_name, so its session policy cannot apply: {', '.join(unused)}"
                )
                raise TypeError(message)
        if cls.visibility not in ("public", "personal") and not isinstance(cls.visibility, Private):
            message = f"{cls.__name__}.visibility must be 'public', 'personal', or Private"
            raise TypeError(message)
        if not isinstance(cls.root_options, dict):
            message = f"{cls.__name__}.root_options must be a dict"
            raise TypeError(message)
        cls.root_options = cast(SessionOptions, dict(cls.root_options))
        duplicates = _DEDICATED_ROOT_OPTIONS.intersection(cls.root_options)
        if duplicates:
            names = ", ".join(sorted(duplicates))
            message = f"{cls.__name__}.root_options repeats dedicated Screen policy: {names}"
            raise TypeError(message)


__all__ = ["Screen"]
