"""Durable client and worker runner for native schematic operations."""

import asyncio
import dataclasses
import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, cast

from squid.artifacts import ArtifactStore
from squid.config import SchematicConfig
from squid.core.concurrency import run_all, run_all_settled
from squid.core.errors import InfrastructureError, SquidError
from squid.schematics.application.commands import RenderRequest, SimulationRequest
from squid.schematics.application.jobs import (
    ClaimedSchematicJob,
    SchematicJobErrorKind,
    SchematicJobOperation,
    SchematicJobService,
    SchematicJobSnapshot,
)
from squid.schematics.application.ports import SchematicAnalyzer
from squid.schematics.domain.models import (
    AnalyzerCapabilities,
    AutostackLattice,
    FingerprintPreset,
    SchematicAnalysis,
    SchematicComparison,
    SchematicFormat,
    SchematicLimits,
    SimulationResult,
    VersionLossEntry,
)
from squid.schematics.domain.values import VerifiedResourcePack
from squid.schematics.errors import (
    InvalidSchematicError,
    SchematicSupportUnavailableError,
    SchematicTimeoutError,
    SchematicTooLargeError,
    SchematicWorkerCrashedError,
)
from squid.schematics.infrastructure import wire
from squid.schematics.infrastructure.worker import current_schematic_job_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _JobResponse:
    result: Mapping[str, Any]
    payload: bytes | None


