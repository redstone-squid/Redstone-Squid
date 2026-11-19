"""One endpoint registry for addressable UI operations and screen openings."""

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Concatenate, cast, get_type_hints, overload

from squid_ui.errors import LayoutInvariantError
from squid_ui.routing.address import AddressParts, AddressRoute, InvalidAddress
from squid_ui.routing.pattern import RouteTable

type Proceed = Callable[[], Awaitable[Any]]
type Middleware[Context] = Callable[[Context, RouteMatch[Context], Proceed], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class RouteMatch[Context]:
    endpoint: Endpoint[Context, ...]
    arguments: Mapping[str, Any]


class Endpoint[Context, **P]:
    """A stable, typed endpoint. Direct calls use the same middleware as address dispatch."""

    def __init__(
        self,
        router: Router[Context],
        path: str,
        handler: Any,
        fragment: str | None,
        policy: object | None,
        group: RouteGroup[Context] | None,
    ) -> None:
        self.router = router
        self.path = path
        self.group = group
        self.owner: type[Any] | None = None
        self._bind(handler, fragment, policy)

    def _bind(self, handler: Any, fragment: str | None, policy: object | None) -> None:
        is_classmethod = isinstance(handler, classmethod)
        function = handler.__func__ if is_classmethod else handler
        signature = inspect.signature(function)
        parameters = tuple(signature.parameters.values())[2 if is_classmethod else 1 :]
        address = AddressRoute(self.path, inspect.Signature(parameters), get_type_hints(function), fragment=fragment)
        if hasattr(self, "address_route") and (
            address.signature != self.address_route.signature or fragment != self.address_route.fragment
        ):
            message = "Reloading an endpoint cannot change its address parameters"
            raise ValueError(message)
        self.address_route = address
        self.policy = policy
        self.function = function
        self.is_classmethod = is_classmethod

    def __set_name__(self, owner: type[Any], name: str) -> None:
        self.owner = owner

    def address(self, *args: P.args, **kwargs: P.kwargs) -> str:
        return self.address_route.build(*args, **kwargs)

    def custom_id(self, *args: P.args, **kwargs: P.kwargs) -> str:
        """Encode the canonical address for a persistent control."""
        custom_id = "ui:" + self.address(*args, **kwargs)
        if len(custom_id) > 100:
            message = "Persistent control address exceeds the 100-character custom ID limit"
            raise LayoutInvariantError(message)
        return custom_id

    async def __call__(self, request: Context, /, *args: P.args, **kwargs: P.kwargs) -> Any:
        arguments = self.address_route.arguments(args, kwargs)
        return await self.router.dispatch(request, RouteMatch(self, arguments))


@dataclass(frozen=True, slots=True)
class _Declare[Context]:
    router: Router[Context]
    path: str
    fragment: str | None
    policy: object | None
    group: RouteGroup[Context] | None = None

    @overload
    def __call__[**P](
        self, handler: Callable[Concatenate[type[Any], Context, P], Awaitable[Any]], /
    ) -> Endpoint[Context, P]: ...

    @overload
    def __call__[**P](self, handler: Callable[Concatenate[Context, P], Awaitable[Any]], /) -> Endpoint[Context, P]: ...

    def __call__(self, handler: Any, /) -> Endpoint[Context, ...]:
        return self.router._register(self.path, handler, self.fragment, self.policy, self.group)


class Router[Context]:
    """Own endpoint identities, typed addresses, and the middleware invocation chain.

    Freeze after importing endpoint modules. Redeclaration replaces handlers on stable
    handles, but cannot change address parameters or add endpoints after freezing.
    Adapters supply request contexts and middleware for acknowledgement and delivery.
    """

    def __init__(self) -> None:
        self._endpoints: dict[str, Endpoint[Context, ...]] = {}
        self._table = RouteTable[Endpoint[Context, ...]]()
        self._middleware: list[Middleware[Context]] = []
        self._groups: dict[str, RouteGroup[Context]] = {}
        self._frozen = False

    def route(self, path: str, *, fragment: str | None = None, policy: object | None = None) -> _Declare[Context]:
        return _Declare(self, path, fragment, policy)

    def group(self, prefix: str) -> RouteGroup[Context]:
        if prefix not in self._groups:
            if self._frozen:
                message = "Routes are frozen"
                raise RuntimeError(message)
            if not prefix.startswith("/") or prefix.endswith("/"):
                message = "Group prefixes must start with / and have no trailing slash"
                raise ValueError(message)
            self._groups[prefix] = RouteGroup(self, prefix)
        return self._groups[prefix]

    def add_middleware(self, middleware: Middleware[Context]) -> None:
        if self._frozen:
            message = "Routes are frozen"
            raise RuntimeError(message)
        self._middleware.append(middleware)

    def _register(
        self, path: str, handler: Any, fragment: str | None, policy: object | None, group: RouteGroup[Context] | None
    ) -> Endpoint[Context, ...]:
        if endpoint := self._endpoints.get(path):
            endpoint._bind(handler, fragment, policy)
            return endpoint
        if self._frozen:
            message = "Routes are frozen"
            raise RuntimeError(message)
        endpoint = Endpoint(self, path, handler, fragment, policy, group)
        self._table.add(endpoint.address_route.pattern, endpoint)
        self._endpoints[path] = endpoint
        return endpoint

    def freeze(self) -> None:
        self._frozen = True

    def endpoints(self) -> tuple[Endpoint[Context, ...], ...]:
        return tuple(self._endpoints.values())

    def resolve(self, address: str) -> RouteMatch[Context]:
        parts = AddressParts.parse(address)
        found = self._table.match(parts.path[1:])
        if found is None:
            message = "Unknown UI address"
            raise InvalidAddress(message)
        endpoint, arguments = found
        return RouteMatch(endpoint, endpoint.address_route.decode(parts, arguments))

    async def dispatch(self, request: Context, match: RouteMatch[Context]) -> Any:
        endpoint = match.endpoint
        if endpoint.router is not self:
            message = "Endpoint belongs to another router"
            raise ValueError(message)
        function, owner, is_classmethod = endpoint.function, endpoint.owner, endpoint.is_classmethod
        callback = cast(Callable[..., Awaitable[Any]], function)
        middleware = (*self._middleware, *(endpoint.group.middleware if endpoint.group is not None else ()))

        async def invoke(index: int) -> Any:
            if index == len(middleware):
                if is_classmethod:
                    if owner is None:
                        message = "Class endpoint has not been bound to its owner"
                        raise RuntimeError(message)
                    return await callback(owner, request, **match.arguments)
                return await callback(request, **match.arguments)
            called = False
            active = True

            async def proceed() -> Any:
                nonlocal called
                if called or not active:
                    message = "Middleware proceed must be called once during its invocation"
                    raise RuntimeError(message)
                called = True
                return await invoke(index + 1)

            try:
                return await middleware[index](request, match, proceed)
            finally:
                active = False

        return await invoke(0)


class RouteGroup[Context]:
    """A path prefix and middleware within the owning router's registry."""

    def __init__(self, router: Router[Context], prefix: str) -> None:
        self.router = router
        self.prefix = prefix
        self.middleware: list[Middleware[Context]] = []
        self._middleware_keys: dict[str, int] = {}

    def route(self, path: str, *, fragment: str | None = None, policy: object | None = None) -> _Declare[Context]:
        return _Declare(self.router, self.prefix + path, fragment, policy, self)

    def add_middleware(self, middleware: Middleware[Context], *, key: str | None = None) -> None:
        if key is not None and key in self._middleware_keys:
            self.middleware[self._middleware_keys[key]] = middleware
            return
        if self.router._frozen:
            message = "Routes are frozen"
            raise RuntimeError(message)
        if key is not None:
            self._middleware_keys[key] = len(self.middleware)
        self.middleware.append(middleware)
