"""Schematic actions and their results within a build detail panel."""

from collections.abc import Awaitable, Callable
from typing import override

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
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    BUILD_SCHEMATIC_DOWNLOAD_PRIVATE,
    BUILD_SCHEMATIC_MEASURE_TIMING,
    BUILD_SCHEMATIC_VIEW_PRIVATE,
)
from squid.schematics.application import ConvertRequest, SchematicService, StoredSchematic, summarise_losses
from squid.schematics.domain.models import SchematicFormat
from squid.schematics.errors import AmbiguousSimulationInputError, SchematicRenderRefusedError


class SchematicTools(sl.Component[sl.ComponentsV2Target, sd.Request]):
    """Own schematic actions, permission checks, and the latest generated result."""

    _schematic_result: sl.TextLike | None = sl.state(None, opaque=True)
    _asset: sl.document.Asset | None = sl.state(None, opaque=True)

    def __init__(
        self,
        build_id: int,
        *,
        schematics: SchematicService,
        measure_timing: bool,
        actor_account_id: int | None,
        authorize: Callable[[PermissionNode], Awaitable[bool]],
    ) -> None:
        self._build_id = build_id
        self._schematics = schematics
        self._measure_timing = measure_timing
        self._actor_account_id = actor_account_id
        self._authorize = authorize

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
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
        actions = [
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
        ]
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = [
            sl.action_controls(*actions, key="schematic-actions")
        ]
        nodes.append(
            sl.form(
                tr(t"bot-submission-build-browse-convert-schematic = Convert schematic"),
                self._convert_form(),
                key="convert",
                on_submit=self._convert,
            )
        )
        if self._measure_timing:
            nodes.append(
                sl.form(
                    tr(t"bot-submission-build-browse-measure-timing = Measure timing"),
                    self._timing_form(),
                    key="timing",
                    on_submit=self._timing,
                )
            )
        return tuple(nodes)

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