class QueuedSchematicAnalyzer:
    """Submit native-engine requests to the database worker and await durable results."""

    def __init__(
        self,
        jobs: SchematicJobService,
        artifacts: ArtifactStore,
        config: SchematicConfig,
    ) -> None:
        self._jobs = jobs
        self._artifacts = artifacts
        self._config = config

    async def capabilities(self) -> AnalyzerCapabilities:
        if not self._config.enabled:
            return AnalyzerCapabilities(available=False, unavailable_reason="Schematic support is disabled.")
        try:
            response = await self._request(
                "capabilities", {}, (), timeout_seconds=self._config.job_wait_timeout_seconds
            )
        except SquidError as error:
            return AnalyzerCapabilities(available=False, unavailable_reason=error.public_detail())
        return wire.decode_capabilities(cast(Mapping[str, Any], response.result["capabilities"]))

    async def analyze(
        self,
        data: bytes,
        *,
        limits: SchematicLimits,
        with_lattice: bool = False,
        source_format: SchematicFormat | None = None,
    ) -> SchematicAnalysis:
        self._require_enabled()
        response = await self._request(
            "analyze",
            {
                "limits": dataclasses.asdict(limits),
                "with_lattice": with_lattice,
                "source_format": source_format.value if source_format is not None else None,
                "lattice_max_block_count": self._config.lattice_max_block_count,
            },
            (data,),
            timeout_seconds=self._config.job_wait_timeout_seconds,
        )
        return wire.decode_analysis(cast(Mapping[str, Any], response.result["analysis"]))

    async def convert(
        self,
        data: bytes,
        *,
        target: SchematicFormat,
        data_version: int | None = None,
    ) -> tuple[bytes, tuple[VersionLossEntry, ...]]:
        self._require_enabled()
        response = await self._request(
            "convert",
            {"target": target.value, "data_version": data_version},
            (data,),
            timeout_seconds=self._config.job_wait_timeout_seconds,
        )
        return response.payload or b"", wire.decode_losses(response.result["losses"])

    async def compare(
        self,
        left: bytes,
        right: bytes,
        *,
        preset: FingerprintPreset,
        timeout_seconds: float | None = None,
    ) -> SchematicComparison:
        self._require_enabled()
        wait_timeout = min(
            self._config.job_wait_timeout_seconds,
            timeout_seconds if timeout_seconds is not None else self._config.job_wait_timeout_seconds,
        )
        response = await self._request(
            "compare",
            {"preset": preset.value, "timeout_seconds": timeout_seconds},
            (left, right),
            timeout_seconds=wait_timeout,
        )
        return wire.decode_comparison(cast(Mapping[str, Any], response.result["comparison"]))

    async def render(
        self, data: bytes, *, request: RenderRequest, resource_pack: VerifiedResourcePack | None = None
    ) -> bytes:
        self._require_enabled()
        inputs = (data,) if resource_pack is None else (data, resource_pack.data)
        pack_metadata = wire.encode_resource_pack(resource_pack) if resource_pack is not None else None
        response = await self._request(
            "render",
            {"request": wire.encode_render_request(request), "resource_pack": pack_metadata},
            inputs,
            timeout_seconds=self._config.job_wait_timeout_seconds,
        )
        return response.payload or b""

    async def simulate(self, data: bytes, *, request: SimulationRequest) -> SimulationResult:
        self._require_enabled()
        response = await self._request(
            "simulate",
            {"request": wire.encode_simulation_request(request)},
            (data,),
            timeout_seconds=self._config.job_wait_timeout_seconds,
        )
        return wire.decode_simulation(cast(Mapping[str, Any], response.result["simulation"]))

    async def autostack(self, data: bytes, *, lattice: AutostackLattice, counts: tuple[int, ...]) -> bytes:
        self._require_enabled()
        response = await self._request(
            "autostack",
            {"lattice": wire.encode_lattice(lattice), "counts": list(counts)},
            (data,),
            timeout_seconds=self._config.job_wait_timeout_seconds,
        )
        return response.payload or b""

    async def aclose(self) -> None:
        """The queued client owns no process-local resources."""

    async def _request(
        self,
        operation: SchematicJobOperation,
        params: Mapping[str, Any],
        payloads: Sequence[bytes],
        *,
        timeout_seconds: float,
    ) -> _JobResponse:
        input_keys = tuple(await run_all([partial(self._stage_input, payload) for payload in payloads]))
        job_id = await self._jobs.submit(operation, params, input_keys)
        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    snapshot = await self._jobs.get(job_id)
                    if snapshot is None:
                        msg = "The schematic job result disappeared before it was collected."
                        raise InfrastructureError(msg, resource="schematic", context={"job_id": job_id})
                    if snapshot.dead_at is not None:
                        raise _job_failure(operation, timeout_seconds, snapshot)
                    if snapshot.completed_at is not None:
                        payload = await self._load_result(snapshot)
                        return _JobResponse(snapshot.result or {}, payload)
                    await asyncio.sleep(self._config.job_poll_interval_seconds)
        except TimeoutError:
            raise SchematicTimeoutError(operation=operation, timeout_seconds=timeout_seconds) from None

    async def _stage_input(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        key = f"schematics/{digest[:2]}/{digest}"
        metadata = await self._artifacts.put(key, data, content_type="application/octet-stream")
        if metadata.byte_size != len(data) or metadata.sha256 not in (None, digest):
            msg = "Object storage did not confirm a staged schematic job input."
            raise InfrastructureError(msg, resource="schematic", context={"sha256": digest})
        return key

    async def _load_result(self, snapshot: SchematicJobSnapshot) -> bytes | None:
        if snapshot.result_object_key is None:
            return None
        payload = await self._artifacts.get(
            snapshot.result_object_key,
            max_bytes=self._config.max_job_artifact_bytes,
        )
        if payload is None:
            msg = "The schematic worker completed without a readable result artifact."
            raise InfrastructureError(msg, resource="schematic", context={"job_id": snapshot.id})
        return payload

    def _require_enabled(self) -> None:
        if not self._config.enabled:
            raise SchematicSupportUnavailableError(developer_action="Enable schematic support in configuration.")


class SchematicJobRunner:
    """Execute claimed jobs with the worker process's native analyzer."""

    def __init__(
        self,
        jobs: SchematicJobService,
        artifacts: ArtifactStore,
        analyzer: SchematicAnalyzer,
        config: SchematicConfig,
    ) -> None:
        self._jobs = jobs
        self._artifacts = artifacts
        self._analyzer = analyzer
        self._config = config

    async def process_batch(self, *, limit: int = 8) -> None:
        jobs = await self._jobs.claim(limit=limit)
        # Settled rather than cancelled on the first failure: the jobs are
        # independent, and a cancelled one never reaches the handler that fails it,
        # so it would hold its claim and its result object until the next cleanup
        # pass. Failures still raise once the batch is done.
        await run_all_settled([partial(self._process, job) for job in jobs])

    async def cleanup(self) -> None:
        for object_key in await self._jobs.cleanup():
            await self._artifacts.delete(object_key)

    async def _process(self, job: ClaimedSchematicJob) -> None:
        token = current_schematic_job_id.set(job.id)
        try:
            await self._process_claimed(job)
        finally:
            current_schematic_job_id.reset(token)

    async def _process_claimed(self, job: ClaimedSchematicJob) -> None:
        result_object_key: str | None = None
        try:
            inputs = await run_all([partial(self._load_input, job, key) for key in job.input_keys])
            result, output = await self._execute(job, inputs)
            if output is not None:
                result_object_key = f"schematic-jobs/results/{job.id}"
                content_type = "image/png" if job.operation == "render" else "application/octet-stream"
                await self._artifacts.put(result_object_key, output, content_type=content_type)
            applied = await self._jobs.complete(job, result, result_object_key)
            if not applied and result_object_key is not None:
                await self._artifacts.delete(result_object_key)
        except Exception as error:
            kind, context, terminal = _classify_error(error)
            dead = await self._jobs.fail(
                job,
                error,
                error_kind=kind,
                error_context=context,
                terminal=terminal,
            )
            if dead:
                logger.warning(
                    "Schematic job moved to dead state",
                    extra={"squid.schematic.job_id": job.id, "squid.schematic.operation": job.operation},
                    exc_info=True,
                )

    async def _load_input(self, job: ClaimedSchematicJob, key: str) -> bytes:
        payload = await self._artifacts.get(key, max_bytes=self._config.max_job_artifact_bytes)
        if payload is None:
            msg = "A schematic job input artifact is missing."
            raise InfrastructureError(msg, resource="schematic", context={"job_id": job.id, "object_key": key})
        return payload

    async def _execute(
        self,
        job: ClaimedSchematicJob,
        inputs: Sequence[bytes],
    ) -> tuple[Mapping[str, Any], bytes | None]:
        params = job.params
        operation = wire.validate_durable_request_payloads(job.operation, inputs)
        if operation == "capabilities":
            return {"capabilities": wire.encode_capabilities(await self._analyzer.capabilities())}, None
        if operation == "analyze":
            limits, with_lattice, source_format, _ = wire.decode_analyze_params(params)
            analysis = await self._analyzer.analyze(
                inputs[0],
                limits=limits,
                with_lattice=with_lattice,
                source_format=source_format,
            )
            return {"analysis": wire.encode_analysis(analysis)}, None
        if operation == "convert":
            target, data_version = wire.decode_convert_params(params)
            converted, losses = await self._analyzer.convert(
                inputs[0],
                target=target,
                data_version=data_version,
            )
            return {"losses": wire.encode_losses(losses)}, converted
        if operation == "compare":
            comparison = await self._analyzer.compare(
                inputs[0],
                inputs[1],
                preset=wire.decode_compare_params(params),
                timeout_seconds=wire.decode_optional_timeout(params.get("timeout_seconds")),
            )
            return {"comparison": wire.encode_comparison(comparison)}, None
        if operation == "render":
            pack_metadata = params.get("resource_pack")
            expected_inputs = 2 if pack_metadata is not None else 1
            if len(inputs) != expected_inputs:
                msg = "Durable render resource-pack metadata does not match its staged inputs."
                raise InvalidSchematicError(msg)
            pack = wire.decode_resource_pack(pack_metadata, inputs[1]) if pack_metadata is not None else None
            rendered = await self._analyzer.render(
                inputs[0],
                request=wire.decode_render_request(params.get("request")),
                resource_pack=pack,
            )
            return {}, rendered
        if operation == "simulate":
            simulation = await self._analyzer.simulate(
                inputs[0], request=wire.decode_simulation_request(params.get("request"))
            )
            return {"simulation": wire.encode_simulation(simulation)}, None
        if operation == "autostack":
            lattice, counts = wire.decode_autostack_params(params)
            output = await self._analyzer.autostack(
                inputs[0],
                lattice=lattice,
                counts=counts,
            )
            return {}, output
        msg = f"Unsupported schematic job operation {operation}."
        raise InvalidSchematicError(msg)


def _classify_error(error: Exception) -> tuple[SchematicJobErrorKind, Mapping[str, Any], bool]:
    context = dict(error.context) if isinstance(error, SquidError) else {}
    if isinstance(error, SchematicTooLargeError):
        return "too_large", context, True
    if isinstance(error, InvalidSchematicError):
        return "invalid", context, True
    if isinstance(error, SchematicSupportUnavailableError):
        return "unavailable", context, True
    if isinstance(error, SchematicTimeoutError):
        return "timeout", context, True
    if isinstance(error, SchematicWorkerCrashedError):
        return "crashed", context, True
    if isinstance(error, SquidError):
        return "internal", context, True
    return "internal", context, False


def _job_failure(
    operation: SchematicJobOperation,
    timeout_seconds: float,
    snapshot: SchematicJobSnapshot,
) -> Exception:
    context = snapshot.error_context
    if snapshot.error_kind == "too_large":
        return SchematicTooLargeError(
            actual=int(context.get("actual", 0)),
            limit=int(context.get("limit", 0)),
            measure=str(context.get("measure", "size")),
        )
    if snapshot.error_kind == "invalid":
        return InvalidSchematicError(snapshot.last_error, context={**context, "job_id": snapshot.id})
    if snapshot.error_kind == "unavailable":
        return SchematicSupportUnavailableError(snapshot.last_error, context={"job_id": snapshot.id})
    if snapshot.error_kind == "timeout":
        return SchematicTimeoutError(operation=operation, timeout_seconds=timeout_seconds)
    if snapshot.error_kind == "crashed":
        exit_code = context.get("exit_code")
        return SchematicWorkerCrashedError(
            operation=operation,
            exit_code=int(exit_code) if exit_code is not None else None,
        )
    return InfrastructureError(
        snapshot.last_error or "The schematic worker failed.",
        resource="schematic",
        context={**context, "job_id": snapshot.id, "operation": operation},
    )
