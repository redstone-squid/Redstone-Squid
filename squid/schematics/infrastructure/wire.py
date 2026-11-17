"""Encode worker frames and domain values without importing the native engine.

The pool and its child processes exchange a JSON header describing the operation or result,
followed by raw binary payloads. Keeping schematic files and rendered images out of JSON avoids
the size increase from base64 encoding those large payloads.

A frame is::

    | 4 bytes BE header length | 4 bytes BE body length | header JSON (UTF-8) | body |

Schematic inputs and binary results are concatenated in the body. The header's parts array
records their lengths, including both inputs of a comparison. Resource packs are an exception:
the subprocess request carries their bytes as base64 in its parameters, while durable jobs
can store them as a second input object.

An optional trace mapping carries propagation headers separately from frame fields.
"""

import asyncio
import base64
import binascii
import dataclasses
import json
from collections.abc import Mapping, Sequence
from typing import IO, Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, TypeAdapter, ValidationError, model_validator

from squid.schematics.application.commands import RenderRequest, SimulationRequest
from squid.schematics.application.video import VideoRenderRequest
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
from squid.schematics.domain.values import RESOURCE_PACK_MEDIA_TYPE, RgbaColor, VerifiedResourcePack
from squid.schematics.errors import AmbiguousSimulationInputError

LENGTH_PREFIX_BYTES = 4
MAX_FRAME_BYTES = 256 * 1024 * 1024
"""Maximum combined header and body size, excluding the eight-byte length prefix."""

type Operation = Literal[
    "capabilities", "analyze", "convert", "compare", "render", "render_video", "simulate", "autostack"
]
type ErrorKind = Literal["invalid", "ambiguous_simulation_input", "too_large", "unavailable", "internal"]
"""Error kinds the supervisor accepts when reconstructing a typed exception."""

_OPERATIONS: frozenset[str] = frozenset(
    {"capabilities", "analyze", "convert", "compare", "render", "render_video", "simulate", "autostack"}
)
_REQUEST_PAYLOAD_ARITY: Mapping[Operation, int] = {
    "capabilities": 0,
    "analyze": 1,
    "convert": 1,
    "compare": 2,
    "render": 1,
    "render_video": 1,
    "simulate": 1,
    "autostack": 1,
}
_RESPONSE_PAYLOAD_ARITY: Mapping[Operation, int] = {
    "capabilities": 0,
    "analyze": 0,
    "convert": 1,
    "compare": 0,
    "render": 1,
    "render_video": 1,
    "simulate": 0,
    "autostack": 1,
}
_DURABLE_REQUEST_PAYLOAD_ARITY: Mapping[Operation, tuple[int, ...]] = {
    **{operation: (arity,) for operation, arity in _REQUEST_PAYLOAD_ARITY.items()},
    "render": (1, 2),
    "render_video": (1, 2),
}
MAX_FRAME_PARTS = max(_REQUEST_PAYLOAD_ARITY.values())

# Wire models reject malformed JSON before values reach the native engine; domain
# dataclasses remain the types used by the application after decoding.
type _FiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]
type _UnitFloat = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0.0, le=1.0)]
type _NonnegativeInt = Annotated[int, Field(strict=True, ge=0)]
type _NonnegativeFloat = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0.0)]
type _PositiveInt = Annotated[int, Field(strict=True, ge=1)]
type _PositiveFloat = Annotated[float, Field(strict=True, allow_inf_nan=False, gt=0.0)]
type _StrictVector3 = tuple[StrictInt, StrictInt, StrictInt]


