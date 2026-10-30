"""Run shared services for as long as their owner needs them."""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable
from contextvars import Context, copy_context
from typing import TYPE_CHECKING

from squid_ui.runtime.tasks import anyio, task_is_within

if TYPE_CHECKING:
    from anyio.abc import TaskGroup, TaskStatus

_logger = logging.getLogger(__name__)
type ServiceFailure = Callable[[Exception], Awaitable[None]]


class ManagedService(ABC):
    """A service that runs until ``run`` returns or its owner cancels it.

    Keep construction cheap and put subscriptions, connections, and background work in
    ``run``. The owner starts that work when it's needed and cancels it when it's done.

    Each instance belongs to one owner for life. You can pass it around freely to share
    its state. On remount, the same instance gets a new run, with fresh resources.
    Service methods manage access to the resources acquired by each run.
    """

    _service_owner: object | None = None

    @abstractmethod
    async def run(self, *, task_status: TaskStatus[None]) -> None:
        """Set up the service, call ``task_status.started()``, then do its work.

        When the component unmounts, its owner cancels this run. Use context managers
        or finally blocks to release the resources it acquired. A later mount runs
        the same service again, after the previous run has finished cleaning up.

        Closing a connection may need to await a response. Since the run is already
        cancelled, put those awaits inside a shielded cancel scope so cleanup can
        finish. Give remote operations a timeout appropriate to the resource::

            connection = await connect()
            try:
                task_status.started()
                await connection.follow_updates()
            finally:
                with anyio.CancelScope(shield=True):
                    with anyio.fail_after(5):
                        await connection.aclose()

        Here the shield lets closing continue after cancellation, and the timeout
        reports a cleanup failure if the connection takes more than five seconds.
        Choose that limit for your connection. If one cleanup step fails, still
        attempt the others; context managers and AsyncExitStack help arrange this.
        Nest child task groups inside the resources they use, so workers finish
        before those resources close.

        Returning ends this run. Returning before calling ``started()`` is a setup
        failure. Register cleanup as each resource is acquired so partial setup can
        unwind too. Work that must finish after the panel disappears belongs to an
        application or session owner that outlives it.
        """


class _ServiceActivation:
    """One service run, ending when it returns or ``cancel`` asks it to stop."""

    def __init__(self) -> None:
        self._runner_id: int | None = None
        self._scope: anyio.CancelScope | None = None
        self._cancelled = False
        self._started = False
        self._ready = asyncio.Event()
        self._done = asyncio.Event()
        self._errors: list[Exception] = []

    def started(self, value: None = None) -> None:
        """Mark setup complete, following AnyIO's task status convention."""
        if self._started:
            message = "service signalled readiness more than once"
            raise RuntimeError(message)
        self._started = True
        self._ready.set()

    def cancel(self) -> None:
        """Ask the run to stop. Use ``wait`` to wait for cleanup."""
        self._cancelled = True
        if self._scope is not None:
            self._scope.cancel()

    async def wait(self) -> None:
        """Wait for cleanup, then raise any errors from the run."""
        if _owns_current_activity((self,)) and not self._done.is_set():
            message = "service activity cannot await its own shutdown"
            raise RuntimeError(message)
        await self._done.wait()
        if self._errors:
            message = "service activation failed"
            raise ExceptionGroup(message, self._errors)

    async def wait_ready(self) -> None:
        """Wait for setup to finish, or raise if the run ends before it's ready."""
        await self._ready.wait()
        if self._cancelled or self._errors or not self._started:
            await self.wait()
            message = "service activation ended before readiness"
            raise RuntimeError(message)

    async def _run(self, service: ManagedService, previous: _ServiceActivation | None) -> None:

        self._runner_id = anyio.get_current_task().id
        try:
            with anyio.CancelScope() as scope:
                self._scope = scope
                if self._cancelled:
                    scope.cancel()
                if previous is not None:
                    await previous._done.wait()
                    previous = None
                await anyio.lowlevel.checkpoint()
                await service.run(task_status=self)
                if not self._started:
                    self._errors.append(RuntimeError("service returned without signalling readiness"))
        except Exception as error:
            self._errors.append(error)
        finally:
            # A quick remove/remount/remove can cancel this run while it's still waiting.
            # Keep it in line so the next run waits for all earlier cleanup to finish.
            with anyio.CancelScope(shield=True):
                if previous is not None:
                    await previous._done.wait()
            self._scope = None
            self._runner_id = None
            self._done.set()
            self._ready.set()


