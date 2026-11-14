"""Command decorators that hand the handler a `Request` and return discord.py's own objects.

`sd.command` is `app_commands.command` plus request injection: it returns the native
`Command`, so cog scanning, checks, groups and every other discord.py decorator keep working.
Unknown keyword arguments pass straight through, which is what keeps the kwargs TypedDicts
open.
"""

import annotationlib
import asyncio
import inspect
import weakref
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial, wraps
from typing import Any, Concatenate, Protocol, Unpack, cast, get_origin, overload, override

import discord
from discord import app_commands
from discord.ext import commands
from typing_extensions import TypedDict

from squid_reactivity.operations import Cancelled, Failed, Pending, Succeeded
from squid_ui import paragraph
from squid_ui.forms import FormSpec
from squid_ui_discord._pending import _PendingCommand
from squid_ui_discord.access import Everyone
from squid_ui_discord.command_options import CommandText, Option, apply_options, command_text
from squid_ui_discord.contracts import DocumentContent, FacadeContent, RequestSource
from squid_ui_discord.modal import ModalSpec
from squid_ui_discord.request import Deferral, Request, _request_for_binding
from squid_ui_discord.response import Abandoned, Presented, Rejected, Response, ResponseResult, Sent

type CommandResult = (
    FacadeContent[Any] | Response[Any] | FormSpec | ModalSpec | discord.ui.Modal | ResponseResult | None
)
type ChoiceValue = str | int | float
type AutocompleteItem = app_commands.Choice[ChoiceValue] | tuple[str, ChoiceValue]
type AsyncHandler = Callable[..., Awaitable[object]]
type PendingCard = DocumentContent | str
"""What a command shows while it runs; a string becomes one paragraph."""

_SETTLED_RESULTS = (Sent, Presented, Rejected, Abandoned)
_MENU_TYPES = (discord.AppCommandType.message, discord.AppCommandType.user)


class NativeCommandKwargs(TypedDict, total=False, extra_items=object):
    """Keywords forwarded to `app_commands.command`; unknown ones pass through unchecked."""

    name: CommandText
    description: CommandText
    nsfw: bool
    auto_locale_strings: bool
    extras: dict[Any, Any]


class NativeHybridKwargs(TypedDict, total=False, extra_items=object):
    """Keywords forwarded to `commands.hybrid_command` / `commands.hybrid_group`."""

    name: str | app_commands.locale_str
    description: str
    with_app_command: bool
    aliases: list[str] | tuple[str, ...]
    help: str
    brief: str
    usage: str
    hidden: bool
    enabled: bool
    fallback: str | app_commands.locale_str


class NativePrefixKwargs(TypedDict, total=False, extra_items=object):
    """Keywords forwarded to `commands.command`."""

    name: str
    aliases: list[str] | tuple[str, ...]
    help: str
    brief: str
    usage: str
    hidden: bool
    enabled: bool


class NativeContextMenuKwargs(TypedDict, total=False, extra_items=object):
    """Keywords forwarded to `app_commands.ContextMenu`.

    `default_permissions` is the one discord.py sets as an attribute rather than accepting
    in the constructor; it is applied the same way here.
    """

    nsfw: bool
    guild_ids: list[int]
    allowed_contexts: app_commands.AppCommandContext
    allowed_installs: app_commands.AppInstallationType
    auto_locale_strings: bool
    extras: dict[Any, Any]
    default_permissions: discord.Permissions | None


class AsyncHandlerTransform(Protocol):
    """A decorator that preserves an async handler's signature."""

    def __call__[**P, T](self, handler: Callable[P, Awaitable[T]], /) -> Callable[P, Awaitable[T]]: ...


type _Binding = app_commands.Group | commands.Cog


