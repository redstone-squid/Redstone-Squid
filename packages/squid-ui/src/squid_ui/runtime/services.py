"""Run shared services for as long as their owner needs them."""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable
from contextvars import Context, copy_context
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import anyio
    from anyio.abc import TaskGroup, TaskStatus

_logger = logging.getLogger(__name__)
type ServiceFailure = Callable[[Exception], Awaitable[None]]


class ManagedService(ABC):
    """A service that runs until ``run`` returns or its owner cancels it.

    Keep construction cheap and put subscriptions, connections, and background work in
    ``run``. The owner starts that work when it's needed and cancels it when it's done.

    Each instance belongs to one owner for life. You can pass it around freely to share
    its state. On remount, the same instance gets a new run, with fresh resources.
    """

    _service_owner: object | None = None

    @abstractmethod
    async def run(self, *, task_status: TaskStatus[None]) -> None:
        """Set up the service, call ``task_status.started()``, then do its work.

        Use context managers for resources and AnyIO task groups for child tasks. They
        keep cleanup in the same place as setup. Put async cleanup inside a shielded
        cancel scope so it can finish when the owner cancels the run.

        Returning ends this run. A later remount can start another one. Returning before
        calling ``started()`` counts as a setup failure.
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
        import anyio

        self._runner_id = anyio.get_current_task().id
        try:
            with anyio.CancelScope() as scope:
                self._scope = scope
                if self._cancelled:
                    scope.cancel()
                if previous is not None:
                    await previous._done.wait()
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
        import anyio

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
        if self._runner_id is not None and _is_current_task_within({self._runner_id}):
            msg = "service activity cannot close its own host"
            raise RuntimeError(msg)
        self._closed = True
        self._stop.set()
        if self._tasks is not None:
            await self._done.wait()

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
            import anyio

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

    After cancelling, call ``wait`` to wait for cleanup.
    """

    def __init__(self) -> None:
        self._token = object()
        self._services: list[ManagedService] = []
        self._current: dict[int, _ServiceActivation] = {}
        self._retired: list[_ServiceActivation] = []
        self._sealed = False
        self._active = False

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

        After cancellation, each new run waits for its previous run's cleanup. Call
        ``wait_ready`` when you need to wait for setup too.

        Args:
            host: A running host to supervise the services.
            on_error: Called after a failed run has finished cleaning up. By default,
                the host logs the error. ``wait`` also reports errors to its caller.
        """
        host._require_running()
        self.seal()
        if self._active:
            return
        self._active = True
        for service in self._services:
            self._current[id(service)] = host._start(service, self._current.get(id(service)), on_error)

    def cancel(self) -> None:
        """Ask all current runs to stop. The services stay registered for reuse."""
        if not self._active:
            return
        self._active = False
        for activation in self._current.values():
            activation.cancel()
            self._retired.append(activation)

    async def wait(self) -> None:
        """Wait for cancelled runs to finish cleaning up.

        This waits for the runs that were cancelled when the call began. Errors are
        collected and raised together once those runs finish.
        """
        if self._owns_retired_activity():
            message = "service activity cannot await its own owner's shutdown"
            raise RuntimeError(message)
        pending = tuple(self._retired)
        errors: list[Exception] = []
        for activation in pending:
            try:
                await activation.wait()
            except Exception as error:
                errors.append(error)
        self._retired = [activation for activation in self._retired if activation not in pending or activation._errors]
        if errors:
            msg = "owned services failed"
            raise ExceptionGroup(msg, errors)

    def _owns_retired_activity(self) -> bool:
        return _owns_current_activity(self._retired)

    async def wait_ready(self) -> None:
        """Wait for the current runs to finish setup.

        An error ends the wait. The other services keep running until the owner or its
        error handler cancels them. Cancelling the wait also leaves the services running.
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

    def validate(self) -> ServiceHost:
        """Check that services have a running host before delivering the tree."""
        if self.host is None:
            message = "components with owned services require a ServiceHost"
            raise RuntimeError(message)
        self.host._require_running()
        return self.host

    def activate(self, owner: ServiceOwner) -> None:
        owner.activate(self.validate(), on_error=self.on_error)
        self._active.add(owner)

    def cancel(self, owner: ServiceOwner) -> None:
        owner.cancel()
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
        pending = tuple(self._retired)
        errors: list[Exception] = []
        for owner in pending:
            try:
                await owner.wait()
            except Exception as error:
                errors.append(error)
        # Release owners whose cleanup finished, even if another owner failed.
        self._retired.difference_update(owner for owner in pending if not owner._retired)
        if errors:
            message = "component service shutdown failed"
            raise ExceptionGroup(message, errors)


def _owns_current_activity(activations: Iterable[_ServiceActivation]) -> bool:
    return _is_current_task_within(
        {activation._runner_id for activation in activations if activation._runner_id is not None}
    )


def _is_current_task_within(runner_ids: set[int]) -> bool:
    if not runner_ids:
        return False
    import anyio

    # Task groups decide ownership. Context variables come from whoever submits the
    # work, which can be a service handing a job to an application task group (or vice versa).
    parents = {task.id: task.parent_id for task in anyio.get_running_tasks()}
    current: int | None = anyio.get_current_task().id
    while current is not None:
        if current in runner_ids:
            return True
        current = parents.get(current)
    return False


async def _wait_ready(activations: tuple[_ServiceActivation, ...]) -> None:
    """Wait for setup in parallel so any failure can wake the caller promptly."""
    if not activations:
        return
    import anyio

    async with anyio.create_task_group() as tasks:
        for activation in activations:
            tasks.start_soon(activation.wait_ready)


__all__ = ["ManagedService", "ServiceHost", "ServiceOwner"]
