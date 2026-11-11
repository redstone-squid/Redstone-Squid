"""Discord screens with reusable policy and owned child flows."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, ClassVar, Unpack, cast

from squid_reactivity.completion import Completion
from squid_reactivity.internals import CURRENT_TRANSACTION
from squid_reactivity.transactions import (
    on_action_commit,
    on_action_rollback,
)
from squid_ui.interactions import ActionEvent as PortableActionEvent
from squid_ui.runtime.component import Component
from squid_ui.runtime.tasks import anyio, task_is_within
from squid_ui.target_types import ComponentsV2Target
from squid_ui_discord.actions import ActionResponder
from squid_ui_discord.audience import Private
from squid_ui_discord.closure import CloseReason
from squid_ui_discord.events import ActionEvent
from squid_ui_discord.request import Request
from squid_ui_discord.response import Abandoned, Presented, Rejected, ResponseOverrides, ResponseSpec, invoker_only
from squid_ui_discord.screen_flow import Cancelled, Completed, ScreenCancelReason, ScreenResultEvent
from squid_ui_discord.session_specs import SessionSpec

if TYPE_CHECKING:
    from squid_ui_discord.facade import Scope
    from squid_ui_discord.message_root import MessageRoot
    from squid_ui_discord.sessions import Session


@dataclass
class _Flow[ResultT, OwnerT]:
    scope: Scope[OwnerT] | None = None
    opening: Request[OwnerT] | None = None
    root: MessageRoot | None = None
    parent: Screen[Any, Any] | None = None
    children: set[Screen[Any, Any]] = field(default_factory=set)
    on_result: Callable[[ScreenResultEvent[ResultT]], Awaitable[None]] | None = None
    on_cancel: Callable[[Cancelled], Awaitable[None]] | None = None
    outcome: Completed[ResultT] | Cancelled | None = None
    done: Completion[Completed[ResultT] | Cancelled] = field(default_factory=Completion)
    claimed: bool = False
    opened: bool = False
    closing: bool = False
    pending: bool = False
    closing_task: int | None = None


class Screen[ResultT = None, OwnerT = Any](Component[ComponentsV2Target, Request[Any]]):
    """A single presentation whose outcome and children end with its owning scope.

    Declare reusable delivery settings in ``policy``. Operational settings omitted there
    inherit installation defaults. ``complete`` and ``cancel`` stage intentions in the
    current action; a rollback leaves the screen open. Child callbacks receive fresh
    parent action authority, never the request that originally opened the child.
    """

    event_frontend: ClassVar[str | None] = "discord"
    policy: ClassVar[ResponseSpec] = ResponseSpec(audience="personal", access=invoker_only)
    __response_spec__: ClassVar[ResponseSpec]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        cls.__response_spec__ = cls._screen_compile_response_spec()

    @classmethod
    def _screen_compile_response_spec(cls) -> ResponseSpec:
        if not isinstance(cls.policy, ResponseSpec):
            msg = "Screen.policy must be a ResponseSpec"
            raise TypeError(msg)
        policy = ResponseSpec(audience="personal", access=invoker_only).overlay(cls.policy)
        if policy.audience not in ("public", "personal") and not isinstance(policy.audience, Private):
            msg = "screen audience must be 'public', 'personal', or Private"
            raise TypeError(msg)
        if (
            policy.access is not None
            and policy.access is not invoker_only
            and not callable(getattr(policy.access, "check", None))
        ):
            msg = "screen access must be an access policy, invoker_only, or None"
            raise TypeError(msg)
        if isinstance(policy.session, SessionSpec) and policy.session.name is None:
            policy = replace(policy, session=replace(policy.session, name=f"{cls.__module__}.{cls.__qualname__}"))
        return policy

    @property
    def _screen_flow(self) -> _Flow[ResultT, OwnerT]:
        # Subclasses need no super().__init__ call, as with Component's own state.
        state = self.__dict__.get("_screen_flow")
        if state is None:
            state = _Flow[ResultT, OwnerT]()
            self.__dict__["_screen_flow"] = state
        return state

    @property
    def owner(self) -> OwnerT:
        scope = self._screen_flow.scope
        if scope is None:
            msg = "screen owner is unavailable before presentation"
            raise RuntimeError(msg)
        return scope.owner

    @property
    def opening(self) -> Request[OwnerT] | None:
        """The opening request, or None for out-of-band delivery."""
        return self._screen_flow.opening

    @property
    def parent(self) -> Screen[Any, Any] | None:
        return self._screen_flow.parent

    @property
    def session(self) -> Session | None:
        """The live session, including during the first load and render."""
        state = self._screen_flow
        if state.scope is None or state.root is None:
            return None
        return state.scope.runtime.sessions.session_for(state.root)

    @property
    def outcome(self) -> Completed[ResultT] | Cancelled | None:
        return self._screen_flow.outcome

    @property
    def close_reason(self) -> CloseReason | None:
        """The owning message's closure cause, available during the final render.

        The screen's outcome separately records its result or cancellation.
        """
        root = self._screen_flow.root
        return None if root is None else root.close_reason

    @property
    def expired(self) -> bool:
        """Whether this screen timed out, including during its final render."""
        return self.close_reason is CloseReason.TIMEOUT

    async def wait(self) -> Completed[ResultT] | Cancelled:
        """Observe closure without cancelling it when this waiter is cancelled."""
        current = CURRENT_TRANSACTION.get()
        if current is not None and not current.closed:
            msg = "wait for a screen outside the action transaction"
            raise RuntimeError(msg)
        state = self._screen_flow
        if state.root is not None:
            state.root._check_shutdown_wait()
        if state.closing_task is not None and task_is_within({state.closing_task}):
            message = "screen closure cannot wait for its own completion"
            raise RuntimeError(message)
        return await state.done.wait()

    async def on_close(self, outcome: Completed[ResultT] | Cancelled) -> None:
        """Run once after owned children close; failures join supervised shutdown."""

    def _screen_claim(self, scope: Scope[OwnerT], opening: Request[OwnerT] | None) -> None:
        state = self._screen_flow
        if state.claimed or state.closing:
            msg = "this Screen instance has already been presented; use one presentation attempt per instance"
            raise RuntimeError(msg)
        if state.parent is not None:
            parent = state.parent._screen_flow
            if parent.scope is not scope or parent.closing or parent.root is None or parent.root.finished:
                msg = "the parent screen is no longer available in this scope"
                raise RuntimeError(msg)
        state.claimed = True
        state.scope = scope
        state.opening = opening

    def _screen_bind(self, root: MessageRoot, session: Session | None) -> None:
        del session  # Session membership is indexed by the host before initial loading.
        state = self._screen_flow
        if not state.claimed or state.closing or state.root is not None:
            msg = "screen cannot bind another root or bind after closure"
            raise RuntimeError(msg)
        state.root = root
        root.on_finish(self._screen_root_finished)
        if state.parent is not None:
            parent_root = state.parent._screen_require_root()
            parent_root._shutdown_dependencies = (*parent_root._shutdown_dependencies, root)

            def detach() -> None:
                parent_root._shutdown_dependencies = tuple(
                    child for child in parent_root._shutdown_dependencies if child is not root
                )

            root._shutdown.add_done_callback(detach)

    def _screen_opened(self) -> None:
        state = self._screen_flow
        parent = state.parent
        if state.closing or (
            parent is not None and (parent._screen_flow.closing or parent._screen_require_root().finished)
        ):
            msg = "the screen closed during presentation"
            raise RuntimeError(msg)
        state.opened = True
        self._screen_require_root()._finishing_hooks.append(self._screen_finish_children_first)

    def _screen_finish_children_first(self, root: MessageRoot) -> None:
        # Installed after session activation so existing session cleanup is composed,
        # not overwritten. Borrowing children must stop before parent services do.
        children = tuple(self._screen_flow.children)
        prior = root._finish_children
        dependencies = tuple(child._screen_flow.root for child in children if child._screen_flow.root is not None)
        root._shutdown_dependencies = tuple(dict.fromkeys((*root._shutdown_dependencies, *dependencies)))

        async def close_children(disable: bool) -> None:
            errors: list[Exception] = []
            for child in children:
                try:
                    await child._screen_close(ScreenCancelReason.PARENT_CLOSED)
                except Exception as error:
                    errors.append(error)
            if prior is not None:
                try:
                    await prior(disable)
                except Exception as error:
                    errors.append(error)
            if errors:
                message = "screen child cleanup failed"
                raise ExceptionGroup(message, errors)

        root._finish_children = close_children

    async def _screen_opening_failed(self) -> None:
        await self._screen_supervised_close(ScreenCancelReason.CLOSED)

    def _screen_require_root(self) -> MessageRoot:
        root = self._screen_flow.root
        if root is None:
            msg = "screen is not bound to a message"
            raise RuntimeError(msg)
        return root

    def _screen_validate_event(self, event: ActionEvent) -> ActionResponder:
        state = self._screen_flow
        root = self._screen_require_root()
        responder = event.responder
        if (
            state.closing
            or root.finished
            or not isinstance(responder, ActionResponder)
            or responder.message_root is not root
            or not responder._action_active
            or event.context.root is not root
            or event.context.scope is not state.scope
        ):
            msg = "screen operations require its current action event"
            raise RuntimeError(msg)
        return responder

    def complete(self, event: ActionEvent, value: ResultT) -> None:
        """Stage a result; publish it only after the action and parent callback succeed."""
        self._screen_stage_outcome(event, Completed(value))

    def cancel(self, event: ActionEvent) -> None:
        """Stage user cancellation after this action commits."""
        self._screen_stage_outcome(event, Cancelled(ScreenCancelReason.USER))

    def _screen_stage_outcome(self, event: ActionEvent, outcome: Completed[ResultT] | Cancelled) -> None:
        responder = self._screen_validate_event(event)
        state = self._screen_flow
        if state.pending or state.outcome is not None:
            msg = "a screen result is already pending or accepted"
            raise RuntimeError(msg)
        state.pending = True

        def rolled_back(*_args: object) -> None:
            state.pending = False

        async def accept() -> None:
            try:
                if state.closing or self._screen_require_root().finished:
                    return
                parent = state.parent
                if isinstance(outcome, Completed) and parent is not None:
                    callback = state.on_result
                    assert callback is not None

                    async def deliver(fresh: PortableActionEvent) -> None:
                        assert isinstance(fresh.context, Request)
                        await callback(
                            ScreenResultEvent(
                                actor=fresh.actor,
                                responder=fresh.responder,
                                locale=fresh.locale,
                                metadata=fresh.metadata,
                                context=fresh.context,
                                value=outcome.value,
                            )
                        )

                        def committed(*_args: object) -> None:
                            # A parent may itself complete in this callback. Publish the
                            # child result at commit before that parent's cleanup begins.
                            if not state.closing:
                                state.outcome = outcome

                        on_action_commit(committed)

                    parent_root = parent._screen_require_root()
                    try:
                        accepted = await parent_root._dispatch_screen_result(responder.interaction, deliver)
                    except BaseException:
                        # Rendering can fail after the parent accepted the result. Its
                        # committed outcome still closes the child and cannot be retried.
                        if state.outcome is outcome:
                            await self._screen_require_root().finish()
                        raise
                    if not accepted:
                        if parent_root.finished or parent._screen_flow.closing:
                            await self._screen_close(ScreenCancelReason.PARENT_CLOSED)
                        return
                state.outcome = outcome
                await self._screen_require_root().finish()
            finally:
                state.pending = False

        try:
            on_action_rollback(rolled_back)
            responder._on_screen_success(accept, name=f"{type(self).__qualname__}.resolve")
        except BaseException:
            state.pending = False
            raise

    async def open_child[ChildResultT](
        self,
        event: ActionEvent,
        child: Screen[ChildResultT, Any],
        *,
        on_result: Callable[[ScreenResultEvent[ChildResultT]], Awaitable[None]],
        on_cancel: Callable[[Cancelled], Awaitable[None]] | None = None,
        **overrides: Unpack[ResponseOverrides],
    ) -> Presented[Screen[ChildResultT, Any]] | Rejected | Abandoned:
        """Open a child with its own admission policy and attach its lifetime to this screen."""
        self._screen_validate_event(event)
        state, child_state = self._screen_flow, child._screen_flow
        if child is self or child_state.parent is not None or child_state.claimed or child_state.closing:
            msg = "a child screen must be a fresh unattached instance"
            raise RuntimeError(msg)
        child_state.parent = self
        child_state.on_result = on_result
        child_state.on_cancel = on_cancel
        state.children.add(child)
        scope = state.scope
        assert scope is not None
        host = scope.runtime.execution_host
        if host is None:
            state.children.discard(child)
            child_state.parent = None
            msg = "screen flows require an execution host"
            raise RuntimeError(msg)

        async def rollback_child() -> None:
            await child._screen_close(ScreenCancelReason.CLOSED)

        on_action_rollback(lambda *_args: host.start_cleanup(rollback_child))
        try:
            result = await event.context.respond(child, **overrides)
        except BaseException as error:
            try:
                with anyio.CancelScope(shield=True):
                    await child._screen_opening_failed()
            except BaseException as cleanup_error:
                message = "child opening and cleanup failed"
                raise BaseExceptionGroup(message, [error, cleanup_error]) from None
            raise
        if isinstance(result, Presented):
            return cast(Presented[Screen[ChildResultT, Any]], result)
        await child._screen_opening_failed()
        assert isinstance(result, Rejected | Abandoned)
        return result

    async def _screen_supervised_close(self, reason: ScreenCancelReason) -> None:
        """Run rejection cleanup outside an opening action's transaction."""
        state = self._screen_flow
        if state.done.done:
            return
        scope = state.scope
        host = None if scope is None else scope.runtime.execution_host
        current = CURRENT_TRANSACTION.get()
        if current is None or current.closed or host is None:
            await self._screen_close(reason)
            return
        done = Completion[Exception | None]()

        async def cleanup() -> None:
            error: Exception | None = None
            try:
                await self._screen_close(reason)
            except Exception as caught:
                error = caught
            finally:
                done.resolve(error)

        host.start_cleanup(cleanup)
        error = await done.wait()
        if error is not None:
            raise error

    async def _screen_close(self, reason: ScreenCancelReason) -> None:
        state = self._screen_flow
        root = state.root
        if state.outcome is None:
            state.outcome = Cancelled(reason)
        if root is not None:
            await root.finish(reason=CloseReason.CLOSED if reason is ScreenCancelReason.USER else CloseReason(reason))
        else:
            await self._screen_finalize()

    async def _screen_root_finished(self, root: MessageRoot) -> None:
        state = self._screen_flow
        if state.outcome is None:
            reason = root.close_reason or CloseReason.CLOSED
            state.outcome = Cancelled(ScreenCancelReason(reason))
        await self._screen_finalize()

    async def _screen_finalize(self) -> None:
        state = self._screen_flow
        if state.closing:
            return
        state.closing = True
        state.closing_task = anyio.get_current_task().id
        outcome = state.outcome
        assert outcome is not None
        errors: list[Exception] = []
        try:
            for child in tuple(state.children):
                try:
                    await child._screen_close(ScreenCancelReason.PARENT_CLOSED)
                except Exception as error:
                    errors.append(error)
            if state.opened and isinstance(outcome, Cancelled) and state.on_cancel is not None:
                try:
                    await state.on_cancel(outcome)
                except Exception as error:
                    errors.append(error)
            try:
                await self.on_close(outcome)
            except Exception as error:
                errors.append(error)
        finally:
            if state.parent is not None:
                state.parent._screen_flow.children.discard(self)
            state.children.clear()
            state.on_result = None
            state.on_cancel = None
            state.done.resolve(outcome)
            state.closing_task = None
        if errors:
            msg = "screen closure failed"
            raise ExceptionGroup(msg, errors)


Screen.__response_spec__ = Screen._screen_compile_response_spec()

__all__ = ["Screen"]