# The decorator, not the factory, carries the overloads: `sd.command()` takes no argument that
# could pick one, so the choice between the method and free-function forms is made when the
# handler is applied.
class CommandDecorator(Protocol):
    """What `sd.command(...)` returns; a method's `Request` is typed by its binding."""

    @overload
    def __call__[OwnerT: _Binding, ClientT: discord.Client, **P](
        self,
        callback: Callable[Concatenate[OwnerT, Request[OwnerT, ClientT], P], Awaitable[CommandResult]],
        /,
    ) -> app_commands.Command[OwnerT, P, None]: ...
    @overload
    def __call__[ClientT: discord.Client, **P](
        self,
        callback: Callable[Concatenate[Request[Any, ClientT], P], Awaitable[CommandResult]],
        /,
    ) -> app_commands.Command[Any, P, None]: ...


class HybridCommandDecorator(Protocol):
    """What `sd.hybrid_command(...)` returns."""

    @overload
    def __call__[OwnerT: commands.Cog, ClientT: discord.Client, **P](
        self,
        callback: Callable[Concatenate[OwnerT, Request[OwnerT, ClientT], P], Awaitable[CommandResult]],
        /,
    ) -> commands.HybridCommand[OwnerT, P, None]: ...
    @overload
    def __call__[ClientT: discord.Client, **P](
        self,
        callback: Callable[Concatenate[Request[Any, ClientT], P], Awaitable[CommandResult]],
        /,
    ) -> commands.HybridCommand[Any, P, None]: ...


class PrefixCommandDecorator(Protocol):
    """What `sd.prefix_command(...)` returns."""

    @overload
    def __call__[OwnerT: commands.Cog, ClientT: discord.Client, **P](
        self,
        callback: Callable[Concatenate[OwnerT, Request[OwnerT, ClientT], P], Awaitable[CommandResult]],
        /,
    ) -> commands.Command[OwnerT, P, None]: ...
    @overload
    def __call__[ClientT: discord.Client, **P](
        self,
        callback: Callable[Concatenate[Request[Any, ClientT], P], Awaitable[CommandResult]],
        /,
    ) -> commands.Command[Any, P, None]: ...


@dataclass(frozen=True, slots=True)
class CommandPolicy:
    """How a command acknowledges and reports; `None` fields inherit from the enclosing group."""

    defer: Deferral | None = None
    pending: PendingCard | None = None

    def overlay(self, other: CommandPolicy) -> CommandPolicy:
        """Overlay non-None fields; None inherits rather than clearing an enclosing policy."""
        return replace(
            self,
            defer=self.defer if other.defer is None else other.defer,
            pending=self.pending if other.pending is None else other.pending,
        )


@dataclass(frozen=True, slots=True)
class ContextMenuDeclaration:
    """One context-menu declaration, attached to the unbound callback until cog load."""

    name: str | app_commands.locale_str
    type: discord.AppCommandType
    policy: CommandPolicy
    native: NativeContextMenuKwargs


_CONTEXT_MENUS: weakref.WeakKeyDictionary[AsyncHandler, ContextMenuDeclaration] = weakref.WeakKeyDictionary()


def _source_parameter(callback: Callable[..., object]) -> tuple[list[inspect.Parameter], int]:
    signature = inspect.signature(callback, annotation_format=annotationlib.Format.FORWARDREF)
    parameters = list(signature.parameters.values())
    source_index = 1 if parameters and parameters[0].name in {"self", "cls"} else 0
    if source_index >= len(parameters):
        message = "a Squid command needs Request in the native source slot"
        raise TypeError(message)
    parameter = parameters[source_index]
    if get_origin(parameter.annotation) is not Request and parameter.annotation is not Request:
        message = "Request is only supported in the native source slot"
        raise TypeError(message)
    return parameters, source_index


def _external_signature(callback: Callable[..., object], source_type: type) -> inspect.Signature:
    """The signature discord.py sees: the request slot re-typed as the native source."""
    parameters, source_index = _source_parameter(callback)
    parameters[source_index] = parameters[source_index].replace(annotation=source_type)
    # An explicit __signature__ bypasses discord.py's usual Parameter construction.
    # Prefix parsing and help need its converter and display metadata properties.
    parameters = [
        parameter
        if isinstance(parameter, commands.Parameter)
        else commands.Parameter(
            name=parameter.name,
            kind=parameter.kind,
            default=parameter.default,
            annotation=parameter.annotation,
        )
        for parameter in parameters
    ]
    return inspect.Signature(parameters, return_annotation=inspect.Signature.empty)


