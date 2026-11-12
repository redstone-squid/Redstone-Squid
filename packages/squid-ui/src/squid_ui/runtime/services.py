"""Run shared services for as long as their owner needs them.

A ManagedService describes background work, such as following live updates.
A ServiceOwner decides when that work starts and stops; components use an owner
to tie service runs to their mounted lifetime. UIExecutionHost supplies the task
group that runs those services and waits for their cleanup during shutdown.

The service object can be shared with other components without transferring
ownership. Each activation creates a separate ServiceRun handle, so a waiter can
observe one run even if the service is later stopped and started again.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Generator, Iterable
from contextvars import Context, copy_context
from enum import Enum, auto
from typing import TYPE_CHECKING

from squid_reactivity import Reactive
from squid_reactivity.diagnostics import (
    copy_event_observers,
)
from squid_reactivity.internals import Execution
from squid_ui.runtime.tasks import anyio, task_is_within

if TYPE_CHECKING:
    from anyio.abc import TaskGroup, TaskStatus

_logger = logging.getLogger(__name__)
type ServiceFailure = Callable[[Exception], Awaitable[None]]


class ManagedService(Reactive, ABC):
    """A reactive model with a framework-managed execution lifecycle.

    Like any Reactive subclass, a service can declare state, computed values, and
    resources for consumers to observe. It also implements ``run``, which the
    framework starts and cancels through an explicit owner. Constructing or reading
    the service does not start its work.

    Register the service with ``Component.own`` to tie its work to the component's
    mounted lifetime, or with a ``ServiceOwner`` for an application or session.
    Each instance belongs to one owner for life, but any number of consumers can
    share it. A run ends when ``run`` returns or its owner cancels it. On remount,
    the same instance starts a new run after the previous run finishes cleaning up.

    Reactive state remains readable between runs and follows the usual transaction
    rules: writes inside transactions must target declared state. Readiness and
    ``current_run`` are execution signals; they do not notify reactive readers.

    Keep construction cheap. Acquire subscriptions and connections in ``run`` and
    release them when that run ends. Methods using those resources must check that
    they are available: retaining the service or awaiting readiness does not keep
    them open. See ``run`` for readiness and cancellation-safe cleanup conventions.
    """

    # Ownership and activation are runtime bookkeeping, outside state transactions.
    _reactive_internal_attributes = frozenset({"_service_owner", "_service_run"})

    _service_owner: object | None = None
    _service_run: ServiceRun | None = None

    @property
    def current_run(self) -> ServiceRun | None:
        """Observe the latest activation, or None before activation or after it is released.

        Capture this handle once when waiting on another service. It never switches
        to a later run. Holding it retains that run's outcome, but grants no control
        over its lifetime. Readiness is a milestone, not a lease on live resources.
        """
        return self._service_run

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


class ServiceRunStatus(Enum):
    """The execution state of one service activation."""

    STARTING = auto()
    READY = auto()
    STOPPING = auto()
    COMPLETED = auto()
    CANCELLED = auto()
    FAILED = auto()


class ServiceRun:
    """Observe one service execution, including its readiness and cleanup.

    Waiting never starts work. The owner starts the execution and controls its
    cancellation; this handle continues to refer to that run after a remount.
    """

    def __init__(self) -> None:
        self._runner_id: int | None = None
        self._scope: anyio.CancelScope | None = None
        self._cancelled = False
        self._started = False
        self._ready = asyncio.Event()
        self._execution: Execution[ExceptionGroup | None] = Execution()
        self._errors: list[Exception] = []
        self._failure: ExceptionGroup | None = None

    @property
    def status(self) -> ServiceRunStatus:
        """Return the current execution state; terminal states include completed cleanup."""
        if self._execution.done:
            if self._errors:
                return ServiceRunStatus.FAILED
            return ServiceRunStatus.CANCELLED if self._cancelled else ServiceRunStatus.COMPLETED
        if self._cancelled:
            return ServiceRunStatus.STOPPING
        return ServiceRunStatus.READY if self._started else ServiceRunStatus.STARTING

    @property
    def error(self) -> ExceptionGroup | None:
        """Return the terminal failure, or None while running or after successful cleanup."""
        return self._failure

    def __await__(self) -> Generator[object]:
        """Wait for this run and its cleanup, without starting or cancelling it."""
        return self.wait().__await__()

    def started(self, value: None = None) -> None:
        """Mark setup complete, following AnyIO's task status convention."""
        if self._started:
            message = "service signalled readiness more than once"
            raise RuntimeError(message)
        self._started = True
        self._ready.set()

    def _cancel(self) -> None:
        """Ask the run to stop. Use ``wait`` to wait for cleanup."""
        if self._execution.done:
            return
        self._cancelled = True
        if self._scope is not None:
            self._scope.cancel()

    async def wait(self) -> None:
        """Wait for this run and its cleanup, raising its failure on every failed wait.

        This observes normal completion as well as cancellation. It neither stops
        the service nor consumes the outcome. Cancelling the wait leaves work alone.
        """
        if _owns_current_activity((self,)) and not self._execution.done:
            message = "service activity cannot await its own shutdown"
            raise RuntimeError(message)
        failure = await self._execution.wait()
        if failure is not None:
            raise failure

    async def wait_ready(self) -> None:
        """Wait for setup, raising if the run ends before readiness.

        Cancelling this wait leaves the service running. Readiness does not
        guarantee its resources remain available to subsequent operations.
        """
        if _owns_current_activity((self,)) and not self._ready.is_set():
            message = "service activity cannot await its own readiness"
            raise RuntimeError(message)
        await self._ready.wait()
        if self._cancelled or self._errors or not self._started:
            await self.wait()
            message = "service activation ended before readiness"
            raise RuntimeError(message)

    async def _run(self, service: ManagedService, previous: ServiceRun | None) -> None:
        await self._execution.run(lambda: self._execute(service, previous), lambda: self._failure)

    async def _execute(self, service: ManagedService, previous: ServiceRun | None) -> ExceptionGroup | None:

        self._runner_id = anyio.get_current_task().id
        try:
            with anyio.CancelScope() as scope:
                self._scope = scope
                if self._cancelled:
                    scope.cancel()
                if previous is not None:
                    await previous._execution.wait()
                    previous = None
                await anyio.lowlevel.checkpoint()
                await service.run(task_status=self)
                if not self._started:
                    self._errors.append(RuntimeError("service returned without signalling readiness"))
        except Exception as error:
            self._errors.append(error)
        except BaseException:
            self._cancelled = True
            raise
        finally:
            # A quick remove/remount/remove can cancel this run while it's still waiting.
            # Keep it in line so the next run waits for all earlier cleanup to finish.
            with anyio.CancelScope(shield=True):
                if previous is not None:
                    await previous._execution.wait()
            self._scope = None
            self._runner_id = None
            if self._errors:
                self._failure = ExceptionGroup("service activation failed", self._errors)
            self._ready.set()

        return self._failure


