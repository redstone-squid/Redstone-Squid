"""Schematic actions and their results within a build detail panel."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol, cast, override

import anyio

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.schematics import (
    WRITABLE_EXTENSIONS,
    _describe,
    _describe_input_refusal,
    _describe_timing,
    _parse_position,
)
from squid.bot.ui import tr
from squid.bot.utils.permissions import actor_for_interaction, allows
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    BUILD_SCHEMATIC_DOWNLOAD_PRIVATE,
    BUILD_SCHEMATIC_MEASURE_TIMING,
    BUILD_SCHEMATIC_VIEW_PRIVATE,
)
from squid.runtime import BackgroundTaskSupervisor
from squid.schematics.application import ConvertRequest, SchematicService, StoredSchematic, summarise_losses
from squid.schematics.application.video import VideoInteraction, VideoRenderRequest
from squid.schematics.domain.models import SchematicFormat
from squid.schematics.errors import AmbiguousSimulationInputError, SchematicRenderRefusedError


class _VideoTaskHost(Protocol):
    background_tasks: BackgroundTaskSupervisor


class SchematicTools(sl.Component[sl.ComponentsV2Target, sd.Request]):
    """Own schematic actions, permission checks, and the latest generated result."""

    _schematic_result: sl.TextLike | None = sl.state(None, opaque=True)
    _asset: sl.document.Asset | None = sl.state(None, opaque=True)
    _video_job_id: int | None = sl.state(None)

    def __init__(
        self,
        build_id: int,
        *,
        schematics: SchematicService,
        measure_timing: bool,
        actor_account_id: int | None,
        authorize: Callable[[PermissionNode], Awaitable[bool]],
        show_details: Callable[[], bool] | None = None,
    ) -> None:
        self._build_id = build_id
        self._schematics = schematics
        self._measure_timing = measure_timing
        self._actor_account_id = actor_account_id
        self._authorize = authorize
        self._show_details = show_details
        self._video_delivery_lock = asyncio.Lock()
        self._delivered_videos: set[int] = set()

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        if self._show_details is not None and not self._show_details():
            return ()
        nodes = list(self._schematic_nodes())
        if self._schematic_result is not None:
            nodes.append(sl.note(self._schematic_result))
        if self._asset is not None:
            nodes.append(
                sl.download(
                    tr(t"bot-submission-build-browse-download-result = Download result"),
                    self._asset,
                    key="build-download",
                )
            )
        return tuple(nodes)

    def _schematic_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        if not self._schematics.available:
            return ()
        videos_enabled = self._schematics.videos is not None and self._schematics.videos.enabled
        return (
            sl.action_controls(
                sl.action_control(
                    tr(t"bot-submission-build-browse-schematic-info = Schematic info"),
                    self._schematic_info,
                    key="schematic-info",
                ),
                sl.action_control(
                    tr(t"bot-submission-build-browse-download-schematic = Download schematic"),
                    self._schematic_download,
                    key="schematic-download",
                ),
                sl.action_control(
                    tr(t"bot-submission-build-browse-render-schematic = Render schematic"),
                    self._schematic_render,
                    key="schematic-render",
                ),
                sl.form(
                    tr(t"bot-submission-build-browse-convert-schematic = Convert schematic"),
                    self._convert_form(),
                    key="convert",
                    on_submit=self._convert,
                ),
                self._measure_timing
                and sl.form(
                    tr(t"bot-submission-build-browse-measure-timing = Measure timing"),
                    self._timing_form(),
                    key="timing",
                    on_submit=self._timing,
                ),
                videos_enabled
                and sl.form(
                    tr(t"schematic-video-action = Render video"),
                    _VideoForm().spec(),
                    key="schematic-video",
                    on_submit=self._video,
                ),
                videos_enabled
                and self._video_job_id is not None
                and sl.action_control(
                    tr(t"schematic-video-refresh = Check video"),
                    self._refresh_video,
                    key="video-refresh",
                ),
                key="schematic-actions",
            ),
        )

    @staticmethod
    def _convert_form() -> sl.forms.FormSpec:
        return _ConvertForm().spec()

    @staticmethod
    def _timing_form() -> sl.forms.FormSpec:
        return _TimingForm().spec()

    async def _primary(self, event: sl.ActionEvent, private_access: PermissionNode) -> StoredSchematic | None:
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
        self._asset = sl.document.Asset(
            "schematic-render",
            f"build-{self._build_id}-render.png",
            "image/png",
            sl.document.InlineAsset(rendered.png),
        )

    async def _video(self, event: sl.SubmitEvent) -> None:
        request = cast(sd.Request, event.context)
        actor = await actor_for_interaction(request)
        stored = await self._primary(event, BUILD_SCHEMATIC_VIEW_PRIVATE)
        service = self._schematics.videos
        if stored is None or service is None or actor.account_id is None:
            return
        form = _VideoForm.from_submission(event)
        try:
            interactions: list[VideoInteraction] = []
            for line in (form.interactions or "").splitlines():
                if not line.strip():
                    continue
                fields = line.split(maxsplit=1)
                position = _parse_position(fields[1]) if len(fields) == 2 else None
                interactions.append(VideoInteraction(tick=int(fields[0]), position=position))
            recipe = VideoRenderRequest(
                duration_ticks=form.seconds * 20,
                orbit_degrees=form.orbit,
                simulation_enabled=form.simulate,
                interactions=tuple(interactions) if form.simulate else (),
            )
        except ValueError:
            await event.notice(
                tr(
                    t"schematic-video-invalid = Use a tick followed by x y z on each line, with activations inside the clip."
                )
            )
            return
        status = await service.submit_video(
            self._build_id,
            stored.id,
            recipe,
            account_id=actor.account_id,
            can_view_private=await allows(request, BUILD_SCHEMATIC_VIEW_PRIVATE),
        )
        self._video_job_id = status.id
        job_id = status.id
        await event.notice(
            tr(t"schematic-video-queued = Video #{job_id} queued. I'll attach it here when it is ready.")
        )
        host = cast(_VideoTaskHost, request.client)
        host.background_tasks.start(self._follow_video(request, status.id), name=f"schematic-video-{status.id}")

    async def _follow_video(self, request: sd.Request, job_id: int) -> None:
        with anyio.move_on_after(600):
            while request.interaction is not None and not request.interaction.is_expired():
                if await self._deliver_video(request, job_id):
                    return
                await anyio.sleep(2)

    async def _refresh_video(self, event: sl.PressEvent) -> None:
        request = cast(sd.Request, event.context)
        if self._video_job_id is not None and not await self._deliver_video(request, self._video_job_id, repeat=True):
            await event.notice(tr(t"schematic-video-pending = The video is still rendering."))

    async def _deliver_video(self, request: sd.Request, job_id: int, *, repeat: bool = False) -> bool:
        async with self._video_delivery_lock:
            # Explicit checks should show the result even after automatic delivery.
            if not repeat and job_id in self._delivered_videos:
                return True
            delivered = await self._deliver_video_once(request, job_id)
            if delivered and request.interaction is not None and not request.interaction.is_expired():
                self._delivered_videos.add(job_id)
            return delivered

    async def _deliver_video_once(self, request: sd.Request, job_id: int) -> bool:
        service = self._schematics.videos
        actor = await actor_for_interaction(request)
        if service is None or actor.account_id is None:
            return True
        can_view = await allows(request, BUILD_SCHEMATIC_VIEW_PRIVATE)
        status = await service.get_video(job_id, account_id=actor.account_id, can_view_private=can_view)
        if status.status in ("queued", "running"):
            return False
        if status.input_candidates:
            candidates = "; ".join(" ".join(map(str, position)) for position in status.input_candidates)
            await request.respond(
                sl.paragraph(
                    tr(
                        t"schematic-video-input-choice = Choose an input position and request the video again: {candidates}"
                    )
                ),
                audience="personal",
            )
            return True
        if status.status != "succeeded":
            reason = _video_failure_reason(status.error, expired=status.status == "expired")
            await request.respond(
                sl.paragraph(tr(t"schematic-video-failure-detail = Video #{job_id}: {reason}")),
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
                    tr(
                        t"schematic-video-too-large = This video exceeds Discord's attachment limit. Download it while signed in: {url}"
                    )
                ),
                audience="personal",
            )
            return True
        asset = sl.document.Asset(
            "schematic-video",
            f"build-{self._build_id}-simulation.mp4",
            "video/mp4",
            sl.document.InlineAsset(content),
        )
        await request.respond(
            sl.download(
                tr(t"schematic-video-ready = Your schematic video is ready."),
                asset,
                key="schematic-video-download",
            ),
            audience="personal",
        )
        return True

    async def _convert(self, event: sl.SubmitEvent) -> None:
        if await self._primary(event, BUILD_SCHEMATIC_DOWNLOAD_PRIVATE) is None:
            return
        form = _ConvertForm.from_submission(event)
        file_format = form.format
        data_version = form.data_version
        version = form.version or None
        data, losses = await self._schematics.convert(
            self._build_id,
            ConvertRequest(target_format=file_format, target_data_version=data_version),
            version_label=version,
        )
        self._asset = self._schematic_asset(data, WRITABLE_EXTENSIONS[file_format])
        loss_summary = summarise_losses(losses)
        self._schematic_result = tr(
            t"bot-submission-build-browse-conversion-report = Conversion report: {loss_summary}"
        )

    async def _timing(self, event: sl.SubmitEvent) -> None:
        if (
            not await self._may(event, BUILD_SCHEMATIC_MEASURE_TIMING)
            or await self._primary(event, BUILD_SCHEMATIC_VIEW_PRIVATE) is None
        ):
            return
        form = _TimingForm.from_submission(event)
        raw = form.input or None
        position = _parse_position(raw)
        if raw is not None and position is None:
            await event.notice(
                tr(t"build-input-position-invalid = Input position must contain three integers, for example `12 5 -3`.")
            )
            return
        try:
            result = await self._schematics.measure_timing(self._build_id, input_position=position)
        except AmbiguousSimulationInputError as error:
            self._schematic_result = _describe_input_refusal(error)
            return
        self._schematic_result = _describe_timing(result)

    def _schematic_asset(self, data: bytes, extension: str) -> sl.document.Asset:
        return sl.document.Asset(
            "schematic",
            f"build-{self._build_id}.{extension}",
            "application/octet-stream",
            sl.document.InlineAsset(data),
        )

    async def _may(self, event: sl.ActionEvent, node: PermissionNode) -> bool:
        if await self._authorize(node):
            return True
        await event.notice(
            tr(t"build-operation-permission-denied = You are no longer allowed to perform this build operation.")
        )
        return False


class _ConvertForm(sl.forms.Form):
    title = tr(t"bot-submission-build-browse-convert-schematic = Convert schematic")

    format = sl.forms.ChoiceField(
        label=tr(t"bot-submission-build-browse-output-format = Output format"),
        default=SchematicFormat.LITEMATIC,
        options=tuple(
            sl.forms.ChoiceOption(file_format.value, file_format.value, file_format)
            for file_format in WRITABLE_EXTENSIONS
        ),
    )
    data_version = sl.forms.IntField(
        label=tr(t"build-minecraft-data-version-label = Minecraft data version"), required=False
    )
    version = sl.forms.TextField(
        label=tr(t"build-minecraft-version-label = Minecraft version name"),
        required=False,
        maximum=100,
    )


class _TimingForm(sl.forms.Form):
    title = tr(t"build-schematic-timing-action = Measure schematic timing")

    input = sl.forms.TextField(
        label=tr(t"bot-submission-build-browse-input-position-x-y-z = Input position (x y z)"),
        required=False,
        maximum=100,
    )


class _VideoForm(sl.forms.Form):
    title = tr(t"schematic-video-action = Render video")
    simulate = sl.forms.ChoiceField(
        label=tr(t"schematic-video-motion = Build motion"),
        default=True,
        options=(
            sl.forms.ChoiceOption("simulate", tr(t"schematic-video-simulate = Simulate inputs"), value=True),
            sl.forms.ChoiceOption("static", tr(t"schematic-video-static = Keep build static"), value=False),
        ),
    )
    seconds = sl.forms.IntField(
        label=tr(t"schematic-video-duration = Duration (seconds)"), default=6, minimum=1, maximum=10
    )
    orbit = sl.forms.IntField(
        label=tr(t"schematic-video-orbit = Camera orbit (degrees; 0 for fixed)"), default=180, minimum=-360, maximum=360
    )
    interactions = sl.forms.TextField(
        label=tr(t"schematic-video-inputs = Activations: tick x y z, one per line"),
        default="20",
        required=False,
        maximum=1000,
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
