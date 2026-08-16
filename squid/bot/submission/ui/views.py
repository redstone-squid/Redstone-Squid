"""Models and views for discord interactions."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast, override

import discord
from discord import Interaction
from whenever import Instant

from squid.bot.errors import ErrorHandledLayoutView, ErrorHandledModal, ExpiringLayoutView
from squid.bot.i18n import resolve_locale, t
from squid.bot.submission.navigation_view import (
    BaseNavigableView,
    MaybeAwaitableBaseNavigableViewFunc,
    disable_view_controls,
)
from squid.bot.submission.parse import parse_dimensions, parse_hallway_dimensions
from squid.bot.submission.ui.components import (
    BuildField,
    DirectonalityLocationalitySelect,
    DoorTypeSelect,
    DynamicBuildEditButton,
    EphemeralBuildEditButton,
    get_text_input,
)
from squid.bot.utils.components import (
    DISCORD_BLUE,
    DISCORD_YELLOW,
    CardField,
    CardSection,
    StaticLayout,
    card_container,
    edit_interaction_layout,
    error_layout,
    no_mentions,
    text_layout,
)
from squid.bot.utils.permissions import allows
from squid.bot.utils.sentinel import DEFAULT, DefaultType
from squid.builds.application import BuildEditPatch, BuildService
from squid.builds.domain import Build, BuildCategory, BuildDraft, Status
from squid.core.i18n import _
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT

if TYPE_CHECKING:
    import squid.bot.app
    import squid.bot.submission.build_handler

logger = logging.getLogger(__name__)

BUILD_INFO_TIMEOUT_SECONDS = 300


@dataclass(frozen=True, slots=True)
class EditFieldSpec:
    """One field of the generic build edit modal.

    ``attribute`` is a :class:`BuildEditPatch` field name rather than a domain
    attribute: the patch is what the edit ultimately speaks, and some of its
    names no longer match the entity (patterns and the door facts live on the
    category subclasses, the url views project ``links``). Reading them back off
    a build goes through `squid.bot.submission.ui.components.get_text_input`.
    """

    attribute: str
    placeholder: str
    required: bool = False
    categories: frozenset[BuildCategory] | None = None
    """Categories that own this field, or None when every category has it."""

    def applies_to(self, build: Build) -> bool:
        return self.categories is None or build.category in self.categories


_DOOR_ONLY = frozenset({BuildCategory.DOOR})

EDIT_FIELDS: tuple[EditFieldSpec, ...] = (
    EditFieldSpec("dimensions", "Width x Height x Depth", required=True),
    EditFieldSpec("door_dimensions", "2x2", required=True, categories=_DOOR_ONLY),
    EditFieldSpec("version_spec", "1.16 - 1.17.3"),
    EditFieldSpec("door_type", "Full lamp, Funnel"),
    EditFieldSpec("door_orientation_type", "Door, Trapdoor, Skydoor", categories=_DOOR_ONLY),
    EditFieldSpec("wiring_placement_restrictions", "Seamless, Full Flush"),
    EditFieldSpec("component_restrictions", "Observerless"),
    EditFieldSpec("miscellaneous_restrictions", "Directional, Locational"),
    EditFieldSpec("normal_closing_time", "in gameticks", categories=_DOOR_ONLY),
    EditFieldSpec("normal_opening_time", "in gameticks", categories=_DOOR_ONLY),
    EditFieldSpec("creators_ign", "Me, My Dog"),
    EditFieldSpec("image_urls", "any urls, comma separated"),
    EditFieldSpec("video_urls", "any urls, comma separated"),
    EditFieldSpec("world_download_urls", "any urls, comma separated"),
    EditFieldSpec("completion_time", "Any time format works"),
)
"""Every entry must name a BuildEditPatch field; a test pins that."""


def _split_values(value: str) -> list[str]:
    """Split a user-facing comma-separated list while ignoring empty values."""
    return [item.strip() for item in value.split(",") if item.strip()]


def _format_dimensions(value: tuple[int | None, ...]) -> str:
    """Format only dimensions that have actually been supplied."""
    if not any(item is not None for item in value):
        return ""
    return " x ".join("?" if item is None else str(item) for item in value)


class SubmissionModal(ErrorHandledModal):
    """Collect the minimum useful build details without hiding a mini-language in a notes field."""

    def __init__(
        self,
        build: BuildDraft,
        builds: BuildService,
        parent: BuildSubmissionForm | None = None,
        *,
        locale: str | None = None,
    ) -> None:
        super().__init__(title=t(locale, _("Build basics")))
        self.build = build
        self.builds = builds
        self.parent = parent
        self.locale = locale

        self.door_size = discord.ui.TextInput(
            placeholder=t(locale, _("For example: 2x2")),
            default=_format_dimensions(build.door_dimensions),
            required=True,
        )
        self.pattern = discord.ui.TextInput(
            placeholder=t(locale, _("For example: regular, full lamp")),
            default=", ".join(build.patterns),
            required=False,
        )
        self.dimensions = discord.ui.TextInput(
            placeholder=t(locale, _("Width x Height x Depth")),
            default=_format_dimensions(build.dimensions),
            required=False,
        )
        self.versions = discord.ui.TextInput(
            placeholder=t(locale, _("For example: 1.20.4+")),
            default=build.version_spec,
            required=False,
        )
        self.creators = discord.ui.TextInput(
            placeholder=t(locale, _("Minecraft names, comma separated")),
            default=", ".join(build.creators_ign),
            required=False,
        )

        self.add_item(discord.ui.Label(text=t(locale, _("Door opening size")), component=self.door_size))
        self.add_item(discord.ui.Label(text=t(locale, _("Pattern")), component=self.pattern))
        self.add_item(discord.ui.Label(text=t(locale, _("Overall build size")), component=self.dimensions))
        self.add_item(discord.ui.Label(text=t(locale, _("Supported versions")), component=self.versions))
        self.add_item(discord.ui.Label(text=t(locale, _("Creators")), component=self.creators))

    @override
    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            door_dimensions = parse_hallway_dimensions(self.door_size.value)
            dimensions = parse_dimensions(self.dimensions.value) if self.dimensions.value else (None, None, None)
        except ValueError as error:
            await interaction.response.send_message(
                view=error_layout(t(self.locale, _("Check the dimensions")), str(error)),
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )
            return

        if door_dimensions[0] is None or door_dimensions[1] is None:
            await interaction.response.send_message(
                view=error_layout(
                    t(self.locale, _("Door opening size required")),
                    t(self.locale, _("Enter at least a width and height, such as `2x2`.")),
                ),
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )
            return

        self.build.door_dimensions = door_dimensions
        self.build.patterns = _split_values(self.pattern.value) or ["Regular"]
        self.build.dimensions = dimensions
        self.build.version_spec = self.versions.value.strip() or None
        self.build.creators_ign = _split_values(self.creators.value)
        if self.parent is None:
            await interaction.response.defer()
            return
        self.parent.validation_error = None
        self.parent.render()
        await edit_interaction_layout(interaction, self.parent)


class SubmissionDetailsModal(ErrorHandledModal):
    """Collect optional restrictions, links, and notes in an explicit format."""

    def __init__(self, parent: BuildSubmissionForm) -> None:
        super().__init__(title=t(parent.locale, _("Links and optional details")))
        self.parent = parent
        build = parent.build
        restrictions = (
            build.wiring_placement_restrictions
            + build.animated_restrictions
            + build.component_restrictions
            + build.miscellaneous_restrictions
        )
        self.restrictions = discord.ui.TextInput(
            placeholder=t(parent.locale, _("For example: Seamless, Observerless")),
            default=", ".join(restrictions),
            required=False,
        )
        self.image_urls = discord.ui.TextInput(
            placeholder=t(parent.locale, _("Image links, comma separated")),
            default=", ".join(build.image_urls),
            required=False,
        )
        self.video_urls = discord.ui.TextInput(
            placeholder=t(parent.locale, _("Video links, comma separated")),
            default=", ".join(build.video_urls),
            required=False,
        )
        self.world_urls = discord.ui.TextInput(
            placeholder=t(parent.locale, _("World download links, comma separated")),
            default=", ".join(build.world_download_urls),
            required=False,
        )
        self.notes = discord.ui.TextInput(
            placeholder=t(parent.locale, _("Anything staff should know")),
            default=build.extra_info.get("user"),
            style=discord.TextStyle.paragraph,
            required=False,
        )
        self.add_item(discord.ui.Label(text=t(parent.locale, _("Restrictions")), component=self.restrictions))
        self.add_item(discord.ui.Label(text=t(parent.locale, _("Images")), component=self.image_urls))
        self.add_item(discord.ui.Label(text=t(parent.locale, _("Videos")), component=self.video_urls))
        self.add_item(discord.ui.Label(text=t(parent.locale, _("World downloads")), component=self.world_urls))
        self.add_item(discord.ui.Label(text=t(parent.locale, _("Notes")), component=self.notes))

    @override
    async def on_submit(self, interaction: discord.Interaction) -> None:
        image_urls = _split_values(self.image_urls.value)
        video_urls = _split_values(self.video_urls.value)
        world_urls = _split_values(self.world_urls.value)
        invalid_urls = [
            url for url in (*image_urls, *video_urls, *world_urls) if not url.startswith(("https://", "http://"))
        ]
        if invalid_urls:
            await interaction.response.send_message(
                view=error_layout(
                    t(self.parent.locale, _("Check the links")),
                    t(self.parent.locale, _("Every link must start with `https://` or `http://`.")),
                ),
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )
            return

        await self.parent.builds.classify_restrictions(
            self.parent.build,
            _split_values(self.restrictions.value),
        )
        self.parent.build.replace_links("image", image_urls)
        self.parent.build.replace_links("video", video_urls)
        self.parent.build.replace_links("world-download", world_urls)
        notes = self.notes.value.strip()
        if notes:
            self.parent.build.extra_info["user"] = notes
        else:
            self.parent.build.extra_info.pop("user", None)
        await self.parent.refresh(interaction)


class EditModal[BotT: "squid.bot.app.RedstoneSquid"](ErrorHandledModal):
    """This is a modal that allows users to edit a build. Exclusively for BuildEditView."""

    def __init__(
        self, parent: BuildEditView[BotT], title: str, timeout: float | None = 60, custom_id: str | None = None
    ):
        self.parent = parent
        if custom_id:
            super().__init__(title=title, timeout=timeout, custom_id=custom_id)
        else:
            super().__init__(title=title, timeout=timeout)

    @override
    async def on_submit(self, interaction: discord.Interaction[BotT]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
        fields = [item for item in self.walk_children() if isinstance(item, BuildField)]
        await asyncio.gather(*(item.on_modal_submit() for item in fields))
        errors = [f"**{item.display_label}:** {item.validation_error}" for item in fields if item.validation_error]
        self.parent.validation_error = "\n".join(errors) or None
        await self.parent.update(interaction)


class BuildSubmissionForm(ErrorHandledLayoutView):
    """A private, resumable workspace for a minimal build submission.

    `on_submit` performs the actual submission from inside the button callback, so a
    failure can leave the form standing for a retry. Without it the view merely records
    the choice in `value` and stops, leaving the caller to submit after `wait()` — which
    is fine for a caller that has nothing that can fail.
    """

    actions = discord.ui.ActionRow()

    def __init__(
        self,
        build: BuildDraft,
        builds: BuildService,
        *,
        author_id: int | None = None,
        locale: str | None = None,
        timeout: float | None = 300.0,
        on_submit: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        build.submission_status = Status.PENDING
        build.category = BuildCategory.DOOR
        build.patterns = build.patterns or ["Regular"]

        self.build = build
        self.builds = builds
        self.author_id = author_id
        self.locale = locale
        self.on_submit = on_submit
        self.validation_error: str | None = None
        self.value: bool | None = None
        self._submitting = False
        self.edit_basics.label = t(locale, _("Edit basics"))
        self.edit_details.label = t(locale, _("Add links & details"))
        self.submit.label = t(locale, _("Submit for review"))
        self.cancel.label = t(locale, _("Cancel"))
        self.render()

    @property
    def is_ready(self) -> bool:
        """Return whether the minimal builder-entered fields are present."""
        width, height, _depth = self.build.door_dimensions
        return self.build.door_orientation is not None and width is not None and height is not None

    def render(self) -> None:
        """Render the current draft and keep all actions in one message."""
        controls = self.actions
        self.clear_items()
        missing = []
        if self.build.door_orientation is None:
            missing.append(t(self.locale, _("door type")))
        if not self.build.door_width or not self.build.door_height:
            missing.append(t(self.locale, _("door opening size")))
        guidance = self.validation_error
        if guidance is None and missing:
            guidance = t(self.locale, _("Required before review: {fields}."), fields=", ".join(missing))
        if guidance is None:
            guidance = t(self.locale, _("Ready to submit. Optional details can be added later."))

        self.add_item(
            card_container(
                t(self.locale, _("Submit a build")),
                guidance,
                accent_colour=DISCORD_BLUE if self.is_ready else DISCORD_YELLOW,
                sections=(
                    CardSection(
                        t(self.locale, _("Basics")),
                        (
                            CardField(t(self.locale, _("Door type")), self.build.door_orientation or "—"),
                            CardField(
                                t(self.locale, _("Opening size")), _format_dimensions(self.build.door_dimensions) or "—"
                            ),
                            CardField(t(self.locale, _("Pattern")), ", ".join(self.build.patterns)),
                            CardField(
                                t(self.locale, _("Build size")), _format_dimensions(self.build.dimensions) or "—"
                            ),
                            CardField(t(self.locale, _("Versions")), self.build.version_spec or "—"),
                            CardField(t(self.locale, _("Creators")), ", ".join(self.build.creators_ign) or "—"),
                        ),
                    ),
                ),
                footer=t(self.locale, _("Only the door type and opening size are required.")),
            )
        )
        self.add_item(discord.ui.ActionRow(DoorTypeSelect(self.build, locale=self.locale)))
        self.add_item(discord.ui.ActionRow(DirectonalityLocationalitySelect(self.build, locale=self.locale)))
        self.add_item(controls)

    @override
    async def interaction_check(self, interaction: Interaction, /) -> bool:
        if self.author_id is None or interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message(
            t(self.locale, _("These submission controls belong to someone else.")),
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )
        return False

    async def refresh(self, interaction: Interaction) -> None:
        """Refresh the workspace after a select or modal changes the draft."""
        self.validation_error = None
        self.render()
        await edit_interaction_layout(interaction, self)

    @actions.button(label="Edit basics", style=discord.ButtonStyle.primary)
    async def edit_basics(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(SubmissionModal(self.build, self.builds, self, locale=self.locale))

    @actions.button(label="Add links & details", style=discord.ButtonStyle.secondary)
    async def edit_details(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(SubmissionDetailsModal(self))

    @actions.button(label="Submit for review", style=discord.ButtonStyle.success)
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.is_ready:
            self.validation_error = t(
                self.locale,
                _("Choose a door type and add an opening size such as `2x2` before submitting."),
            )
            self.render()
            await edit_interaction_layout(interaction, self)
            return
        if self._submitting:
            await interaction.response.send_message(
                view=text_layout(t(self.locale, _("This build is still being submitted. Give it a moment."))),
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )
            return

        await interaction.response.defer()
        if self.on_submit is not None:
            self._submitting = True
            try:
                await self.on_submit()
            except Exception:
                # Never stop the view here: this message and its filled-in draft are the only
                # way back, so the form has to survive the failure and stay clickable. The
                # exception still reaches the view's error handler, which reports it beside
                # the form as its own card.
                self.validation_error = t(
                    self.locale,
                    _("Submitting failed and nothing was saved. Press “Submit for review” to try again."),
                )
                self.render()
                with contextlib.suppress(discord.HTTPException):
                    await interaction.edit_original_response(view=self, allowed_mentions=no_mentions())
                raise
            finally:
                self._submitting = False
        self.value = True
        self.stop()

    @actions.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.value = False
        self.stop()


class ConfirmationView(ErrorHandledLayoutView):
    """Ask the invoking user to confirm or cancel an action."""

    actions = discord.ui.ActionRow()

    def __init__(self, prompt: str | None = None, timeout: int = 60, *, locale: str | None = None):
        super().__init__(timeout=timeout)
        self.value = None
        controls = self.actions
        self.clear_items()
        self.add_item(discord.ui.TextDisplay(prompt or t(locale, _("Confirm this action?"))))
        self.add_item(controls)
        self.confirm.label = t(locale, _("Confirm"))
        self.cancel.label = t(locale, _("Cancel"))

    @actions.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.value = True
        self.stop()

    @actions.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.value = False
        self.stop()


class BuildEditView[BotT: "squid.bot.app.RedstoneSquid"](ExpiringLayoutView):
    """A view that allows users to edit a build.

    Changes are accumulated locally and applied under a service-managed lock on submit.
    """

    actions = discord.ui.ActionRow()

    def __init__(
        self,
        build: Build,
        builds: BuildService,
        items: Sequence[BuildField[Any]] | DefaultType = DEFAULT,
        *,
        locale: str | None = None,
        timeout: float = 300,
    ):
        """Initializes the BuildEditView.

        Args:
            build: The build to edit.
            items: The items to display in the view.
            timeout: The timeout for the view.
        """
        super().__init__(timeout=timeout)
        self.build = build
        self.builds = builds
        self.locale = locale
        self.validation_error: str | None = None
        if items is DEFAULT:
            items = [
                get_text_input(build, field.attribute, placeholder=field.placeholder, required=field.required)
                for field in EDIT_FIELDS
                if field.applies_to(build)
            ]
        self.items = items
        self.page = 1
        self._max_pages = max(1, (len(self.items) + 4) // 5)
        self.expiry_time = Instant.now().add(seconds=timeout)

    async def can_edit(self, interaction: Interaction[BotT]) -> bool:
        """Allow pending-build owners, and anyone holding the build-edit node.

        The guild is no longer part of the answer. `build.submission.edit` is
        global-scoped, so "the home server only" now comes from where the grant
        was made rather than from a hardcoded comparison -- and a DM, which used
        to be refused outright, is fine for someone who holds the node.
        """
        # Compares accounts, not snowflakes: ownership is `submitter_account_id`, and
        # `submitter_discord_id` is derived state that is absent on a freshly submitted
        # build. Read-only resolution, so opening an edit view never mints an account.
        actor_account_id = await interaction.client.account_ids.resolve(
            interaction.client.services.accounts, interaction.user.id
        )
        if (
            self.build.submission_status is Status.PENDING
            and actor_account_id is not None
            and self.build.submitter_account_id == actor_account_id
        ):
            return True
        return await allows(interaction, BUILD_SUBMISSION_EDIT)

    async def _send_denial(self, interaction: Interaction[BotT], message: str) -> None:
        layout = error_layout(t(self.locale, _("Cannot edit this build")), message)
        if interaction.response.is_done():  # pyrefly: ignore[no-matching-overload]
            await interaction.followup.send(view=layout, ephemeral=True, allowed_mentions=no_mentions())
        else:
            await interaction.response.send_message(view=layout, ephemeral=True, allowed_mentions=no_mentions())

    @override
    async def interaction_check(self, interaction: Interaction[BotT], /) -> bool:  # pyright: ignore [reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
        if Instant.now() > self.expiry_time:
            await self._send_denial(
                interaction,
                t(self.locale, _("This edit session expired. Reopen the build to start again.")),
            )
            return False
        if not await self.can_edit(interaction):
            await self._send_denial(
                interaction,
                t(self.locale, _("Only the pending build's submitter or a trusted staff member can edit it.")),
            )
            return False
        return True

    def get_modal(self) -> EditModal:
        """Page is 1-indexed"""
        modal = EditModal(
            parent=self,
            title=f"Edit Build (Page {self.page})",
            timeout=max(0.0, (self.expiry_time - Instant.now()).total("seconds")),
        )
        if 5 * self.page <= len(self.items):
            for i in range(5):
                base_index = 5 * (self.page - 1)
                modal.add_item(self.items[base_index + i].to_label())
        else:
            for i in range(len(self.items) % 5):
                base_index = 5 * (self.page - 1)
                modal.add_item(self.items[base_index + i].to_label())
        return modal

    def _handle_button_states(self) -> None:
        self.previous_page.disabled = self.page == 1
        self.next_page.disabled = self.page == self._max_pages

    async def send(self, interaction: discord.Interaction[BotT], ephemeral: bool = True) -> None:
        self.locale = await resolve_locale(interaction, interaction.client.services.settings)
        if not await self.can_edit(interaction):
            await self._send_denial(
                interaction,
                t(self.locale, _("Only the pending build's submitter or a trusted staff member can edit it.")),
            )
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)
        self.open.label = t(self.locale, _("Edit this section"))
        self.previous_page.label = t(self.locale, _("Previous"))
        self.next_page.label = t(self.locale, _("Next"))
        self.submit.label = t(self.locale, _("Review changes"))
        self._handle_button_states()
        await self._render(interaction)
        message = await interaction.followup.send(  # pyrefly: ignore[no-matching-overload]
            view=self,
            ephemeral=ephemeral,
            allowed_mentions=no_mentions(),
            wait=True,
        )
        self.bind_message(message)

    async def update(self, interaction: discord.Interaction[BotT]):
        self._handle_button_states()
        await self._render(interaction)
        await edit_interaction_layout(interaction, self)

    def get_handler(
        self, interaction: discord.Interaction[BotT]
    ) -> squid.bot.submission.build_handler.BuildHandler[BotT]:
        return interaction.client.for_build(self.build)

    def summary_text(self) -> str:
        start = 5 * (self.page - 1)
        page_items = self.items[start : start + 5]
        # Both markers are circles from the same family so only the fill differs; U+2022 BULLET
        # renders smaller and lower than U+25CB, which made a changed field look less prominent.
        summaries = [f"{'●' if item.modified else '○'} {item.summary}" for item in page_items]
        if self.validation_error:
            summaries.insert(
                0, t(self.locale, _("Fix these values before review:\n{errors}"), errors=self.validation_error)
            )
        return "\n".join(summaries)

    async def _render(self, interaction: discord.Interaction[BotT]) -> None:
        controls = self.actions
        self.clear_items()
        self.add_item(
            card_container(
                t(self.locale, _("Edit build")),
                t(
                    self.locale,
                    _("Section {page} of {pages}. Filled dots have unsaved changes."),
                    page=self.page,
                    pages=self._max_pages,
                ),
                accent_colour=DISCORD_YELLOW if self.validation_error else DISCORD_BLUE,
                fields=(CardField(t(self.locale, _("Fields in this section")), self.summary_text()),),
            )
        )
        self.add_item(await self.get_handler(interaction).render_container())
        self.add_item(controls)

    @actions.button(label="Open", style=discord.ButtonStyle.primary)
    async def open(self, interaction: discord.Interaction[BotT], button: discord.ui.Button):
        await interaction.response.send_modal(self.get_modal())

    @actions.button(label="Previous Page", style=discord.ButtonStyle.primary)
    async def previous_page(self, interaction: discord.Interaction[BotT], button: discord.ui.Button):
        self.page -= 1
        self._handle_button_states()
        await self.update(interaction)

    @actions.button(label="Next Page", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction[BotT], button: discord.ui.Button):
        self.page += 1
        self._handle_button_states()
        await self.update(interaction)

    @actions.button(label="Review changes", style=discord.ButtonStyle.success)
    async def submit(self, interaction: discord.Interaction[BotT], button: discord.ui.Button) -> None:
        if self.validation_error:
            await self.update(interaction)
            return
        changed = [item for item in self.items if item.modified]
        if not changed:
            self.validation_error = t(self.locale, _("No changes to review yet."))
            await self.update(interaction)
            return

        changes = "\n".join(
            f"**{item.display_label}:** `{item.original_string_value or '—'}` → `{item.current_string_value or '—'}`"
            for item in changed
        )
        confirmation = ConfirmationView(
            t(self.locale, _("## Review changes\n{changes}\n\nApply these changes?"), changes=changes),
            locale=self.locale,
        )
        # Deferring as an update keeps this interaction's original response pointing at the
        # workspace message, so the result can be rendered into it once the prompt is answered.
        # The confirmation goes out as a followup because the response slot is spent on the defer.
        await interaction.response.defer()
        confirmation_message = await interaction.followup.send(  # pyrefly: ignore[no-matching-overload]
            view=confirmation,
            ephemeral=True,
            allowed_mentions=no_mentions(),
            wait=True,
        )
        await confirmation.wait()
        await confirmation_message.delete()
        if confirmation.value is not True:
            return

        patch = BuildEditPatch.from_attributes({item.attribute: item.actual_value for item in changed})
        if self.build.id is None:
            patch.apply(self.build)
            await self.builds.save(self.build)
        else:
            async with self.builds.edit(self.build.id, patch) as edit:
                self.build = await edit.commit()
            await interaction.client.refresh_posts("build", str(self.build.id))
        self.stop()
        success = StaticLayout(
            discord.ui.TextDisplay(t(self.locale, _("## Changes saved"))),
            await self.get_handler(interaction).render_container(),
        )
        # The workspace is ephemeral, and an ephemeral message only exists inside the interaction:
        # editing it through the channel endpoint (`Message.edit`) is a 404, so go via the webhook.
        await interaction.edit_original_response(view=success, allowed_mentions=no_mentions())


class BuildInfoView[BotT: "squid.bot.app.RedstoneSquid"](BaseNavigableView[BotT]):
    def __init__(
        self,
        build: Build,
        *,
        parent: BaseNavigableView[BotT] | MaybeAwaitableBaseNavigableViewFunc[BotT] | None = None,
    ):
        super().__init__(parent=parent, timeout=BUILD_INFO_TIMEOUT_SECONDS)
        self.build = build
        self._message: discord.Message | None = None
        if build.id is None:
            edit_button = EphemeralBuildEditButton(build)
        else:
            edit_button = DynamicBuildEditButton(build)
        self._edit_row = discord.ui.ActionRow(
            cast(discord.ui.Item[discord.ui.LayoutView], edit_button),
        )
        self.add_item(self._edit_row)

    async def _render(self, interaction: discord.Interaction[BotT]) -> None:
        self.clear_items()
        self.add_item(await interaction.client.for_build(self.build).render_container())
        self.add_item(self._edit_row)
        self.add_item(self._navigation_row)

    @override
    async def send(self, interaction: discord.Interaction[BotT]) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer()
        await self._render(interaction)
        self._message = await interaction.followup.send(  # pyrefly: ignore[no-matching-overload]
            view=self,
            allowed_mentions=no_mentions(),
            wait=True,
        )

    @override
    async def update(self, interaction: discord.Interaction[BotT]) -> None:
        await self._render(interaction)
        await edit_interaction_layout(interaction, self)
        if interaction.message is not None:
            self._message = interaction.message

    @override
    async def on_timeout(self) -> None:
        """Expire stateful navigation while leaving persistent public actions separate."""
        disable_view_controls(self)
        self.stop()
        if self._message is None:
            return
        try:
            await self._message.edit(view=self, allowed_mentions=no_mentions())
        except discord.HTTPException:
            logger.debug("Could not disable expired build info controls", exc_info=True)