def _as_request_source(value: object) -> RequestSource:
    if isinstance(value, discord.Interaction | commands.Context):
        return value
    if hasattr(value, "response") and hasattr(value, "followup") and hasattr(value, "user"):
        return cast(RequestSource, value)
    if callable(getattr(value, "send", None)) and hasattr(value, "author"):
        return cast(RequestSource, value)
    message = "discord.py supplied neither an Interaction nor a Context"
    raise TypeError(message)


def _group_policy(req: Request[Any, Any]) -> CommandPolicy:
    """The policy inherited down the invoked command's parent chain, outermost first."""
    chain: list[CommandPolicy] = []
    node: object = req.command
    while node is not None:
        policy = getattr(node, "policy", None)
        if isinstance(node, Group | HybridGroup) and isinstance(policy, CommandPolicy):
            chain.append(policy)
        node = getattr(node, "parent", None)
    inherited = CommandPolicy()
    for policy in reversed(chain):
        inherited = inherited.overlay(policy)
    return inherited


async def present_return(req: Request[Any, Any], result: CommandResult) -> ResponseResult | None:
    """Present a handler's content unless it already returned a settled outcome or None.

    Returning new content after responding through the request raises instead of sending
    a second implicit response. Forms must still have an unused initial response slot.
    """
    if result is None or isinstance(result, _SETTLED_RESULTS):
        return result
    if req.responded:
        message = "a handler explicitly responded and also returned response content"
        raise RuntimeError(message)
    if isinstance(result, FormSpec | ModalSpec | discord.ui.Modal):
        await req.form(result)
        return None
    return await req.respond(result)


async def _run_pending(req: Request[Any, Any], card: PendingCard, work: Callable[[], Awaitable[object]]) -> None:
    """Show `card` at once, run `work` behind it, and replace it with the returned document.

    The error policy from `Config.errors` renders and observes a failure; the failure is
    re-raised afterwards so discord.py's error handling still sees it.
    """
    errors = req.runtime.config.errors
    component = _PendingCommand(
        cast(Callable[[], Awaitable[DocumentContent | None]], work),
        initial=paragraph(card) if isinstance(card, str) else card,
        render_error=None if errors.render is None else partial(errors.render, req),
    )
    result = await req.respond(component, access=Everyone())
    match component.execution.status:
        case Succeeded():
            return
        case Failed(error=error):
            if errors.observe is not None:
                delivery = result.delivery if isinstance(result, Presented) else None
                await errors.observe(req, error, delivery)
            raise error
        case Cancelled():
            raise asyncio.CancelledError
        case Pending():
            message = "pending command remained pending after mount settlement"
            raise RuntimeError(message)


