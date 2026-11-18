"""Declare and load Discord extensions with application-owned dependencies."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar, cast

import anyio
from discord.ext import commands

logger = logging.getLogger(__name__)
ExportT = TypeVar("ExportT", covariant=True, default=None)


@dataclass(frozen=True, slots=True)
class ExtensionSpec(Generic[ExportT]):
    """Describe an extension without importing its Discord entry point."""

    id: str
    module: str
    requires: tuple[str, ...] = ()


type Extension = str | ExtensionSpec[object]
type ExtensionOperation = Callable[[str], Awaitable[None]]


class ExtensionDependencyError(commands.ExtensionError):
    """An extension's declared dependencies or lifecycle cannot be satisfied."""


class ExtensionContext[Exports]:
    """Resolve and publish exports during one extension's setup."""

    def __init__(self, manager: ExtensionManager, spec: ExtensionSpec[Exports]) -> None:
        self._manager = manager
        self._spec = spec

    def require[T](self, dependency: ExtensionSpec[T]) -> T:
        """Return the exports of a declared dependency that has finished loading."""
        return self._manager.require(self._spec, dependency)

    def publish(self, exports: Exports) -> None:
        """Publish the extension's exports for later consumers."""
        self._manager.publish(self._spec, exports)


class ExtensionManager:
    """Load a fixed dependency graph and let untracked extensions use Discord's lifecycle."""

    def __init__(self, extensions: Sequence[Extension]) -> None:
        self._specs: dict[str, ExtensionSpec[object]] = {}
        self._managed: dict[str, ExtensionSpec[object]] = {}
        modules: set[str] = set()
        for extension in extensions:
            module = extension if isinstance(extension, str) else extension.module
            if module in modules:
                msg = f"Duplicate extension module {module}"
                raise ExtensionDependencyError(msg, name=module)
            modules.add(module)
            if isinstance(extension, ExtensionSpec):
                if extension.id in self._specs:
                    msg = f"Duplicate extension ID {extension.id}"
                    raise ExtensionDependencyError(msg, name=module)
                self._specs[extension.id] = self._managed[module] = extension

        self._order = self._load_order(extensions)
        self._lifecycle_lock = asyncio.Lock()
        self._started = False
        self._loaded: list[str] = []
        self._exports: dict[str, object] = {}
        self._setting_up: str | None = None

    def _load_order(self, extensions: Sequence[Extension]) -> tuple[str, ...]:
        ordered: dict[str, None] = {}
        pending: list[str] = []

        def visit(extension: ExtensionSpec[object]) -> None:
            if extension.module in ordered:
                return
            if extension.id in pending:
                chain = " -> ".join((*pending, extension.id))
                msg = f"Extension dependency cycle: {chain}"
                raise ExtensionDependencyError(msg, name=extension.module)
            pending.append(extension.id)
            for dependency in extension.requires:
                provider = self._specs.get(dependency)
                if provider is None:
                    chain = " -> ".join((*pending, dependency))
                    msg = f"Missing enabled extension: {chain}"
                    raise ExtensionDependencyError(msg, name=extension.module)
                visit(provider)
            pending.pop()
            ordered[extension.module] = None

        for extension in extensions:
            if isinstance(extension, str):
                ordered[extension] = None
            else:
                visit(extension)
        return tuple(ordered)

    def check_live_operation(self, module: str) -> None:
        """Require a restart to change a managed extension."""
        if module in self._managed:
            spec = self._managed[module]
            msg = f"Restart the bot to change extension {spec.id}"
            raise ExtensionDependencyError(msg, name=module)

    def setup_context[T](self, spec: ExtensionSpec[T]) -> ExtensionContext[T]:
        """Open the setup context for the extension currently being loaded."""
        self._check_setup(spec)
        return ExtensionContext(self, spec)

    def _check_setup(self, spec: ExtensionSpec[object]) -> None:
        if self._setting_up != spec.module or self._managed.get(spec.module) is not spec:
            msg = f"Extension {spec.id} has no active setup"
            raise ExtensionDependencyError(msg, name=spec.module)

    def require[T](self, spec: ExtensionSpec[object], dependency: ExtensionSpec[T]) -> T:
        self._check_setup(spec)
        if dependency.id not in spec.requires or self._specs.get(dependency.id) is not dependency:
            msg = f"Extension {spec.id} does not require {dependency.id}"
            raise ExtensionDependencyError(msg, name=spec.module)
        if dependency.id not in self._exports:
            msg = f"Extension {dependency.id} did not publish exports"
            raise ExtensionDependencyError(msg, name=spec.module)
        return cast(T, self._exports[dependency.id])

    def publish[T](self, spec: ExtensionSpec[T], exports: T) -> None:
        self._check_setup(spec)
        if spec.id in self._exports:
            msg = f"Extension {spec.id} already published exports"
            raise ExtensionDependencyError(msg, name=spec.module)
        self._exports[spec.id] = exports

    async def start(self, load: ExtensionOperation, unload: ExtensionOperation) -> None:
        """Load the enabled graph, rolling back this startup if a load fails."""
        async with self._lifecycle_lock:
            if self._started:
                return
            loaded: list[str] = []
            try:
                for module in self._order:
                    self._setting_up = module
                    await load(module)
                    loaded.append(module)
            except BaseException:
                self._setting_up = None
                with anyio.CancelScope(shield=True):
                    await self._unload(reversed(loaded), unload)
                self._exports.clear()
                raise
            finally:
                self._setting_up = None
            self._loaded = [module for module in loaded if module in self._managed]
            self._started = True

    async def close(self, unload: ExtensionOperation) -> None:
        """Tear down managed consumers before their providers."""
        async with self._lifecycle_lock:
            await self._unload(reversed(self._loaded), unload)
            self._loaded.clear()
            self._exports.clear()

    @staticmethod
    async def _unload(modules: Iterable[str], unload: ExtensionOperation) -> None:
        for module in modules:
            try:
                await unload(module)
            except Exception:
                logger.exception("Extension teardown failed", extra={"squid.extension.module": module})