class UIExecutionHost:
    """Supervise UI activity until ``close`` or cancellation drains the host.

    Start ``host.run`` in an application task group before opening panels. The host
    runs owned services, post-commit operations, and shutdown work. Closure drains
    accepted operations and cleanup before cancelling remaining service runs.
    """

    def __init__(self) -> None:
        self._tasks: TaskGroup | None = None
        self._runner_id: int | None = None
        self._runs: set[ServiceRun] = set()
        self._pending_callbacks = 0
        self._callbacks_done = asyncio.Event()
        self._callbacks_done.set()
        self._context = Context()
        self._closed = False
        self._stop = asyncio.Event()
        self._done = asyncio.Event()

    def _require_running(self) -> TaskGroup:
        if self._closed or self._tasks is None:
            msg = "UI activity requires a running UIExecutionHost"
            raise RuntimeError(msg)
        return self._tasks

    async def run(self, *, task_status: TaskStatus[None] | None = None) -> None:
        """Serve owners inside the caller's task group.

        Use ``await tasks.start(host.run)`` to wait until the host is ready. Services
        inherit the context from this call. Pass any interaction-specific inputs to the
        service explicitly.
        """

        if self._tasks is not None or self._closed:
            msg = "UI execution host is already running or closed"
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
                        await self._callbacks_done.wait()
                    for run in tuple(self._runs):
                        run._cancel()
        finally:
            self._tasks = None
            self._runner_id = None
            self._context = Context()
            self._closed = True
            self._done.set()

    async def close(self) -> None:
        """Drain accepted operations and cleanup, then stop services and await error handlers."""
        if self._runner_id is not None and task_is_within({self._runner_id}):
            msg = "service activity cannot close its own host"
            raise RuntimeError(msg)
        self._closed = True
        self._stop.set()
        if self._tasks is not None:
            await self._done.wait()

    def start_operation(self, operation: Callable[[], Awaitable[None]]) -> None:
        """Accept an operation while open and run it to completion under the host.

        The callback inherits the host context and the submitting scope's diagnostic
        observers, and is shielded from cancellation.
        Closing the host waits for accepted operations; even host-owned callers
        cannot submit new operations once closure begins. Bound waits that may stall.
        Failures are logged without cancelling unrelated work. Callers needing an
        outcome should capture it in their callback, as PostCommitOperation does.
        """
        self._submit(self._require_running(), operation, kind="operation")

    def start_cleanup(self, cleanup: Callable[[], Awaitable[None]]) -> None:
        """Supervise accepted cleanup, including when its initiating caller is cancelled.

        The callback inherits the host context and the submitting scope's diagnostic
        observers, and runs shielded from cancellation.
        It should bound resource-specific operations that may stall. Closing the
        host waits for accepted callbacks before cancelling remaining service runs.
        Activity already running on this host may submit further cleanup while the
        host drains; other callers must submit before host closure begins. This
        permission never extends to starting new operations. Failures are logged
        without cancelling unrelated work.
        """
        tasks = self._tasks
        if tasks is None or (self._closed and (self._runner_id is None or not task_is_within({self._runner_id}))):
            message = "cleanup requires a running UIExecutionHost"
            raise RuntimeError(message)
        self._submit(tasks, cleanup, kind="cleanup")

    def _submit(self, tasks: TaskGroup, callback: Callable[[], Awaitable[None]], *, kind: str) -> None:
        copy_event_observers(self._context).run(tasks.start_soon, self._run_callback, callback, kind)
        self._pending_callbacks += 1
        self._callbacks_done.clear()

    async def _run_callback(self, callback: Callable[[], Awaitable[None]], kind: str) -> None:
        try:
            with anyio.CancelScope(shield=True):
                await callback()
        except Exception:
            _logger.exception("host %s failed", kind)
        finally:
            self._pending_callbacks -= 1
            if not self._pending_callbacks:
                self._callbacks_done.set()

    def _start(
        self, service: ManagedService, previous: ServiceRun | None, on_error: ServiceFailure | None
    ) -> ServiceRun:
        tasks = self._require_running()
        run = ServiceRun()
        service._service_run = run
        self._runs.add(run)
        # Mounting can happen inside an interaction transaction. Start from the host's
        # context so the service can keep working after that transaction ends.
        self._context.run(tasks.start_soon, self._serve, service, run, previous, on_error)
        return run

    async def _serve(
        self,
        service: ManagedService,
        run: ServiceRun,
        previous: ServiceRun | None,
        on_error: ServiceFailure | None,
    ) -> None:
        try:
            await run._run(service, previous)
        finally:
            # The run is done now, so an error handler can finish its message root and
            # wait for service cleanup. Shield reporting so host shutdown lets it finish.

            try:
                with anyio.CancelScope(shield=True):
                    if run._errors:
                        error = ExceptionGroup("owned service failed", run._errors)
                        if on_error is None:
                            _logger.error("owned service failed", exc_info=error)
                        else:
                            try:
                                await on_error(error)
                            except Exception:
                                _logger.exception("owned service failure handler failed")
            finally:
                self._runs.discard(run)


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
        self._current: dict[int, ServiceRun] = {}
        self._retired: list[ServiceRun] = []
        self._sealed = False
        self._active = False
        self._host: UIExecutionHost | None = None

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

    def activate(self, host: UIExecutionHost, *, on_error: ServiceFailure | None = None) -> None:
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
        self._retired = [run for run in self._retired if not run._execution.done]
        self._active = True
        self._host = host
        for service in self._services:
            self._current[id(service)] = host._start(service, self._current.get(id(service)), on_error)

    def cancel(self) -> None:
        """Ask all current runs to stop. The services stay registered for reuse."""
        self._cancel_after(())

    def _cancel_after(self, dependencies: tuple[ServiceRun, ...]) -> None:
        if not self._active:
            return
        runs = tuple(self._current.values())
        if dependencies:

            async def cleanup() -> None:
                for dependency in dependencies:
                    await dependency._execution.wait()
                for run in runs:
                    run._cancel()

            assert self._host is not None
            self._host.start_cleanup(cleanup)
        else:
            for run in runs:
                run._cancel()
        self._active = False
        self._host = None
        self._retired.extend(runs)

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

    async def _wait(self, pending: tuple[ServiceRun, ...], observed: frozenset[ServiceRun]) -> None:
        if _owns_current_activity(pending):
            message = "service activity cannot await its own owner's shutdown"
            raise RuntimeError(message)
        errors: list[Exception] = []
        for run in pending:
            try:
                await run.wait()
            except Exception as error:
                if run in observed:
                    errors.append(error)
        self._retired = [run for run in self._retired if run not in pending]
        # Only release captured runs: a concurrent remount may have replaced them.
        self._current = {key: run for key, run in self._current.items() if run not in pending}
        for service in self._services:
            if service._service_run is not None and service._service_run in pending:
                service._service_run = None
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
        await _wait_ready(self._ready_runs())

    def _ready_runs(self) -> tuple[ServiceRun, ...]:
        if not self._active:
            msg = "service owner is not active"
            raise RuntimeError(msg)
        return tuple(self._current.values())