class ServiceHost:
    """Run services until ``close`` or cancellation shuts down the host.

    Start ``host.run`` in an application task group before mounting components that own
    services. Owners decide when each service runs; the host gives those runs a home.
    """

    def __init__(self) -> None:
        self._tasks: TaskGroup | None = None
        self._runner_id: int | None = None
        self._activations: set[_ServiceActivation] = set()
        self._cleanup_tasks = 0
        self._cleanup_done = asyncio.Event()
        self._cleanup_done.set()
        self._context = Context()
        self._closed = False
        self._stop = asyncio.Event()
        self._done = asyncio.Event()

    def _require_running(self) -> TaskGroup:
        if self._closed or self._tasks is None:
            msg = "owned services require a running ServiceHost"
            raise RuntimeError(msg)
        return self._tasks

    async def run(self, *, task_status: TaskStatus[None] | None = None) -> None:
        """Serve owners inside the caller's task group.

        Use ``await tasks.start(host.run)`` to wait until the host is ready. Services
        inherit the context from this call. Pass any interaction-specific inputs to the
        service explicitly.
        """

        if self._tasks is not None or self._closed:
            msg = "service host is already running or closed"
            raise RuntimeError(msg)
        self._runner_id = anyio.get_current_task().id
        try:
            async with anyio.create_task_group() as tasks:
                self._tasks = tasks
                self._context = copy_context()
                if task_status is not None:
                    task_status.started()
                try:
                    await self._stop.wait()
                finally:
                    self._closed = True
                    with anyio.CancelScope(shield=True):
                        await self._cleanup_done.wait()
                    for activation in tuple(self._activations):
                        activation.cancel()
        finally:
            self._tasks = None
            self._runner_id = None
            self._context = Context()
            self._closed = True
            self._done.set()

    async def close(self) -> None:
        """Stop the services and wait for cleanup and error handlers to finish."""
        if self._runner_id is not None and task_is_within({self._runner_id}):
            msg = "service activity cannot close its own host"
            raise RuntimeError(msg)
        self._closed = True
        self._stop.set()
        if self._tasks is not None:
            await self._done.wait()

    def start_cleanup(self, cleanup: Callable[[], Awaitable[None]]) -> None:
        """Supervise accepted cleanup, including when its initiating caller is cancelled.

        The callback inherits the host context and runs shielded from cancellation.
        It should bound resource-specific operations that may stall. Closing the
        host waits for accepted callbacks before cancelling remaining service runs.
        Cleanup already running on this host may submit further cleanup while the
        host drains; other callers must submit before host closure begins.
        """
        tasks = self._tasks
        if tasks is None or (self._closed and (self._runner_id is None or not task_is_within({self._runner_id}))):
            message = "cleanup requires a running ServiceHost"
            raise RuntimeError(message)
        self._context.run(tasks.start_soon, self._run_cleanup, cleanup)
        self._cleanup_tasks += 1
        self._cleanup_done.clear()

    async def _run_cleanup(self, cleanup: Callable[[], Awaitable[None]]) -> None:

        try:
            with anyio.CancelScope(shield=True):
                await cleanup()
        except Exception:
            _logger.exception("host cleanup failed")
        finally:
            self._cleanup_tasks -= 1
            if not self._cleanup_tasks:
                self._cleanup_done.set()

    def _start(
        self, service: ManagedService, previous: _ServiceActivation | None, on_error: ServiceFailure | None
    ) -> _ServiceActivation:
        tasks = self._require_running()
        activation = _ServiceActivation()
        self._activations.add(activation)
        # Mounting can happen inside an interaction transaction. Start from the host's
        # context so the service can keep working after that transaction ends.
        self._context.run(tasks.start_soon, self._serve, service, activation, previous, on_error)
        return activation

    async def _serve(
        self,
        service: ManagedService,
        activation: _ServiceActivation,
        previous: _ServiceActivation | None,
        on_error: ServiceFailure | None,
    ) -> None:
        try:
            await activation._run(service, previous)
        finally:
            # The run is done now, so an error handler can finish its message root and
            # wait for service cleanup. Shield reporting so host shutdown lets it finish.

            try:
                with anyio.CancelScope(shield=True):
                    if activation._errors:
                        error = ExceptionGroup("owned service failed", activation._errors)
                        if on_error is None:
                            _logger.error("owned service failed", exc_info=error)
                        else:
                            try:
                                await on_error(error)
                            except Exception:
                                _logger.exception("owned service failure handler failed")
            finally:
                self._activations.discard(activation)


