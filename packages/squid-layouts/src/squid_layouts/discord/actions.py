"""Discord adapter for portable component action events."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import discord

from squid_layouts.actions import ActionEvent, ActionPolicy, Visibility
from squid_layouts.discord import delivery as deliver
from squid_layouts.discord.modal import ModalSpec, build_form_modal, build_modal
from squid_layouts.forms import FieldError, FormIssue, FormLike, FormSpec, SubmitHandler, bind_form
from squid_layouts.text import TextLike, resolve_text

if TYPE_CHECKING:
    from squid_layouts.discord.mount import Mount


class ActionResponder:
    """Translate portable response intents onto one Discord interaction."""

    def __init__(
        self,
        interaction: discord.Interaction,
        mount: Mount,
        selected_entities: tuple[object, ...] = (),
    ) -> None:
        self.interaction = interaction
        self.mount = mount
        self.selected_entities = selected_entities

    async def acknowledge(self) -> None:
        if not self.interaction.response.is_done():
            await self.interaction.response.defer()

    async def notice(self, text: TextLike, *, visibility: Visibility = Visibility.PRIVATE) -> None:
        resolved = resolve_text(text, self.mount.localization).content
        await deliver.respond_text(self.interaction, resolved, ephemeral=visibility is Visibility.PRIVATE)

    async def send_modal(self, form: ModalSpec | discord.ui.Modal) -> None:
        """Present a form, stated in Discord's own types.

        Not part of `sl.ActionResponder`: a form's payload is a frontend object, so no
        portable protocol can type it. Reach this through `sl.discord.responder(event)`.
        """
        modal = build_modal(form, limits=self.mount.limits) if isinstance(form, ModalSpec) else form
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
        policy: ActionPolicy | None = None,
    ) -> None:
        """Present a portable form and route its submission back through this mount."""
        spec, handler, default_policy = bind_form(form, on_submit)
        selected_policy = policy or default_policy
        modal = self._form_modal(spec, key, handler, selected_policy, self.mount.generation)
        if self.interaction.response.is_done():
            message = "Discord modals must be the interaction's initial response"
            raise RuntimeError(message)
        await self.interaction.response.send_modal(modal)

    def _form_modal(
        self,
        spec: FormSpec,
        key: str,
        handler: SubmitHandler,
        policy: ActionPolicy,
        generation: int,
    ) -> discord.ui.Modal:
        async def submit(interaction: discord.Interaction, values: dict[str, object]) -> None:
            await self.mount.dispatch_submit(
                key,
                interaction,
                spec,
                values,
                handler,
                policy=policy,
                generation=generation,
            )

        return build_form_modal(
            spec,
            on_submit=submit,
            timeout=self.mount.timeout,
            localization=self.mount.localization,
            limits=self.mount.limits,
        )

    async def retry_form(
        self,
        spec: FormSpec,
        errors: tuple[FormIssue, ...],
        *,
        key: str,
        handler: SubmitHandler,
        policy: ActionPolicy,
        generation: int,
        actor_id: int,
    ) -> None:
        """Render validation errors with a button that reopens the attempted form."""
        lines: list[str] = []
        labels = {field.key: field.label or field.key for field in spec.fields}
        for error in errors:
            if isinstance(error, FieldError):
                label = resolve_text(labels.get(error.key, error.key), self.mount.localization).content
                message = resolve_text(error.message, self.mount.localization).content
                lines.append(f"**{label}:** {message}")
            else:
                lines.append(resolve_text(error.message, self.mount.localization).content)
        retry = _RetryButton(
            owner_id=actor_id,
            modal=lambda interaction: ActionResponder(interaction, self.mount)._form_modal(
                spec,
                key,
                handler,
                policy,
                generation,
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

    async def finish(self) -> None:
        await self.mount.finish_via(self.interaction)

    def invalidate(self) -> None:
        self.mount.invalidate()


def responder(event: ActionEvent) -> ActionResponder:
    """Return the Discord responder behind a portable action event.

    The sanctioned escape hatch to Discord's native response surfaces — `send_modal` —
    which no portable protocol can type. Handlers that must stay frontend-neutral keep to
    `ActionEvent`'s own methods.

    Raises:
        LookupError: The event came from a frontend other than Discord.
    """
    found = event.responder
    if not isinstance(found, ActionResponder):
        frontend = event.context.get("frontend", type(found).__name__)
        message = f"this event came from frontend {frontend!r}, not Discord"
        # LookupError, not TypeError: the argument is a valid event, the Discord fact is just absent.
        raise LookupError(message)  # noqa: TRY004
    return found


def native(event: ActionEvent) -> discord.Interaction[Any]:
    """Return the Discord interaction behind a portable action event.

    For the Discord-only facts handlers actually need — permission checks, nested sends,
    client lookups. The client parameter is `Any` because the framework cannot know the
    host's client subclass; hosts that care can annotate the binding themselves.

    Do not drive `.response` yourself: the mount owns this interaction's response
    lifecycle. Use `event.acknowledge()`, `event.notice()`, `event.finish()`, or
    `responder(event).send_modal()`. A hand-rolled `defer()` survives only because
    `Mount.flush` falls back to editing through the followup.

    The one sanctioned driver is `sl.discord.adopt`'s interaction proxy, which exists to put
    a legacy `interaction.response.edit_message(view=self)` back under mount ownership by
    performing no HTTP at all.

    Raises:
        LookupError: The event came from a frontend other than Discord.
    """
    return responder(event).interaction


def selected_entities(event: ActionEvent) -> tuple[object, ...]:
    """Return the Discord objects resolved for an entity-selection event."""
    return responder(event).selected_entities


class _RetryButton(discord.ui.Button[discord.ui.LayoutView]):
    def __init__(self, *, owner_id: int, modal: Callable[[discord.Interaction], discord.ui.Modal]) -> None:
        super().__init__(label="Try again")
        self.owner_id = owner_id
        self.modal = modal

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await deliver.respond_text(interaction, "This form attempt belongs to another member.", ephemeral=True)
            return
        await interaction.response.send_modal(self.modal(interaction))
