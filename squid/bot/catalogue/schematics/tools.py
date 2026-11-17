"""Schematic actions and their results within a build detail panel."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast, override

import anyio

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.catalogue.schematics.presentation import (
    WRITABLE_EXTENSIONS,
    _describe,
)
from squid.bot.catalogue.schematics.video import VideoEditor
from squid.bot.lookup import pick
from squid.bot.ui import tr
from squid.bot.utils.permissions import allows
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    BUILD_SCHEMATIC_DOWNLOAD_PRIVATE,
    BUILD_SCHEMATIC_VIEW_PRIVATE,
)
from squid.schematics.application import ConvertRequest, SchematicService, StoredSchematic, summarise_losses
from squid.schematics.application.video import VideoRenderRequest
from squid.schematics.domain.models import SchematicFormat
from squid.schematics.errors import SchematicRenderRefusedError
from squid.suggestions.domain import Suggestion
from squid_i18n import md_tr

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid

type SchematicRequest = sd.Request[Any, RedstoneSquid]


class SchematicTools(sl.Component[sl.ComponentsV2Target, SchematicRequest]):
    """Own schematic actions, permission checks, and the latest generated result."""

    _conversion_version: Suggestion | None = sl.state(None)
    _schematic_result: sl.TextLike | None = sl.state(None)
    _asset: sl.document.Asset | None = sl.state(None, opaque=True)
    _video_job_id: int | None = sl.state(None)
    _video_recipe: VideoRenderRequest = sl.state(VideoRenderRequest())

    def __init__(
        self,
        build_id: int,
        *,
        schematics: SchematicService,
        actor_account_id: int | None,
        authorize: Callable[[PermissionNode], Awaitable[bool]],
        show_details: Callable[[], bool] | None = None,
    ) -> None:
        self._build_id = build_id
        self._schematics = schematics
        self._actor_account_id = actor_account_id
        self._authorize = authorize
        self._show_details = show_details
        self._video_delivery_lock = asyncio.Lock()
        self._delivered_videos: set[int] = set()
        self._video_recipes: dict[int, VideoRenderRequest] = {}

    @override
    def render(self) -> sl.DocumentLike[sl.ComponentsV2Target, SchematicRequest]:
        if self._show_details is not None and not self._show_details():
            return ()
        nodes = list(self._schematic_nodes())
        if self._schematic_result is not None:
            nodes.append(sl.note(self._schematic_result))
        if self._asset is not None and self._asset.media_type.startswith("image/"):
            nodes.append(sl.media(self._asset, key="schematic-preview"))
        elif self._asset is not None:
            nodes.append(
                sl.download(
                    self._asset,
                    label=tr(t"bot-submission-build-browse-download-result = Download result"),
                    key="build-download",
                )
            )
        if self._conversion_version is not None:
            label = self._conversion_version.label
            nodes.append(sl.note(tr(t"schematic-convert-selected = Conversion release: {label}")))
        return nodes

    def _schematic_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, SchematicRequest], ...]:
        if not self._schematics.available:
            return ()
        videos_enabled = self._schematics.videos is not None and self._schematics.videos.enabled
        return (
            sl.action_controls(
                sl.action_control(
                    tr(t"bot-submission-build-browse-download-schematic = Download"),
                    self._schematic_download,
                    key="schematic-download",
                ),
                sl.form(
                    tr(t"bot-submission-build-browse-convert-schematic = Other format or version"),
                    _ConvertForm().spec(),
                    key="convert",
                    on_submit=self._convert,
                ),
                sl.action_control(
                    tr(t"schematic-convert-release = Choose conversion release"),
                    sd.on_press(self._choose_conversion_version),
                    key="convert-version",
                ),
                self._conversion_version is not None
                and sl.action_control(
                    tr(t"schematic-convert-original = Keep original version"),
                    self._reset_conversion_version,
                    key="source-version",
                ),
                sl.form(
                    tr(t"schematic-conversion-maintenance = Conversion maintenance"),
                    _ConversionMaintenanceForm().spec(),
                    key="conversion-maintenance",
                    on_submit=self._convert_maintenance,
                ),
                sl.action_control(
                    tr(t"bot-submission-build-browse-render-schematic = Preview image"),
                    self._schematic_render,
                    key="schematic-render",
                ),
                videos_enabled
                and sl.action_control(
                    tr(t"schematic-video-action = Render video"), sd.on_press(self._open_video), key="guided-video"
                ),
                videos_enabled
                and self._video_job_id is not None
                and sl.action_control(
                    tr(t"schematic-video-refresh = Check video"),
                    sd.on_press(self._refresh_video),
                    key="video-refresh",
                ),
                sl.action_control(
                    tr(t"bot-submission-build-browse-schematic-info = Details"),
                    self._schematic_info,
                    key="schematic-info",
                ),
                key="schematic-actions",
            ),
        )

    async def _primary(self, event: sl.ActionEvent[Any], private_access: PermissionNode) -> StoredSchematic | None:
        """Return the featured schematic if the actor may use it, otherwise tell them why not.

        A publicly downloadable schematic is open to everyone. Otherwise the uploader may use it, as
        may anyone holding `private_access`; the download and view actions pass different nodes.
        """
        stored = await self._schematics.featured_for_build(self._build_id)
        if stored is None:
            await event.notice(tr(t"build-schematic-missing = This build has no schematic attached."))
            return None
        is_uploader = self._actor_account_id is not None and stored.uploaded_by_account_id == self._actor_account_id
        if not (stored.publication.is_public_downloadable or is_uploader or await self._authorize(private_access)):
            await event.notice(tr(t"build-schematic-not-public = This schematic is not publicly available."))
            return None
        return stored

    async def _schematic_info(self, event: sl.PressEvent) -> None:
        stored = await self._primary(event, BUILD_SCHEMATIC_VIEW_PRIVATE)
        if stored is None:
            return
        self._schematic_result = _describe(stored, render_skip=await self._schematics.explain_render_skip(stored))

    async def _schematic_download(self, event: sl.PressEvent) -> None:
        if await self._primary(event, BUILD_SCHEMATIC_DOWNLOAD_PRIVATE) is None:
            return
        data, _ = await self._schematics.convert(
            self._build_id, ConvertRequest(target_format=SchematicFormat.LITEMATIC)
        )
        self._asset = self._schematic_asset(data, "litematic")

    async def _schematic_render(self, event: sl.PressEvent) -> None:
        if await self._primary(event, BUILD_SCHEMATIC_VIEW_PRIVATE) is None:
            return
        try:
            rendered = await self._schematics.render_now(self._build_id, request=self._schematics.render_recipe())
        except SchematicRenderRefusedError as error:
            await event.notice(error.public_text())
            return
        self._asset = sl.file(rendered.png, filename=f"build-{self._build_id}-render.png")

    async def _open_video(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        request = event.context
        assert request.root is not None
        parent = request.root.component
        assert isinstance(parent, sd.Screen)

        async def selected(result: sd.ScreenResultEvent[VideoRenderRequest]) -> None:
            await self._submit_recipe(result, result.value)

        await parent.open_child(event, VideoEditor(self._video_recipe), on_result=selected)

    async def _submit_recipe(self, event: sd.ActionEvent, recipe: VideoRenderRequest) -> None:
        request = cast(SchematicRequest, event.context)
        actor = await request.client.actors.resolve(request, refresh=True)
        stored = await self._primary(event, BUILD_SCHEMATIC_VIEW_PRIVATE)
        service = self._schematics.videos
        if stored is None or service is None or actor.account_id is None:
            return
        await self._queue_recipe(request, stored, actor.account_id, recipe, event)

    async def _queue_recipe(
        self,
        request: SchematicRequest,
        stored: StoredSchematic,
        account_id: int,
        recipe: VideoRenderRequest,
        event: sl.ActionEvent[Any],
    ) -> None:
        service = self._schematics.videos
        assert service is not None
        status = await service.submit_video(
            self._build_id,
            stored.id,
            recipe,
            account_id=account_id,
            can_view_private=await allows(request, BUILD_SCHEMATIC_VIEW_PRIVATE),
        )
        self._video_recipe = recipe
        self._video_recipes[status.id] = recipe
        self._video_job_id = status.id
        job_id = status.id
        await event.notice(
            md_tr(t"schematic-video-queued = Video #{job_id} queued. I'll attach it here when it is ready.")
        )
        request.client.background_tasks.start(
            self._follow_video(request, status.id), name=f"schematic-video-{status.id}"
        )

    async def _follow_video(self, request: SchematicRequest, job_id: int) -> None:
        with anyio.move_on_after(600):
            while request.interaction is not None and not request.interaction.is_expired():
                if await self._deliver_video(request, job_id):
                    return
                await anyio.sleep(2)

    async def _refresh_video(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        request = event.context
        if self._video_job_id is not None and not await self._deliver_video(request, self._video_job_id, repeat=True):
            await event.notice(tr(t"schematic-video-pending = The video is still rendering."))

    async def _deliver_video(self, request: SchematicRequest, job_id: int, *, repeat: bool = False) -> bool:
        async with self._video_delivery_lock:
            # Explicit checks should show the result even after automatic delivery.
            if not repeat and job_id in self._delivered_videos:
                return True
            delivered = await self._deliver_video_once(request, job_id)
            if delivered and request.interaction is not None and not request.interaction.is_expired():
                self._delivered_videos.add(job_id)
            return delivered

    async def _deliver_video_once(self, request: SchematicRequest, job_id: int) -> bool:
        service = self._schematics.videos
        actor = await request.client.actors.resolve(request)
        if service is None or actor.account_id is None:
            return True
        can_view = await allows(request, BUILD_SCHEMATIC_VIEW_PRIVATE)
        status = await service.get_video(job_id, account_id=actor.account_id, can_view_private=can_view)
        if status.status in ("queued", "running"):
            return False
        if status.input_candidates:
            await request.respond(
                VideoEditor(
                    self._video_recipes.get(job_id, self._video_recipe),
                    candidates=status.input_candidates,
                    submit=self._submit_recipe,
                ),
                audience="personal",
            )
            return True
        if status.status != "succeeded":
            reason = _video_failure_reason(status.error, expired=status.status == "expired")
            await request.respond(
                sl.paragraph(md_tr(t"schematic-video-failure-detail = Video #{job_id}: {reason}")),
                audience="personal",
            )
            return True
        content = await service.open_video_content(job_id, account_id=actor.account_id, can_view_private=can_view)
        interaction = request.interaction
        if interaction is None or interaction.is_expired():
            return True
        if len(content) > interaction.filesize_limit:
            url = f"{service.public_base_url}/v1/schematic-videos/{job_id}/content"
            await request.respond(
                sl.paragraph(
                    md_tr(
                        t"schematic-video-too-large = This video exceeds Discord's attachment limit. Download it while signed in: {url}",
                    )
                ),
                audience="personal",
            )
            return True
        await request.respond(
            (
                sl.paragraph(tr(t"schematic-video-ready = Your schematic video is ready.")),
                sl.media(content, filename=f"build-{self._build_id}-simulation.mp4", key="schematic-video-preview"),
            ),
            audience="personal",
        )
        return True

    async def _choose_conversion_version(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        await pick(
            event,
            "conversion_versions",
            tr(t"schematic-convert-release = Choose conversion release"),
            self._conversion_version_picked,
        )

    async def _conversion_version_picked(self, _event: sd.ActionEvent, item: Suggestion) -> None:
        self._conversion_version = item

    async def _reset_conversion_version(self, _event: sl.PressEvent) -> None:
        self._conversion_version = None

    async def _convert(self, event: sl.SubmitEvent) -> None:
        form = _ConvertForm.from_submission(event)
        await self._convert_output(
            event,
            ConvertRequest(target_format=form.format),
            version_label=self._conversion_version.value if self._conversion_version else None,
        )

    async def _convert_maintenance(self, event: sl.SubmitEvent) -> None:
        form = _ConversionMaintenanceForm.from_submission(event)
        await self._convert_output(
            event, ConvertRequest(target_format=form.format, target_data_version=form.data_version)
        )

    async def _convert_output(
        self, event: sl.SubmitEvent, request: ConvertRequest, *, version_label: str | None = None
    ) -> None:
        if await self._primary(event, BUILD_SCHEMATIC_DOWNLOAD_PRIVATE) is None:
            return
        data, losses = await self._schematics.convert(self._build_id, request, version_label=version_label)
        file_format = request.target_format
        self._asset = self._schematic_asset(data, WRITABLE_EXTENSIONS[file_format])
        loss_summary = summarise_losses(losses)
        self._schematic_result = md_tr(
            t"bot-submission-build-browse-conversion-report = Conversion report: {loss_summary}"
        )

    def _schematic_asset(self, data: bytes, extension: str) -> sl.document.Asset:
        return sl.file(data, filename=f"build-{self._build_id}.{extension}")


class _ConvertForm(sl.forms.Form):
    title = tr(t"bot-submission-build-browse-convert-schematic = Other format or version")

    format = sl.forms.ChoiceField(
        label=tr(t"bot-submission-build-browse-output-format = Output format"),
        description=tr(
            t"schematic-convert-format-help = litematic for Litematica; schem for WorldEdit; mcstructure for Bedrock."
        ),
        default=SchematicFormat.LITEMATIC,
        options=tuple(
            sl.forms.ChoiceOption(file_format.value, file_format.value, file_format)
            for file_format in WRITABLE_EXTENSIONS
        ),
    )


class _ConversionMaintenanceForm(_ConvertForm):
    title = tr(t"schematic-conversion-maintenance = Conversion maintenance")

    data_version = sl.forms.IntField(
        label=tr(t"schematic-world-format-version = World-format data version"),
        description=tr(
            t"schematic-world-format-help = Minecraft world-format number, such as 3953; this is not a database version ID."
        ),
        minimum=0,
    )


def _video_failure_reason(error: str | None, *, expired: bool) -> sl.TextLike:
    if expired:
        return tr(t"schematic-video-expired = The stored video has expired. Request a new render.")
    match error:
        case "unavailable":
            return tr(
                t"schematic-video-unavailable = Video rendering is unavailable on the worker. Ask an administrator to check the video helper and FFmpeg installation."
            )
        case "timeout":
            return tr(
                t"schematic-video-timeout = Rendering exceeded its time limit. Try a shorter clip or a smaller schematic."
            )
        case "too_large":
            return tr(
                t"schematic-video-too-large-failure = The schematic or output exceeded the rendering limits. Try a smaller schematic or shorter clip."
            )
        case "ambiguous_simulation_input":
            return tr(
                t"schematic-video-no-input = No usable simulation input was selected. Specify a lever or button position, or keep the build static."
            )
        case "invalid":
            return tr(
                t"schematic-video-invalid-failure = The renderer could not process this schematic. Ask an administrator to check the worker logs using this video number."
            )
        case "crashed":
            return tr(
                t"schematic-video-crashed = The rendering process stopped unexpectedly. Ask an administrator to check the worker logs using this video number."
            )
        case _:
            return tr(
                t"schematic-video-internal-failure = Rendering failed. Ask an administrator to check the worker logs using this video number."
            )