class _WorkerJson(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _RenderRequestJson(_WorkerJson):
    width: StrictInt
    height: StrictInt
    projection: Literal["orthographic", "perspective"]
    sphere_fit: StrictBool
    yaw: _FiniteFloat | None = None
    pitch: _FiniteFloat | None = None
    zoom: _FiniteFloat | None = None
    background: tuple[_UnitFloat, _UnitFloat, _UnitFloat, _UnitFloat]


class _SimulationRequestJson(_WorkerJson):
    input_position: _StrictVector3 | None = None
    watch_positions: tuple[_StrictVector3, ...]
    max_ticks: _PositiveInt


class _LimitsJson(_WorkerJson):
    max_upload_bytes: _PositiveInt
    max_inflated_bytes: _PositiveInt
    max_allocated_volume: _PositiveInt
    max_axis_length: _PositiveInt
    max_sniff_bytes: _PositiveInt


class _AnalyzeParamsJson(_WorkerJson):
    limits: _LimitsJson
    with_lattice: StrictBool
    source_format: SchematicFormat | None = None
    lattice_max_block_count: _PositiveInt = 200_000


class _ConvertParamsJson(_WorkerJson):
    target: SchematicFormat
    data_version: _NonnegativeInt | None = None


class _CompareParamsJson(_WorkerJson):
    preset: FingerprintPreset


class _DimensionsJson(_WorkerJson):
    width: _NonnegativeInt
    height: _NonnegativeInt
    length: _NonnegativeInt


class _SignJson(_WorkerJson):
    x: StrictInt
    y: StrictInt
    z: StrictInt
    text: str


class _MetricsJson(_WorkerJson):
    source_format: SchematicFormat
    byte_size: _NonnegativeInt
    sha256: str
    dimensions: _DimensionsJson
    allocated_dimensions: _DimensionsJson
    block_count: _NonnegativeInt
    bounding_volume: _NonnegativeInt
    entity_count: _NonnegativeInt
    palette_size: _NonnegativeInt
    region_names: tuple[str, ...] = ()
    source_data_version: StrictInt | None = None
    declared_name: str | None = None
    declared_author: str | None = None
    signs: tuple[_SignJson, ...] = ()
    block_counts: dict[str, _NonnegativeInt] | None = None


class _FingerprintsJson(_WorkerJson):
    structural: str
    shape: str
    exact: str
    signature_structural: str | None = None


class _LatticeJson(_WorkerJson):
    mode: Literal["1d", "2d"]
    vectors: tuple[_StrictVector3, ...]
    coverage: _UnitFloat
    cell_min: _StrictVector3
    cell_max: _StrictVector3
    region_min: _StrictVector3
    region_max: _StrictVector3
    label: str | None = None

    @model_validator(mode="after")
    def require_period_vectors(self) -> _LatticeJson:
        expected = 1 if self.mode == "1d" else 2
        if len(self.vectors) != expected:
            msg = f"A {self.mode} lattice requires exactly {expected} vector(s)."
            raise ValueError(msg)
        return self


class _AutostackParamsJson(_WorkerJson):
    lattice: _LatticeJson
    counts: tuple[_PositiveInt, ...]

    @model_validator(mode="after")
    def require_repeat_counts(self) -> _AutostackParamsJson:
        expected = 1 if self.lattice.mode == "1d" else 2
        if len(self.counts) != expected:
            msg = f"A {self.lattice.mode} lattice requires exactly {expected} repeat count(s)."
            raise ValueError(msg)
        return self


class _AnalysisJson(_WorkerJson):
    metrics: _MetricsJson
    fingerprints: _FingerprintsJson
    analyzer_version: str
    analysis_schema_version: Annotated[int, Field(strict=True, ge=1)]
    lattice: _LatticeJson | None = None


class _ComparisonJson(_WorkerJson):
    preset: FingerprintPreset
    identical: StrictBool
    footprint_distance: _NonnegativeFloat
    edit_distance: _NonnegativeInt | None = None
    support: _UnitFloat | None = None
    summary: str | None = None


class _LossJson(_WorkerJson):
    version: str
    kind: str
    severity: Literal["Loss", "Approximated"]
    path: str
    detail: str


class _CapabilitiesJson(_WorkerJson):
    available: StrictBool
    analyzer_version: str | None = None
    can_render: StrictBool
    can_simulate: StrictBool
    render_backends: tuple[str, ...]
    unavailable_reason: str | None = None


class _SimulationSampleJson(_WorkerJson):
    tick: _NonnegativeInt
    x: StrictInt
    y: StrictInt
    z: StrictInt
    powered: StrictBool
    signal_strength: Annotated[int, Field(strict=True, ge=0, le=15)]


class _SimulationJson(_WorkerJson):
    ticks_run: _NonnegativeInt
    settled_tick: _NonnegativeInt | None
    input_position: _StrictVector3 | None = None
    input_source: Literal["insign", "heuristic", "manual"] | None = None
    last_piston_tick: _NonnegativeInt | None = None
    block_changes: _NonnegativeInt
    piston_events: _NonnegativeInt
    redstone_events: _NonnegativeInt
    trustworthy: StrictBool
    samples: tuple[_SimulationSampleJson, ...]
    notes: tuple[str, ...]


class _ResourcePackJson(_WorkerJson):
    sha256: str
    media_type: str

    @model_validator(mode="after")
    def require_media_type(self) -> _ResourcePackJson:
        if self.media_type != RESOURCE_PACK_MEDIA_TYPE:
            msg = f"Unsupported resource-pack media type {self.media_type!r}."
            raise ValueError(msg)
        return self


class _SimulationInputErrorJson(_WorkerJson):
    candidates: tuple[_StrictVector3, ...]
    rejected: _StrictVector3 | None = None


class _WorkerErrorJson(_WorkerJson):
    kind: ErrorKind
    message: str
    context: dict[str, Any]


_ANALYSIS = TypeAdapter(SchematicAnalysis)
_LATTICE = TypeAdapter(AutostackLattice)
_COMPARISON = TypeAdapter(SchematicComparison)
_LOSSES = TypeAdapter(tuple[VersionLossEntry, ...])
_CAPABILITIES = TypeAdapter(AnalyzerCapabilities)
_SIMULATION = TypeAdapter(SimulationResult)
_OPTIONAL_TIMEOUT = TypeAdapter(_PositiveFloat | None)


@dataclasses.dataclass(frozen=True, slots=True)
class Frame:
    """One request or response: a JSON header plus zero or more binary payloads."""

    header: Mapping[str, Any]
    payloads: tuple[bytes, ...] = ()

    def encode(self) -> bytes:
        header = {**self.header, "parts": [len(payload) for payload in self.payloads]}
        header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
        body_length = sum(len(payload) for payload in self.payloads)
        if len(header_bytes) + body_length > MAX_FRAME_BYTES:
            msg = "Frame exceeds the maximum size."
            raise ValueError(msg)
        body = b"".join(self.payloads)
        return (
            len(header_bytes).to_bytes(LENGTH_PREFIX_BYTES, "big")
            + body_length.to_bytes(LENGTH_PREFIX_BYTES, "big")
            + header_bytes
            + body
        )


class FrameStreamClosed(Exception):
    """The peer closed the stream, cleanly or otherwise."""


@dataclasses.dataclass(frozen=True, slots=True)
class WorkerRequest:
    """A request whose ID, operation, parameter object, and number of payloads have been checked."""

    request_id: int
    operation: Operation
    params: Mapping[str, Any]
    job_id: int | None

    @classmethod
    def from_frame(cls, frame: Frame) -> WorkerRequest:
        """Check a request header and its expected payload count, raising FrameStreamClosed if invalid."""
        try:
            request_id = _integer(frame.header.get("id"), "request id", minimum=0)
            operation = _operation(frame.header.get("op"))
            params = _mapping(frame.header.get("params"), "request params")
            job_id_value = frame.header.get("job_id")
            job_id = None if job_id_value is None else _integer(job_id_value, "job id", minimum=0)
            _payload_arity(frame.payloads, _REQUEST_PAYLOAD_ARITY[operation], f"{operation} request")
        except (TypeError, ValueError, KeyError) as exc:
            raise FrameStreamClosed(str(exc)) from exc
        return cls(request_id, operation, params, job_id)


async def read_frame(stream: asyncio.StreamReader) -> Frame:
    """Read exactly one frame, raising :class:`FrameStreamClosed` at end of stream."""
    try:
        prefix = await stream.readexactly(2 * LENGTH_PREFIX_BYTES)
        header_length, body_length = _frame_lengths(prefix)
        header_bytes = await stream.readexactly(header_length)
        header, parts = _decode_frame_header(header_bytes, body_length)
        body = await stream.readexactly(body_length)
    except asyncio.IncompleteReadError as exc:
        raise FrameStreamClosed(str(exc)) from exc
    return Frame(header, _split_payloads(body, parts))


def read_frame_sync(stream: IO[bytes]) -> Frame | None:
    """Read one blocking frame, returning `None` only for a clean end of stream."""
    prefix = _read_exactly(stream, 2 * LENGTH_PREFIX_BYTES)
    if not prefix:
        return None
    if len(prefix) != 2 * LENGTH_PREFIX_BYTES:
        msg = "The peer closed during a frame prefix."
        raise FrameStreamClosed(msg)
    header_length, body_length = _frame_lengths(prefix)
    header_bytes = _read_exactly(stream, header_length)
    if len(header_bytes) != header_length:
        msg = "The peer closed during a frame header."
        raise FrameStreamClosed(msg)
    header, parts = _decode_frame_header(header_bytes, body_length)
    body = _read_exactly(stream, body_length)
    if len(body) != body_length:
        msg = "The peer closed during a frame body."
        raise FrameStreamClosed(msg)
    return Frame(header, _split_payloads(body, parts))


def validate_durable_request_payloads(operation: object, payloads: Sequence[bytes]) -> Operation:
    """Validate durable-job inputs, where a render may carry a pack as a second object."""
    decoded = _operation(operation)
    expected = _DURABLE_REQUEST_PAYLOAD_ARITY[decoded]
    if len(payloads) not in expected:
        choices = " or ".join(str(count) for count in expected)
        msg = f"Expected durable {decoded} request to carry {choices} payload(s), got {len(payloads)}."
        raise ValueError(msg)
    return decoded


def validate_worker_response(frame: Frame, *, request_id: int, operation: Operation) -> None:
    """Check the response status, request ID, result or error object, and payload count."""
    try:
        response_id = _integer(frame.header.get("id"), "response id", minimum=0)
        _matching_request_id(response_id, request_id)
        ok = _boolean(frame.header.get("ok"), "response ok")
        if ok:
            _mapping(frame.header.get("result"), "response result")
            _payload_arity(frame.payloads, _RESPONSE_PAYLOAD_ARITY[operation], f"{operation} response")
        else:
            decode_error(frame.header.get("error"))
            _payload_arity(frame.payloads, 0, f"{operation} error response")
    except (TypeError, ValueError, KeyError) as exc:
        raise FrameStreamClosed(str(exc)) from exc


def _frame_lengths(prefix: bytes) -> tuple[int, int]:
    header_length = int.from_bytes(prefix[:LENGTH_PREFIX_BYTES], "big")
    body_length = int.from_bytes(prefix[LENGTH_PREFIX_BYTES:], "big")
    if header_length + body_length > MAX_FRAME_BYTES:
        msg = "Frame exceeds the maximum size; the stream is out of sync."
        raise FrameStreamClosed(msg)
    return header_length, body_length


def _decode_frame_header(header_bytes: bytes, body_length: int) -> tuple[Mapping[str, Any], tuple[int, ...]]:
    try:
        value = json.loads(header_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FrameStreamClosed(str(exc)) from exc
    if not isinstance(value, dict):
        msg = "Frame header must be a JSON object."
        raise FrameStreamClosed(msg)
    header = cast(Mapping[str, Any], value)
    raw_parts = header.get("parts")
    if not isinstance(raw_parts, list):
        msg = "Frame parts must be a JSON array."
        raise FrameStreamClosed(msg)
    if len(raw_parts) > MAX_FRAME_PARTS:
        msg = f"Frame has more than {MAX_FRAME_PARTS} payload parts."
        raise FrameStreamClosed(msg)
    parts: list[int] = []
    for raw_size in cast(list[object], raw_parts):
        if not isinstance(raw_size, int) or isinstance(raw_size, bool) or raw_size < 0:
            msg = "Frame part lengths must be non-negative integers."
            raise FrameStreamClosed(msg)
        if raw_size > body_length:
            msg = "Frame part length exceeds the declared body length."
            raise FrameStreamClosed(msg)
        parts.append(raw_size)
    if sum(parts) != body_length:
        msg = "Frame payload part lengths do not equal the declared body length."
        raise FrameStreamClosed(msg)
    return header, tuple(parts)


def _split_payloads(body: bytes, parts: tuple[int, ...]) -> tuple[bytes, ...]:
    payloads: list[bytes] = []
    offset = 0
    for size in parts:
        next_offset = offset + size
        payloads.append(body[offset:next_offset])
        offset = next_offset
    return tuple(payloads)


def _read_exactly(stream: IO[bytes], size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def encode_analysis(analysis: SchematicAnalysis) -> Mapping[str, Any]:
    """Convert an analysis and its nested values into JSON-safe primitives."""
    return _AnalysisJson.model_validate(dataclasses.asdict(analysis)).model_dump(mode="json")


def decode_analysis(payload: Mapping[str, Any]) -> SchematicAnalysis:
    return _ANALYSIS.validate_python(_AnalysisJson.model_validate(payload).model_dump())


def encode_lattice(lattice: AutostackLattice) -> Mapping[str, Any]:
    return _LatticeJson.model_validate(dataclasses.asdict(lattice)).model_dump(mode="json")


def decode_lattice(payload: object) -> AutostackLattice:
    return _LATTICE.validate_python(_LatticeJson.model_validate(payload).model_dump())


def encode_comparison(comparison: SchematicComparison) -> Mapping[str, Any]:
    return _ComparisonJson.model_validate(dataclasses.asdict(comparison)).model_dump(mode="json")


def decode_comparison(payload: Mapping[str, Any]) -> SchematicComparison:
    return _COMPARISON.validate_python(_ComparisonJson.model_validate(payload).model_dump())


def encode_losses(losses: Sequence[VersionLossEntry]) -> list[Mapping[str, Any]]:
    return [_LossJson.model_validate(dataclasses.asdict(loss)).model_dump(mode="json") for loss in losses]


def decode_losses(payload: object) -> tuple[VersionLossEntry, ...]:
    return _LOSSES.validate_python(
        [_LossJson.model_validate(value).model_dump() for value in _array(payload, "conversion losses")]
    )


def encode_capabilities(capabilities: AnalyzerCapabilities) -> Mapping[str, Any]:
    return _CapabilitiesJson.model_validate(dataclasses.asdict(capabilities)).model_dump(mode="json")


def decode_capabilities(payload: Mapping[str, Any]) -> AnalyzerCapabilities:
    return _CAPABILITIES.validate_python(_CapabilitiesJson.model_validate(payload).model_dump())


def encode_render_request(request: RenderRequest) -> Mapping[str, Any]:
    """Encode a render request without changing the established RGBA array shape."""
    return _RenderRequestJson(
        width=request.width,
        height=request.height,
        projection=request.projection,
        sphere_fit=request.sphere_fit,
        yaw=request.yaw,
        pitch=request.pitch,
        zoom=request.zoom,
        background=request.background.as_tuple(),
    ).model_dump(mode="json")


def decode_render_request(payload: object) -> RenderRequest:
    """Decode and validate a render request received across a worker boundary."""
    value = _RenderRequestJson.model_validate(payload)
    return RenderRequest(
        width=value.width,
        height=value.height,
        projection=value.projection,
        sphere_fit=value.sphere_fit,
        yaw=value.yaw,
        pitch=value.pitch,
        zoom=value.zoom,
        background=RgbaColor.from_channels(value.background),
    )


def encode_simulation_request(request: SimulationRequest) -> Mapping[str, Any]:
    """Encode a simulation request into explicit JSON arrays and scalars."""
    return _SimulationRequestJson(
        input_position=request.input_position,
        watch_positions=request.watch_positions,
        max_ticks=request.max_ticks,
    ).model_dump(mode="json")


def decode_simulation_request(payload: object) -> SimulationRequest:
    """Decode and validate a simulation request received across a worker boundary."""
    value = _SimulationRequestJson.model_validate(payload)
    return SimulationRequest(
        input_position=value.input_position,
        watch_positions=value.watch_positions,
        max_ticks=value.max_ticks,
    )


def decode_limits(payload: object) -> SchematicLimits:
    """Decode attacker-facing worker resource limits without coercing JSON values."""
    return SchematicLimits(**_LimitsJson.model_validate(payload).model_dump())


def decode_analyze_params(
    payload: Mapping[str, Any],
) -> tuple[SchematicLimits, bool, SchematicFormat | None, int]:
    """Decode the parameters for an analyze request."""
    value = _AnalyzeParamsJson.model_validate(payload)
    return (
        SchematicLimits(**value.limits.model_dump()),
        value.with_lattice,
        value.source_format,
        value.lattice_max_block_count,
    )


def decode_convert_params(payload: Mapping[str, Any]) -> tuple[SchematicFormat, int | None]:
    """Decode the parameters for a conversion request."""
    value = _ConvertParamsJson.model_validate(payload)
    return value.target, value.data_version


def decode_compare_params(payload: Mapping[str, Any]) -> FingerprintPreset:
    """Decode the fingerprint preset for a comparison request."""
    return _CompareParamsJson.model_validate(payload).preset


def decode_optional_timeout(value: object) -> float | None:
    """Decode an optional positive operation timeout."""
    try:
        return _OPTIONAL_TIMEOUT.validate_python(value)
    except ValidationError as error:
        msg = f"Invalid operation timeout: {error}"
        raise ValueError(msg) from error


def decode_autostack_params(payload: Mapping[str, Any]) -> tuple[AutostackLattice, tuple[int, ...]]:
    """Decode a lattice and repeat counts, enforcing their shared dimensionality."""
    value = _AutostackParamsJson.model_validate(payload)
    return _LATTICE.validate_python(value.lattice.model_dump()), value.counts


def decode_base64(value: object, what: str) -> bytes:
    """Decode required RFC 4648 base64 text without ignoring junk characters."""
    encoded = _string(value, what)
    if not encoded:
        msg = f"Expected {what} to contain base64 data."
        raise ValueError(msg)
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        msg = f"Invalid base64 in {what}."
        raise ValueError(msg) from exc


def encode_resource_pack(resource_pack: VerifiedResourcePack) -> Mapping[str, str]:
    """Encode pack metadata separately from the bytes that its digest verifies."""
    return _ResourcePackJson(sha256=resource_pack.sha256, media_type=resource_pack.media_type).model_dump()


def decode_resource_pack(payload: object, data: bytes) -> VerifiedResourcePack:
    """Rebuild a verified pack from raw bytes and its JSON metadata."""
    metadata = _ResourcePackJson.model_validate(payload)
    return VerifiedResourcePack(data, metadata.sha256, RESOURCE_PACK_MEDIA_TYPE)


def encode_simulation(result: SimulationResult) -> Mapping[str, Any]:
    return _SimulationJson.model_validate(dataclasses.asdict(result)).model_dump(mode="json")


def decode_simulation(payload: Mapping[str, Any]) -> SimulationResult:
    return _SIMULATION.validate_python(_SimulationJson.model_validate(payload).model_dump())


def encode_simulation_input_error(error: AmbiguousSimulationInputError) -> Mapping[str, Any]:
    """Preserve the controls a caller can choose when simulation input is ambiguous."""
    return _SimulationInputErrorJson(candidates=error.candidates, rejected=error.rejected).model_dump(mode="json")


def decode_simulation_input_error(context: Mapping[str, Any]) -> AmbiguousSimulationInputError:
    """Read current wire coordinates or the domain context retained by older durable workers."""
    value = _SimulationInputErrorJson.model_validate(
        {"candidates": context.get("candidates", context.get("input_candidates")), "rejected": context.get("rejected")}
    )
    return AmbiguousSimulationInputError(
        candidates=value.candidates,
        rejected=value.rejected,
    )


def decode_error(payload: object) -> tuple[ErrorKind, str, Mapping[str, Any]]:
    """Decode a worker error object, rejecting unknown error kinds."""
    value = _WorkerErrorJson.model_validate(payload)
    return value.kind, value.message, value.context


def _mapping(value: object, what: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        msg = f"Expected {what} to be a JSON object, got {type(value).__name__}."
        raise TypeError(msg)
    return cast(Mapping[str, Any], value)


def _array(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        msg = f"Expected {what} to be a JSON array, got {type(value).__name__}."
        raise TypeError(msg)
    return cast(list[object], value)


def _string(value: object, what: str) -> str:
    if not isinstance(value, str):
        msg = f"Expected {what} to be a string, got {type(value).__name__}."
        raise TypeError(msg)
    return value


def _boolean(value: object, what: str) -> bool:
    if not isinstance(value, bool):
        msg = f"Expected {what} to be a boolean, got {type(value).__name__}."
        raise TypeError(msg)
    return value


def _integer(
    value: object,
    what: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        msg = f"Expected {what} to be an integer, got {type(value).__name__}."
        raise TypeError(msg)
    if minimum is not None and value < minimum:
        msg = f"Expected {what} to be at least {minimum}."
        raise ValueError(msg)
    if maximum is not None and value > maximum:
        msg = f"Expected {what} to be at most {maximum}."
        raise ValueError(msg)
    return value


def _operation(value: object) -> Operation:
    operation = _string(value, "worker operation")
    if operation not in _OPERATIONS:
        msg = f"Invalid worker operation {operation!r}."
        raise ValueError(msg)
    return cast(Operation, operation)


def _matching_request_id(response_id: int, request_id: int) -> None:
    if response_id != request_id:
        msg = f"Worker response id {response_id} does not match request {request_id}."
        raise ValueError(msg)


def _payload_arity(payloads: Sequence[bytes], expected: int, what: str) -> None:
    if len(payloads) != expected:
        msg = f"Expected {what} to carry {expected} payload(s), got {len(payloads)}."
        raise ValueError(msg)


def decode_video_request(payload: object) -> VideoRenderRequest:
    """Validate video recipes on both sides of the durable/native boundary."""
    return VideoRenderRequest.model_validate(payload)