class ServiceOwner:
    """A set of services whose work stops when you call ``cancel``.

    Components use this internally. Applications and sessions can also use it directly.
    Register services with ``own``, then call ``activate`` when they're needed. The owner
    keeps its services between activations, so it can start them again later.

    An activation lasts until ``cancel``, even if individual runs return or fail sooner.
    After cancelling, call ``wait`` to wait for cleanup.
    """

    def __init__(self) -> None:
        self._token = object()
        self._services: list[ManagedService] = []
        self._current: dict[int, _ServiceActivation] = {}
        self._retired: list[_ServiceActivation] = []
        self._sealed = False
        self._active = False
        self._host: ServiceHost | None = None

    def own[T: ManagedService](self, service: T) -> T:
        """Take ownership of a service and return it for convenient assignment.

        Register services before the first activation. Registering the same instance
        again is fine; registering one that belongs to another owner raises an error.
        """
        if service._service_owner is self._token:
            return service
        if self._sealed:
            msg = "service ownership declarations are already sealed"
            raise RuntimeError(msg)
        if service._service_owner is not None:
            msg = "service already belongs to another owner"
            raise RuntimeError(msg)
        service._service_owner = self._token
        self._services.append(service)
        return service

    def seal(self) -> None:
        """Finish registration. Called before rendering or starting services."""
        self._sealed = True

    def activate(self, host: ServiceHost, *, on_error: ServiceFailure | None = None) -> None:
        """Start each service, or keep its current run if this owner is already active.

        New runs wait for previous cleanup. Repeated activation keeps the original
        runs and error handler, even after they finish. Cancel first to restart or
        move to another host; use ``wait_ready`` to await setup.

        Args:
            host: A running host to supervise the services.
            on_error: Called after a failed run has finished cleaning up. By default,
                the host logs the error. ``wait`` also reports errors to its caller.
        """
        host._require_running()
        if self._active:
            if self._host is not host:
                msg = "cancel the active service owner before activating it on another host"
                raise RuntimeError(msg)
            return
        self.seal()
        self._retired = [activation for activation in self._retired if not activation._done.is_set()]
        self._active = True
        self._host = host
        for service in self._services:
            self._current[id(service)] = host._start(service, self._current.get(id(service)), on_error)

    def cancel(self) -> None:
        """Ask all current runs to stop. The services stay registered for reuse."""
        self._cancel_after(())

    def _cancel_after(self, dependencies: tuple[_ServiceActivation, ...]) -> None:
        if not self._active:
            return
        activations = tuple(self._current.values())
        if dependencies:

            async def cleanup() -> None:
                for dependency in dependencies:
                    await dependency._done.wait()
                for activation in activations:
                    activation.cancel()

            assert self._host is not None
            self._host.start_cleanup(cleanup)
        else:
            for activation in activations:
                activation.cancel()
        self._active = False
        self._host = None
        self._retired.extend(activations)

    async def wait(self) -> None:
        """Wait for cancelled runs to finish cleaning up.

        This waits for the runs that were cancelled when the call began, reporting
        failures from the owner's latest activation at that point. Earlier runs still
        finish cleanup, but their failures belong to their own observers and the error
        handler. Reactivation does not change what an existing waiter observes.

        Completed waits release the cancelled runs, including failures. Concurrent
        waiters each receive their captured results. Cancelling a wait leaves cleanup
        running and allows another caller to wait for it.
        """
        await self._wait(tuple(self._retired), frozenset(self._current.values()))

    async def _wait(self, pending: tuple[_ServiceActivation, ...], observed: frozenset[_ServiceActivation]) -> None:
        if _owns_current_activity(pending):
            message = "service activity cannot await its own owner's shutdown"
            raise RuntimeError(message)
        errors: list[Exception] = []
        for activation in pending:
            try:
                await activation.wait()
            except Exception as error:
                if activation in observed:
                    errors.append(error)
        self._retired = [activation for activation in self._retired if activation not in pending]
        if errors:
            msg = "owned services failed"
            raise ExceptionGroup(msg, errors)

    async def wait_ready(self) -> None:
        """Wait for the current runs to finish setup.

        An error ends the wait. The other services keep running until the owner or its
        error handler cancels them. Cancelling the wait also leaves the services running.
        The wait observes the current runs when it begins; it never follows a remount.
        A service cannot wait for its own readiness before signalling it.
        """
        await _wait_ready(self._ready_activations())

    def _ready_activations(self) -> tuple[_ServiceActivation, ...]:
        if not self._active:
            msg = "service owner is not active"
            raise RuntimeError(msg)
        return tuple(self._current.values())


