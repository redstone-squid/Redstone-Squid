"""Carry portable action responses through a mounted Discord interaction.

ActionResponder gives the event its acknowledgement, notice, and form operations while
retaining the originating MessageRoot. Mounted forms return through dispatch_submit,
which evaluates the schema and applies the root's action policy. Closure and on_success
work are recorded during the handler, then accepted only after its transaction commits.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, override

import discord

from squid_reactivity.actions import ActionContext, current_action
from squid_reactivity.core import on_action_commit, on_action_rollback
from squid_reactivity.operations import Cancelled
from squid_ui.forms import FieldError, FormField, FormIssue, FormLike, FormSpec, SubmitHandler, bind_form
from squid_ui.interactions import ActionEvent, ActionMode, Visibility
from squid_ui.runtime.continuations import PostCommitOperation
from squid_ui.text import TextLike, resolve_text
from squid_ui_discord import delivery as deliver
from squid_ui_discord.modal import ModalSpec, build_form_modal, build_modal

if TYPE_CHECKING:
    from squid_ui.runtime.histories import History
    from squid_ui_discord.message_root import AnyMessageRoot


class ActionResponder:
    """Translate portable response intents onto one Discord interaction."""

    def __init__(
        self,
        interaction: discord.Interaction,
        message_root: AnyMessageRoot,
        selected_entities: tuple[object, ...] = (),
    ) -> None:
        self.interaction = interaction
        self.message_root = message_root
        self.selected_entities = selected_entities
        self._action_active = False
        self._action_context: ActionContext | None = None
        self._finish_requested = False
        self._finish_accepted = False
        self._after_commit: list[PostCommitOperation[Any]] = []
        self._screen_unlocked: asyncio.Event | None = None

    async def acknowledge(self) -> None:
        if not self.interaction.response.is_done():
            await self.interaction.response.defer()

    async def notice(self, text: TextLike, *, visibility: Visibility = Visibility.PRIVATE) -> None:
        resolved = resolve_text(text, self.message_root.localization).content
        await deliver.respond_text(self.interaction, resolved, ephemeral=visibility is Visibility.PRIVATE)

    async def send_modal(self, form: ModalSpec | discord.ui.Modal) -> None:
        """Present a form, stated in Discord's own types.

        Not part of `sl.ActionResponder`: a form's payload is a frontend object, so no
        portable protocol can type it. Application handlers use `await event.context.form(...)`.
        """
        modal = build_modal(form, limits=self.message_root.limits.components) if isinstance(form, ModalSpec) else form
        if self.interaction.response.is_done():
            message = "Discord modals must be the interaction's initial response"
            raise RuntimeError(message)
        await self.interaction.response.send_modal(modal)

    async def present_form(
        self,
        form: FormLike,
        *,
        key: str = "form",
        on_submit: SubmitHandler | None = None,
        mode: ActionMode | None = None,
        label: TextLike = "",
        record: History | None = None,
    ) -> None:
        """Present a portable form and route its submission back through this mount."""
        spec, handler, default_mode = bind_form(form, on_submit)
        selected_mode = mode or default_mode
        modal = self._form_modal(spec, key, handler, selected_mode, self.message_root.generation, label, record)
        if self.interaction.response.is_done():
            message = "Discord modals must be the interaction's initial response"
            raise RuntimeError(message)
        await self.interaction.response.send_modal(modal)

    def _form_modal(
        self,
        spec: FormSpec,
        key: str,
        handler: SubmitHandler,
        mode: ActionMode,
        generation: int,
        label: TextLike,
        record: History | None,
    ) -> discord.ui.Modal:
        async def submit(interaction: discord.Interaction, values: dict[str, object]) -> None:
            await self.message_root.dispatch_submit(
                key,
                interaction,
                spec,
                values,
                handler,
                mode=mode,
                generation=generation,
                label=label,
                record=record,
            )

        return build_form_modal(
            spec,
            on_submit=submit,
            timeout=self.message_root.timeout,
            localization=self.message_root.localization,
            limits=self.message_root.limits.components,
        )

    async def retry_form(
        self,
        spec: FormSpec,
        errors: tuple[FormIssue, ...],
        *,
        key: str,
        handler: SubmitHandler,
        mode: ActionMode,
        generation: int,
        actor_id: int,
        label: TextLike,
        record: History | None,
    ) -> None:
        """Render validation errors with a button that reopens the attempted form."""
        lines: list[str] = []
        labels = {field.key: field.label or field.key for field in spec.items if isinstance(field, FormField)}
        for error in errors:
            if isinstance(error, FieldError):
                field_labels = [
                    resolve_text(labels.get(field.key, field.key), self.message_root.localization).content
                    for field in error.fields
                ]
                field_label = ", ".join(field_labels)
                message = resolve_text(error.message, self.message_root.localization).content
                lines.append(f"**{field_label}:** {message}")
            else:
                lines.append(resolve_text(error.message, self.message_root.localization).content)
        retry = _RetryButton(
            owner_id=actor_id,
            modal=lambda interaction: ActionResponder(interaction, self.message_root)._form_modal(
                spec,
                key,
                handler,
                mode,
                generation,
                label,
                record,
            ),
        )
        view = discord.ui.LayoutView(timeout=300)
        view.add_item(discord.ui.TextDisplay("\n".join(lines)))
        view.add_item(discord.ui.ActionRow(retry))
        if self.interaction.response.is_done():
            await self.interaction.followup.send(view=view, ephemeral=True)
        else:
            await self.interaction.response.send_message(view=view, ephemeral=True)

    async def redirect(self, url: str) -> None:
        await self.notice(url)

    def finish_on_success(self) -> None:
        """Request closure once this action succeeds and its changes are accepted.

        This records an intent without waiting for delivery or cleanup. A failed or
        cancelled handler discards it. The dispatcher performs shutdown outside the
        transaction, so cleanup errors cannot roll back a successful action.
        """
        if not self._action_active:
            message = "event.finish_on_success() is only available during its action handler"
            raise RuntimeError(message)
        self._finish_requested = True

    def invalidate(self) -> None:
        self.message_root.invalidate()

    def on_success[ValueT](
        self, callback: Callable[[], Awaitable[ValueT]], *, name: str | None = None
    ) -> PostCommitOperation[ValueT]:
        """Register a host-owned operation to run once this action succeeds."""
        if not self._action_active:
            message = "event.on_success() is only available during its action handler"
            raise RuntimeError(message)
        if current_action() is not self._action_context:
            message = "after-commit work requires a dispatcher-owned transaction, not a nested dispatch"
            raise RuntimeError(message)
        host = self.message_root.config.execution_host
        if host is None:
            message = "after-commit work requires a running UIExecutionHost"
            raise RuntimeError(message)
        host._require_running()
        operation = PostCommitOperation(
            callback,
            name=name or getattr(callback, "__qualname__", type(callback).__qualname__),
        )
        on_action_commit(lambda commit, continuation: operation._commit(commit, continuation, host))
        on_action_rollback(operation._discard)
        self._after_commit.append(operation)
        return operation

    def _on_screen_success[ValueT](
        self, callback: Callable[[], Awaitable[ValueT]], *, name: str | None = None
    ) -> PostCommitOperation[ValueT]:
        """Run a screen continuation after commit and release of this action's lock."""
        unlocked = self._screen_unlocked
        if unlocked is None:
            unlocked = self._screen_unlocked = asyncio.Event()

        async def after_unlock() -> ValueT:
            await unlocked.wait()
            return await callback()

        return self.on_success(after_unlock, name=name)

    async def _wait_after_commit(self) -> list[Exception]:
        errors: list[Exception] = []
        for operation in self._after_commit:
            try:
                await operation.wait()
            except asyncio.CancelledError:
                caller = asyncio.current_task()
                if caller is not None and caller.cancelling():
                    raise
                if not isinstance(operation.status, Cancelled):
                    raise
                errors.append(RuntimeError("an after-commit operation was cancelled"))
            except Exception as error:
                errors.append(error)
        return errors


def _responder(event: ActionEvent) -> ActionResponder:
    """Return the Discord responder behind a portable action event.

    Transport adapters and guards also serve standalone roots without an installed runtime.
    Application handlers resolve a request instead.

    Raises:
        LookupError: The event came from a frontend other than Discord.
    """
    found = event.responder
    if not isinstance(found, ActionResponder):
        frontend = event.metadata.get("frontend", type(found).__name__)
        message = f"this event came from frontend {frontend!r}, not Discord"
        # LookupError, not TypeError: the argument is a valid event, the Discord fact is just absent.
        raise LookupError(message)  # noqa: TRY004
    return found


def _selected_entities(event: ActionEvent) -> tuple[object, ...]:
    """Return the Discord objects resolved for an entity-selection event."""
    return _responder(event).selected_entities


class _RetryButton(discord.ui.Button[discord.ui.LayoutView]):
    def __init__(self, *, owner_id: int, modal: Callable[[discord.Interaction], discord.ui.Modal]) -> None:
        super().__init__(label="Try again")
        self.owner_id = owner_id
        self.modal = modal

    @override
    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await deliver.respond_text(interaction, "This form attempt belongs to another member.", ephemeral=True)
            return
        await interaction.response.send_modal(self.modal(interaction))