async def _dispatch(
    callback: AsyncHandler,
    own: CommandPolicy,
    source_index: int,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> None:
    if len(args) <= source_index:
        message = "discord.py did not provide the command source"
        raise TypeError(message)
    source = _as_request_source(args[source_index])
    # args[0] is Command.binding: the cog for cog commands, the group for class-body groups.
    req = await _request_for_binding(source, args[0] if source_index == 1 else None)
    policy = _group_policy(req).overlay(own)
    if policy.defer is not None:
        await req.defer(policy.defer)
    injected = [*args]
    injected[source_index] = req
    if policy.pending is None:
        await present_return(req, cast(CommandResult, await callback(*injected, **kwargs)))
        return
    await _run_pending(req, policy.pending, lambda: callback(*injected, **kwargs))


def _wrap(
    callback: Callable[..., object], own: CommandPolicy, source_type: type
) -> Callable[..., Coroutine[Any, Any, None]]:
    if not inspect.iscoroutinefunction(callback):
        message = "Squid command handlers must be coroutine functions"
        raise TypeError(message)
    handler = cast(AsyncHandler, callback)
    _, source_index = _source_parameter(callback)

    @wraps(callback)
    async def outward(*args: object, **kwargs: object) -> None:
        await _dispatch(handler, own, source_index, args, kwargs)

    outward.__signature__ = _external_signature(callback, source_type)  # type: ignore[attr-defined]
    return outward


def command(
    *,
    pending: PendingCard | None = None,
    defer: Deferral | None = None,
    options: Mapping[str, Option] | None = None,
    **native: Unpack[NativeCommandKwargs],
) -> CommandDecorator:
    """Build an app command whose source parameter receives a Request.

    ``defer`` acknowledges before the handler runs. ``pending`` shows a card and replaces
    it with the returned document; the handler must return document content or None,
    which leaves the card visible. Both policies inherit from an enclosing Group.
    Names and descriptions accept Fluent messages directly. ``options`` groups metadata by
    Python parameter name; unknown names fail at declaration time. Other keywords pass to discord.py.
    """
    own = CommandPolicy(defer=defer, pending=pending)

    def decorate(callback: Callable[..., Awaitable[CommandResult]]) -> app_commands.Command[Any, ..., None]:
        outward = _wrap(callback, own, discord.Interaction)
        if options is not None:
            parameters, source_index = _source_parameter(callback)
            apply_options(outward, options, {parameter.name for parameter in parameters[source_index + 1 :]})
        metadata = dict(native)
        for key in ("name", "description"):
            if key in metadata:
                metadata[key] = command_text(cast(CommandText, metadata[key]))
        result = app_commands.command(**metadata)(outward)  # type: ignore[arg-type]
        for parameter in result.parameters:
            if parameter.choices and parameter.autocomplete:
                message = f"Command option {parameter.name!r} cannot have both choices and autocomplete"
                raise TypeError(message)
        return result

    return cast(CommandDecorator, decorate)


def hybrid_command(
    *,
    pending: PendingCard | None = None,
    defer: Deferral | None = None,
    **native: Unpack[NativeHybridKwargs],
) -> HybridCommandDecorator:
    """`commands.hybrid_command`, with the handler receiving a `Request` in the context slot."""
    own = CommandPolicy(defer=defer, pending=pending)

    def decorate(callback: Callable[..., Awaitable[CommandResult]]) -> commands.HybridCommand[Any, ..., None]:
        outward = _wrap(callback, own, commands.Context)
        return commands.hybrid_command(**native)(outward)  # type: ignore[arg-type]

    return cast(HybridCommandDecorator, decorate)


def prefix_command(
    *,
    pending: PendingCard | None = None,
    defer: Deferral | None = None,
    **native: Unpack[NativePrefixKwargs],
) -> PrefixCommandDecorator:
    """`commands.command`, with the handler receiving a `Request` in the context slot.

    `defer` is accepted for symmetry and does nothing: a prefix context has no interaction
    to acknowledge.
    """
    own = CommandPolicy(defer=defer, pending=pending)

    def decorate(callback: Callable[..., Awaitable[CommandResult]]) -> commands.Command[Any, ..., None]:
        outward = _wrap(callback, own, commands.Context)
        return commands.command(**native)(outward)  # type: ignore[arg-type]

    return cast(PrefixCommandDecorator, decorate)


class Group(app_commands.Group):
    """`app_commands.Group` whose members inherit `defer` and `pending` unless they set their own.

    Both forms work: an instance as a cog attribute with `@group.command(...)`, or a class body
    of `@sd.command(...)` members with `defer`/`pending` as class attributes. Roots opened by
    members belong to the binding cog, or the app for class-body members, which discord.py
    binds to the group; a group is not a scope owner.
    """

    defer: Deferral | None = None
    pending: PendingCard | None = None
    policy: CommandPolicy

    def __init__(
        self,
        *,
        pending: PendingCard | None = None,
        defer: Deferral | None = None,
        **native: Any,
    ) -> None:
        super().__init__(**native)
        cls = type(self)
        self.policy = CommandPolicy(
            defer=cls.defer if defer is None else defer,
            pending=cls.pending if pending is None else pending,
        )

    @override
    def command(  # pyright: ignore[reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
        self,
        *,
        pending: PendingCard | None = None,
        defer: Deferral | None = None,
        options: Mapping[str, Option] | None = None,
        **native: Unpack[NativeCommandKwargs],
    ) -> CommandDecorator:
        """`sd.command` registered under this group."""

        def decorate(callback: Callable[..., Awaitable[CommandResult]]) -> app_commands.Command[Any, ..., None]:
            member = command(pending=pending, defer=defer, options=options, **native)(callback)
            self.add_command(member)
            return member

        return cast(CommandDecorator, decorate)


class HybridGroup(commands.HybridGroup[Any, ..., Any]):
    """`commands.HybridGroup` whose members inherit `defer` and `pending`."""

    policy: CommandPolicy

    def __init__(self, func: Callable[..., Any], /, *, policy: CommandPolicy, **attrs: Any) -> None:
        super().__init__(func, **attrs)
        self.policy = policy

    @override
    def command(  # pyright: ignore[reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
        self,
        *,
        pending: PendingCard | None = None,
        defer: Deferral | None = None,
        **native: Unpack[NativeHybridKwargs],
    ) -> HybridCommandDecorator:
        """`sd.hybrid_command` registered under this group."""

        def decorate(callback: Callable[..., Awaitable[CommandResult]]) -> commands.HybridCommand[Any, ..., None]:
            native.setdefault("parent", self)  # type: ignore[typeddict-item]
            member = hybrid_command(pending=pending, defer=defer, **native)(callback)
            self.add_command(member)
            return member

        return cast(HybridCommandDecorator, decorate)


def hybrid_group(
    *,
    pending: PendingCard | None = None,
    defer: Deferral | None = None,
    **native: Unpack[NativeHybridKwargs],
) -> Callable[[Callable[..., Awaitable[CommandResult]]], HybridGroup]:
    """Build a Request-injecting hybrid group using discord.py's native invocation rules."""
    own = CommandPolicy(defer=defer, pending=pending)

    def decorate(callback: Callable[..., Awaitable[CommandResult]]) -> HybridGroup:
        outward = _wrap(callback, own, commands.Context)
        return HybridGroup(outward, policy=own, **native)

    return decorate


def context_menu(
    *,
    name: str | app_commands.locale_str,
    type: discord.AppCommandType = discord.AppCommandType.message,
    pending: PendingCard | None = None,
    defer: Deferral | None = None,
    **native: Unpack[NativeContextMenuKwargs],
) -> AsyncHandlerTransform:
    """Declare a cog method as a context menu; `sd.Cog` registers it on load.

    discord.py cannot hold a `ContextMenu` on a cog class, so the declaration waits for the
    bound instance.
    """
    if type not in _MENU_TYPES:
        message = "context menus must target messages or users"
        raise ValueError(message)
    declaration = ContextMenuDeclaration(name, type, CommandPolicy(defer=defer, pending=pending), native)

    def decorate[**HandlerP, HandlerReturnT](
        callback: Callable[HandlerP, Awaitable[HandlerReturnT]],
    ) -> Callable[HandlerP, Awaitable[HandlerReturnT]]:
        _source_parameter(callback)
        _CONTEXT_MENUS[cast(AsyncHandler, callback)] = declaration
        return callback

    return decorate


def context_menu_declaration(callback: AsyncHandler) -> ContextMenuDeclaration | None:
    """Return a callback's context-menu declaration, if it has one."""
    try:
        return _CONTEXT_MENUS.get(callback)
    except TypeError:
        return None


def bind_context_menu(
    owner: object, callback: AsyncHandler, declaration: ContextMenuDeclaration
) -> app_commands.ContextMenu:
    """Build the native `ContextMenu` for one declared method, bound to `owner`."""
    target_type = discord.Message if declaration.type is discord.AppCommandType.message else discord.Member

    async def invoke(interaction: discord.Interaction[discord.Client], target: object) -> None:
        await _dispatch(callback, declaration.policy, 1, (owner, interaction, target), {})

    invoke.__annotations__ = {"interaction": discord.Interaction, "target": target_type, "return": None}
    # discord.py rejects callbacks whose qualified name still looks like an unbound method.
    invoke.__qualname__ = invoke.__name__
    native: dict[str, Any] = dict(declaration.native)
    permissions: discord.Permissions | None = native.pop("default_permissions", None)
    menu = app_commands.ContextMenu(name=declaration.name, callback=invoke, type=declaration.type, **native)
    if permissions is not None:
        menu.default_permissions = permissions
    return menu


def autocomplete[OwnerT, ClientT: discord.Client, **P]() -> Callable[
    [Callable[Concatenate[OwnerT, Request[OwnerT, ClientT], P], Awaitable[Sequence[AutocompleteItem]]]],
    Callable[
        Concatenate[OwnerT, discord.Interaction[discord.Client], P],
        Coroutine[None, None, list[app_commands.Choice[ChoiceValue]]],
    ],
]:
    """Inject a Request and normalize the first 25 autocomplete results in order.

    Results may be native choices or supported label/value pairs. Entries beyond the
    first 25 are discarded before validation.
    """

    def decorate(
        callback: Callable[Concatenate[OwnerT, Request[OwnerT, ClientT], P], Awaitable[Sequence[AutocompleteItem]]],
    ) -> Callable[
        Concatenate[OwnerT, discord.Interaction[discord.Client], P],
        Coroutine[None, None, list[app_commands.Choice[ChoiceValue]]],
    ]:
        dynamic_callback = cast(Callable[..., Awaitable[Sequence[AutocompleteItem]]], callback)
        _, source_index = _source_parameter(callback)

        @wraps(callback)
        async def outward(*args: object, **kwargs: object) -> list[app_commands.Choice[ChoiceValue]]:
            source = _as_request_source(args[source_index])
            req = await _request_for_binding(source, args[0] if source_index == 1 else None)
            injected = [*args]
            injected[source_index] = req
            values = await dynamic_callback(*injected, **kwargs)
            if not isinstance(values, Sequence) or isinstance(values, str | bytes):
                message = "autocomplete must return choices or (label, value) pairs"
                raise TypeError(message)
            choices: list[app_commands.Choice[ChoiceValue]] = []
            for value in values[:25]:
                if isinstance(value, app_commands.Choice):
                    choice = value
                elif isinstance(value, tuple) and len(value) == 2:
                    label, raw = value
                    if not isinstance(label, str) or not isinstance(raw, str | int | float):
                        message = "autocomplete pairs must contain a string label and scalar value"
                        raise TypeError(message)
                    choice = app_commands.Choice(name=label, value=raw)
                else:
                    message = "autocomplete must return choices or (label, value) pairs"
                    raise TypeError(message)
                if len(choice.name) > 100:
                    message = "autocomplete choice labels are limited to 100 characters"
                    raise ValueError(message)
                choices.append(cast(app_commands.Choice[ChoiceValue], choice))
            return choices

        outward.__signature__ = _external_signature(callback, discord.Interaction)  # type: ignore[attr-defined]
        return cast(
            Callable[
                Concatenate[OwnerT, discord.Interaction[discord.Client], P],
                Coroutine[None, None, list[app_commands.Choice[ChoiceValue]]],
            ],
            outward,
        )

    return decorate


__all__ = [
    "AsyncHandler",
    "AsyncHandlerTransform",
    "AutocompleteItem",
    "ChoiceValue",
    "CommandDecorator",
    "CommandPolicy",
    "CommandResult",
    "ContextMenuDeclaration",
    "Group",
    "HybridCommandDecorator",
    "HybridGroup",
    "NativeCommandKwargs",
    "NativeContextMenuKwargs",
    "NativeHybridKwargs",
    "NativePrefixKwargs",
    "PendingCard",
    "PrefixCommandDecorator",
    "autocomplete",
    "bind_context_menu",
    "command",
    "context_menu",
    "context_menu_declaration",
    "hybrid_command",
    "hybrid_group",
    "prefix_command",
    "present_return",
]