class _ServiceLifecycle:
    """Track component services until ``cancel`` starts their cleanup."""

    def __init__(self, host: ServiceHost | None, on_error: ServiceFailure | None) -> None:
        self.host = host
        self.on_error = on_error
        self._active: set[ServiceOwner] = set()
        self._retired: set[ServiceOwner] = set()
        self._paths: dict[ServiceOwner, str] = {}

    def validate(self) -> ServiceHost:
        """Check that services have a running host before delivering the tree."""
        if self.host is None:
            message = "components with owned services require a ServiceHost"
            raise RuntimeError(message)
        self.host._require_running()
        return self.host

    def activate(self, owner: ServiceOwner, path: str) -> None:
        self._paths[owner] = path
        owner.activate(self.validate(), on_error=self.on_error)
        self._active.add(owner)

    def cancel(self, owner: ServiceOwner) -> None:
        path = self._paths.get(owner)
        dependencies = tuple(
            activation
            for child, child_path in self._paths.items()
            if child is not owner and path is not None and (path == "$" or child_path.startswith(f"{path}."))
            for activation in (*child._current.values(), *child._retired)
            if not activation._done.is_set()
        )
        owner._cancel_after(dependencies)
        self._active.discard(owner)
        self._retired.add(owner)

    async def wait_ready(self) -> None:
        # Capture the runs now so a remount during the wait belongs to the next call.
        activations = tuple(activation for owner in self._active for activation in owner._ready_activations())
        await _wait_ready(activations)

    async def wait(self, *, allow_reentry: bool) -> None:
        if _owns_current_activity(activation for owner in self._retired for activation in owner._retired):
            if allow_reentry:
                return
            message = "service activity cannot await its own component's shutdown"
            raise RuntimeError(message)
        # Capture every owner's runs before awaiting any cleanup: another waiter or
        # remount may change the owner while an earlier one is still stopping.
        pending = tuple((owner, tuple(owner._retired), frozenset(owner._current.values())) for owner in self._retired)
        errors: list[Exception] = []
        for owner, activations, observed in pending:
            try:
                await owner._wait(activations, observed)
            except Exception as error:
                errors.append(error)
        # Release owners whose cleanup finished, even if another owner failed.
        self._retired.difference_update(owner for owner, _, _ in pending if not owner._retired)
        self._paths = {
            owner: path for owner, path in self._paths.items() if owner in self._active or owner in self._retired
        }
        if errors:
            message = "component service shutdown failed"
            raise ExceptionGroup(message, errors)


def _owns_current_activity(activations: Iterable[_ServiceActivation]) -> bool:
    return task_is_within({activation._runner_id for activation in activations if activation._runner_id is not None})


async def _wait_ready(activations: tuple[_ServiceActivation, ...]) -> None:
    """Wait for setup in parallel so any failure can wake the caller promptly."""
    if not activations:
        return
    if _owns_current_activity(activation for activation in activations if not activation._ready.is_set()):
        message = "service activity cannot await its own readiness"
        raise RuntimeError(message)

    async with anyio.create_task_group() as tasks:
        for activation in activations:
            tasks.start_soon(activation.wait_ready)


__all__ = ["ManagedService", "ServiceHost", "ServiceOwner"]