class _ServiceLifecycle:
    """Coordinate services across the committed component tree.

    Descendant runs finish cleanup before an ancestor's services are cancelled, so
    children can still use parent-provided services while shutting down.
    """

    def __init__(self, host: UIExecutionHost | None, on_error: ServiceFailure | None) -> None:
        self.host = host
        self.on_error = on_error
        self._active: set[ServiceOwner] = set()
        self._retired: set[ServiceOwner] = set()
        self._paths: dict[ServiceOwner, str] = {}

    def validate(self) -> UIExecutionHost:
        """Check that services have a running host before delivering the tree."""
        if self.host is None:
            message = "components with owned services require a UIExecutionHost"
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
            run
            for child, child_path in self._paths.items()
            if child is not owner and path is not None and (path == "$" or child_path.startswith(f"{path}."))
            for run in (*child._current.values(), *child._retired)
            if not run._execution.done
        )
        owner._cancel_after(dependencies)
        self._active.discard(owner)
        self._retired.add(owner)

    async def wait_ready(self) -> None:
        # Capture the runs now so a remount during the wait belongs to the next call.
        runs = tuple(run for owner in self._active for run in owner._ready_runs())
        await _wait_ready(runs)

    async def wait(self, *, allow_reentry: bool) -> None:
        if _owns_current_activity(run for owner in self._retired for run in owner._retired):
            if allow_reentry:
                return
            message = "service activity cannot await its own component's shutdown"
            raise RuntimeError(message)
        # Capture every owner's runs before awaiting any cleanup: another waiter or
        # remount may change the owner while an earlier one is still stopping.
        pending = tuple((owner, tuple(owner._retired), frozenset(owner._current.values())) for owner in self._retired)
        errors: list[Exception] = []
        for owner, runs, observed in pending:
            try:
                await owner._wait(runs, observed)
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


def _owns_current_activity(runs: Iterable[ServiceRun]) -> bool:
    return task_is_within({run._runner_id for run in runs if run._runner_id is not None})


async def _wait_ready(runs: tuple[ServiceRun, ...]) -> None:
    """Wait for setup in parallel so any failure can wake the caller promptly."""
    if not runs:
        return
    if _owns_current_activity(run for run in runs if not run._ready.is_set()):
        message = "service activity cannot await its own readiness"
        raise RuntimeError(message)

    async with anyio.create_task_group() as tasks:
        for run in runs:
            tasks.start_soon(run.wait_ready)


__all__ = ["ManagedService", "ServiceOwner", "ServiceRun", "ServiceRunStatus", "UIExecutionHost"]
