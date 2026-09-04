"""The frame format and value encoding shared by the worker supervisor and its children.

Standard library only, and deliberately free of any engine import: the parent process links
against this module, and the whole point of the worker design is that the parent never loads
the native extension.

A frame is::

    | 4 bytes BE header length | 4 bytes BE body length | header JSON (utf-8) | body |

Binary payloads travel in the body, appended raw. Schematic files, converted output, and
rendered PNGs are megabyte-scale; base64-ing them into the JSON header would inflate every
transfer by a third for no benefit. The header's `parts` field carries the length of each
payload so a multi-payload request such as `compare` can be split back apart. Request headers
may also carry a `trace` mapping containing W3C propagation keys such as `traceparent`;
propagation keys never share the frame-header namespace.
"""

import asyncio
import base64
import binascii
import dataclasses
import json
import math
from collections.abc import Mapping, Sequence
from typing import IO, Any, Literal, cast

from squid.schematics.application.commands import RenderRequest, SimulationRequest
from squid.schematics.domain.models import (
    AnalyzerCapabilities,
    AutostackLattice,
    FingerprintPreset,
    SchematicAnalysis,
    SchematicComparison,
    SchematicDimensions,
    SchematicFingerprints,
    SchematicFormat,
    SchematicLimits,
    SchematicMetrics,
    SchematicSign,
    SimulationResult,
    SimulationSample,
    Vector3,
    VersionLossEntry,
)
from squid.schematics.domain.values import RESOURCE_PACK_MEDIA_TYPE, RgbaColor, VerifiedResourcePack

LENGTH_PREFIX_BYTES = 4
MAX_FRAME_BYTES = 256 * 1024 * 1024
"""Sanity bound on a single frame, so a desynchronised stream cannot ask us to allocate a
nonsensical buffer before we notice."""

type Operation = Literal["capabilities", "analyze", "convert", "compare", "render", "simulate", "autostack"]
type ErrorKind = Literal["invalid", "ambiguous_simulation_input", "too_large", "unavailable", "internal"]
"""How the child classifies a failure so the supervisor can rebuild a typed exception.

Deliberately a small closed set rather than a class name: the child's free-form message is
not shown to users, so anything a caller must actually be told has to be carried by a kind
the supervisor knows how to reconstruct."""

_OPERATIONS: frozenset[str] = frozenset(
    {"capabilities", "analyze", "convert", "compare", "render", "simulate", "autostack"}
)
_ERROR_KINDS: frozenset[str] = frozenset(
    {"invalid", "ambiguous_simulation_input", "too_large", "unavailable", "internal"}
)
_REQUEST_PAYLOAD_ARITY: Mapping[Operation, int] = {
    "capabilities": 0,
    "analyze": 1,
    "convert": 1,
    "compare": 2,
    "render": 1,
    "simulate": 1,
    "autostack": 1,
}
_RESPONSE_PAYLOAD_ARITY: Mapping[Operation, int] = {
    "capabilities": 0,
    "analyze": 0,
    "convert": 1,
    "compare": 0,
    "render": 1,
    "simulate": 0,
    "autostack": 1,
}
_DURABLE_REQUEST_PAYLOAD_ARITY: Mapping[Operation, tuple[int, ...]] = {
    **{operation: (arity,) for operation, arity in _REQUEST_PAYLOAD_ARITY.items()},
    "render": (1, 2),
}
MAX_FRAME_PARTS = max(_REQUEST_PAYLOAD_ARITY.values())


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
    """A request header whose discriminants and payload arity are verified."""

    request_id: int
    operation: Operation
    params: Mapping[str, Any]
    job_id: int | None


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


def decode_worker_request(frame: Frame) -> WorkerRequest:
    """Verify a worker request header and its operation-specific payload arity."""
    try:
        request_id = _integer(frame.header.get("id"), "request id", minimum=0)
        operation = _operation(frame.header.get("op"))
        params = _mapping(frame.header.get("params"), "request params")
        job_id_value = frame.header.get("job_id")
        job_id = None if job_id_value is None else _integer(job_id_value, "job id", minimum=0)
        _payload_arity(frame.payloads, _REQUEST_PAYLOAD_ARITY[operation], f"{operation} request")
    except (TypeError, ValueError, KeyError) as exc:
        raise FrameStreamClosed(str(exc)) from exc
    return WorkerRequest(request_id, operation, params, job_id)


def validate_request_payloads(operation: object, payloads: Sequence[bytes]) -> Operation:
    """Validate an operation discriminant and its required binary input count."""
    decoded = _operation(operation)
    _payload_arity(payloads, _REQUEST_PAYLOAD_ARITY[decoded], f"{decoded} request")
    return decoded


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
    """Verify a response discriminant, correlation id, object shape, and payload arity."""
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
    """Flatten an analysis into JSON-safe primitives.

    `dataclasses.asdict` is enough on the way out because every field is a primitive, a
    `StrEnum` (already a `str`), or a nested frozen dataclass. Decoding is written out by hand
    because rebuilding typed nested dataclasses is not something `asdict` can invert.
    """
    return cast(Mapping[str, Any], dataclasses.asdict(analysis))


def decode_analysis(payload: Mapping[str, Any]) -> SchematicAnalysis:
    lattice = payload.get("lattice")
    return SchematicAnalysis(
        metrics=_decode_metrics(_mapping(payload.get("metrics"), "analysis metrics")),
        fingerprints=_decode_fingerprints(_mapping(payload.get("fingerprints"), "analysis fingerprints")),
        analyzer_version=_string(payload.get("analyzer_version"), "analyzer version"),
        analysis_schema_version=_integer(payload.get("analysis_schema_version"), "analysis schema version", minimum=1),
        lattice=decode_lattice(_mapping(lattice, "analysis lattice")) if lattice is not None else None,
    )


def encode_lattice(lattice: AutostackLattice) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], dataclasses.asdict(lattice))


def decode_lattice(payload: object) -> AutostackLattice:
    payload = _mapping(payload, "lattice")
    mode_value = _string(payload.get("mode"), "lattice mode")
    if mode_value not in ("1d", "2d"):
        msg = f"Invalid lattice mode {mode_value!r}."
        raise ValueError(msg)
    mode = cast(Literal["1d", "2d"], mode_value)
    vectors = tuple(_vector(vector, "lattice vector") for vector in _array(payload.get("vectors"), "lattice vectors"))
    expected_vectors = 1 if mode == "1d" else 2
    if len(vectors) != expected_vectors:
        msg = f"A {mode} lattice requires exactly {expected_vectors} vector(s)."
        raise ValueError(msg)
    return AutostackLattice(
        mode=mode,
        vectors=vectors,
        coverage=_number(payload.get("coverage"), "lattice coverage", minimum=0.0, maximum=1.0),
        cell_min=_vector(payload.get("cell_min"), "lattice cell minimum"),
        cell_max=_vector(payload.get("cell_max"), "lattice cell maximum"),
        region_min=_vector(payload.get("region_min"), "lattice region minimum"),
        region_max=_vector(payload.get("region_max"), "lattice region maximum"),
        label=_optional_string(payload.get("label"), "lattice label"),
    )


def encode_comparison(comparison: SchematicComparison) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], dataclasses.asdict(comparison))


def decode_comparison(payload: Mapping[str, Any]) -> SchematicComparison:
    return SchematicComparison(
        preset=FingerprintPreset(_string(payload.get("preset"), "comparison preset")),
        identical=_boolean(payload.get("identical"), "comparison identity"),
        footprint_distance=_number(payload.get("footprint_distance"), "footprint distance", minimum=0.0),
        edit_distance=_optional_integer(payload.get("edit_distance"), "edit distance", minimum=0),
        support=_optional_number(payload.get("support"), "comparison support", minimum=0.0, maximum=1.0),
        summary=_optional_string(payload.get("summary"), "comparison summary"),
    )


def encode_losses(losses: Sequence[VersionLossEntry]) -> list[Mapping[str, Any]]:
    return [cast(Mapping[str, Any], dataclasses.asdict(loss)) for loss in losses]


def decode_losses(payload: object) -> tuple[VersionLossEntry, ...]:
    losses: list[VersionLossEntry] = []
    for value in _array(payload, "conversion losses"):
        entry = _mapping(value, "conversion loss entry")
        severity_value = _string(entry.get("severity"), "conversion loss severity")
        if severity_value not in ("Loss", "Approximated"):
            msg = f"Invalid conversion loss severity {severity_value!r}."
            raise ValueError(msg)
        losses.append(
            VersionLossEntry(
                version=_string(entry.get("version"), "conversion loss version"),
                kind=_string(entry.get("kind"), "conversion loss kind"),
                severity=cast(Literal["Loss", "Approximated"], severity_value),
                path=_string(entry.get("path"), "conversion loss path"),
                detail=_string(entry.get("detail"), "conversion loss detail"),
            )
        )
    return tuple(losses)


def encode_capabilities(capabilities: AnalyzerCapabilities) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], dataclasses.asdict(capabilities))


def decode_capabilities(payload: Mapping[str, Any]) -> AnalyzerCapabilities:
    return AnalyzerCapabilities(
        available=_boolean(payload.get("available"), "analyzer availability"),
        analyzer_version=_optional_string(payload.get("analyzer_version"), "analyzer version"),
        can_render=_boolean(payload.get("can_render"), "render capability"),
        can_simulate=_boolean(payload.get("can_simulate"), "simulation capability"),
        render_backends=tuple(
            _string(backend, "render backend") for backend in _array(payload.get("render_backends"), "render backends")
        ),
        unavailable_reason=_optional_string(payload.get("unavailable_reason"), "unavailable reason"),
    )


def encode_render_request(request: RenderRequest) -> Mapping[str, Any]:
    """Encode a render request without changing the established RGBA array shape."""
    return {
        "width": request.width,
        "height": request.height,
        "projection": request.projection,
        "sphere_fit": request.sphere_fit,
        "yaw": request.yaw,
        "pitch": request.pitch,
        "zoom": request.zoom,
        "background": list(request.background),
    }


def decode_render_request(payload: object) -> RenderRequest:
    """Decode and validate a render request received across a worker boundary."""
    payload = _mapping(payload, "render request")
    background = payload["background"]
    if not isinstance(background, list):
        msg = "Render background must be a four-channel JSON array."
        raise TypeError(msg)
    return RenderRequest(
        width=_integer(payload.get("width"), "render width"),
        height=_integer(payload.get("height"), "render height"),
        projection=_projection(payload.get("projection")),
        sphere_fit=_boolean(payload.get("sphere_fit"), "render sphere-fit flag"),
        yaw=_optional_number(payload.get("yaw"), "render yaw"),
        pitch=_optional_number(payload.get("pitch"), "render pitch"),
        zoom=_optional_number(payload.get("zoom"), "render zoom"),
        background=RgbaColor.from_channels(
            tuple(_number(channel, "RGBA channel", minimum=0.0, maximum=1.0) for channel in background)
        ),
    )


def encode_simulation_request(request: SimulationRequest) -> Mapping[str, Any]:
    """Encode a simulation request into explicit JSON arrays and scalars."""
    return {
        "input_position": list(request.input_position) if request.input_position is not None else None,
        "watch_positions": [list(position) for position in request.watch_positions],
        "max_ticks": request.max_ticks,
    }


def decode_simulation_request(payload: object) -> SimulationRequest:
    """Decode and validate a simulation request received across a worker boundary."""
    payload = _mapping(payload, "simulation request")
    input_position = payload.get("input_position")
    return SimulationRequest(
        input_position=_vector(input_position, "simulation input position") if input_position is not None else None,
        watch_positions=tuple(
            _vector(position, "simulation watch position")
            for position in _array(payload.get("watch_positions"), "simulation watch positions")
        ),
        max_ticks=_integer(payload.get("max_ticks"), "simulation tick budget", minimum=1),
    )


def decode_limits(payload: object) -> SchematicLimits:
    """Decode attacker-facing worker resource limits without coercing JSON values."""
    value = _mapping(payload, "schematic limits")
    return SchematicLimits(
        max_upload_bytes=_integer(value.get("max_upload_bytes"), "maximum upload bytes", minimum=1),
        max_inflated_bytes=_integer(value.get("max_inflated_bytes"), "maximum inflated bytes", minimum=1),
        max_allocated_volume=_integer(value.get("max_allocated_volume"), "maximum allocated volume", minimum=1),
        max_axis_length=_integer(value.get("max_axis_length"), "maximum axis length", minimum=1),
        max_sniff_bytes=_integer(value.get("max_sniff_bytes"), "maximum sniff bytes", minimum=1),
    )


def decode_analyze_params(
    payload: Mapping[str, Any],
) -> tuple[SchematicLimits, bool, SchematicFormat | None, int]:
    """Decode the parameters for an analyze request."""
    source_value = payload.get("source_format")
    source_format = None if source_value is None else SchematicFormat(_string(source_value, "schematic source format"))
    return (
        decode_limits(payload.get("limits")),
        _boolean(payload.get("with_lattice"), "lattice analysis flag"),
        source_format,
        _integer(payload.get("lattice_max_block_count", 200_000), "lattice block-count limit", minimum=1),
    )


def decode_convert_params(payload: Mapping[str, Any]) -> tuple[SchematicFormat, int | None]:
    """Decode the parameters for a conversion request."""
    data_version = payload.get("data_version")
    return (
        SchematicFormat(_string(payload.get("target"), "conversion target")),
        None if data_version is None else _integer(data_version, "conversion data version", minimum=0),
    )


def decode_compare_params(payload: Mapping[str, Any]) -> FingerprintPreset:
    """Decode the fingerprint preset for a comparison request."""
    return FingerprintPreset(_string(payload.get("preset"), "comparison preset"))


def decode_optional_timeout(value: object) -> float | None:
    """Decode an optional positive operation timeout."""
    timeout = _optional_number(value, "operation timeout", minimum=0.0)
    if timeout is not None and timeout == 0:
        msg = "Expected operation timeout to be greater than zero."
        raise ValueError(msg)
    return timeout


def decode_autostack_params(payload: Mapping[str, Any]) -> tuple[AutostackLattice, tuple[int, ...]]:
    """Decode a lattice and repeat counts, enforcing their shared dimensionality."""
    lattice = decode_lattice(_mapping(payload.get("lattice"), "autostack lattice"))
    counts = tuple(
        _integer(count, "autostack repeat count", minimum=1)
        for count in _array(payload.get("counts"), "autostack repeat counts")
    )
    expected = 1 if lattice.mode == "1d" else 2
    if len(counts) != expected:
        msg = f"A {lattice.mode} lattice requires exactly {expected} repeat count(s)."
        raise ValueError(msg)
    return lattice, counts


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
    """Encode verified metadata while the pack bytes travel as a raw payload."""
    return {"sha256": resource_pack.sha256, "media_type": resource_pack.media_type}


def decode_resource_pack(payload: object, data: bytes) -> VerifiedResourcePack:
    """Rebuild a verified pack from raw bytes and its JSON metadata."""
    payload = _mapping(payload, "resource-pack metadata")
    media_type = _string(payload.get("media_type"), "resource-pack media type")
    if media_type != RESOURCE_PACK_MEDIA_TYPE:
        msg = f"Unsupported resource-pack media type {media_type!r}."
        raise ValueError(msg)
    return VerifiedResourcePack(data, _string(payload.get("sha256"), "resource-pack SHA-256"), RESOURCE_PACK_MEDIA_TYPE)


def encode_simulation(result: SimulationResult) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], dataclasses.asdict(result))


def decode_simulation(payload: Mapping[str, Any]) -> SimulationResult:
    input_position = payload.get("input_position")
    input_source = payload.get("input_source")
    if input_source is not None and input_source not in ("insign", "heuristic", "manual"):
        msg = f"Invalid simulation input source {input_source!r}."
        raise ValueError(msg)
    return SimulationResult(
        ticks_run=_integer(payload.get("ticks_run"), "simulation ticks run", minimum=0),
        settled_tick=_optional_integer(payload.get("settled_tick"), "simulation settled tick", minimum=0),
        input_position=_vector(input_position, "simulation input position") if input_position is not None else None,
        input_source=cast(Literal["insign", "heuristic", "manual"], input_source) if input_source else None,
        last_piston_tick=_optional_integer(payload.get("last_piston_tick"), "last piston tick", minimum=0),
        block_changes=_integer(payload.get("block_changes"), "simulation block changes", minimum=0),
        piston_events=_integer(payload.get("piston_events"), "simulation piston events", minimum=0),
        redstone_events=_integer(payload.get("redstone_events"), "simulation redstone events", minimum=0),
        trustworthy=_boolean(payload.get("trustworthy"), "simulation trustworthiness"),
        samples=tuple(
            SimulationSample(
                tick=_integer(sample.get("tick"), "simulation sample tick", minimum=0),
                x=_integer(sample.get("x"), "simulation sample x"),
                y=_integer(sample.get("y"), "simulation sample y"),
                z=_integer(sample.get("z"), "simulation sample z"),
                powered=_boolean(sample.get("powered"), "simulation sample powered flag"),
                signal_strength=_integer(
                    sample.get("signal_strength"), "simulation sample signal", minimum=0, maximum=15
                ),
            )
            for sample in (
                _mapping(value, "simulation sample") for value in _array(payload.get("samples"), "simulation samples")
            )
        ),
        notes=tuple(_string(note, "simulation note") for note in _array(payload.get("notes"), "simulation notes")),
    )


def _decode_metrics(payload: Mapping[str, Any]) -> SchematicMetrics:
    return SchematicMetrics(
        source_format=SchematicFormat(_string(payload.get("source_format"), "source format")),
        byte_size=_integer(payload.get("byte_size"), "schematic byte size", minimum=0),
        sha256=_string(payload.get("sha256"), "schematic SHA-256"),
        dimensions=_decode_dimensions(_mapping(payload.get("dimensions"), "schematic dimensions")),
        allocated_dimensions=_decode_dimensions(
            _mapping(payload.get("allocated_dimensions"), "allocated schematic dimensions")
        ),
        block_count=_integer(payload.get("block_count"), "schematic block count", minimum=0),
        bounding_volume=_integer(payload.get("bounding_volume"), "schematic bounding volume", minimum=0),
        entity_count=_integer(payload.get("entity_count"), "schematic entity count", minimum=0),
        palette_size=_integer(payload.get("palette_size"), "schematic palette size", minimum=0),
        region_names=tuple(
            _string(name, "schematic region name")
            for name in _array(payload.get("region_names"), "schematic region names")
        ),
        source_data_version=_optional_integer(payload.get("source_data_version"), "source data version"),
        declared_name=_optional_string(payload.get("declared_name"), "declared schematic name"),
        declared_author=_optional_string(payload.get("declared_author"), "declared schematic author"),
        signs=tuple(
            SchematicSign(
                x=_integer(sign.get("x"), "sign x"),
                y=_integer(sign.get("y"), "sign y"),
                z=_integer(sign.get("z"), "sign z"),
                text=_string(sign.get("text"), "sign text"),
            )
            for sign in (_mapping(value, "schematic sign") for value in _array(payload.get("signs"), "schematic signs"))
        ),
    )


def _decode_fingerprints(payload: Mapping[str, Any]) -> SchematicFingerprints:
    return SchematicFingerprints(
        structural=_string(payload.get("structural"), "structural fingerprint"),
        shape=_string(payload.get("shape"), "shape fingerprint"),
        exact=_string(payload.get("exact"), "exact fingerprint"),
        signature_structural=_optional_string(payload.get("signature_structural"), "structural signature"),
    )


def _decode_dimensions(payload: Mapping[str, Any]) -> SchematicDimensions:
    return SchematicDimensions(
        width=_integer(payload.get("width"), "schematic width", minimum=0),
        height=_integer(payload.get("height"), "schematic height", minimum=0),
        length=_integer(payload.get("length"), "schematic length", minimum=0),
    )


def decode_error(payload: object) -> tuple[ErrorKind, str, Mapping[str, Any]]:
    """Decode a typed worker error object without accepting unknown discriminants."""
    error = _mapping(payload, "worker error")
    kind_value = _string(error.get("kind"), "worker error kind")
    if kind_value not in _ERROR_KINDS:
        msg = f"Invalid worker error kind {kind_value!r}."
        raise ValueError(msg)
    return (
        cast(ErrorKind, kind_value),
        _string(error.get("message"), "worker error message"),
        _mapping(error.get("context"), "worker error context"),
    )


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


def _optional_string(value: object, what: str) -> str | None:
    return None if value is None else _string(value, what)


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


def _optional_integer(
    value: object,
    what: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    return None if value is None else _integer(value, what, minimum=minimum, maximum=maximum)


def _number(
    value: object,
    what: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        msg = f"Expected {what} to be a number, got {type(value).__name__}."
        raise TypeError(msg)
    number = float(value)
    if not math.isfinite(number):
        msg = f"Expected {what} to be finite."
        raise ValueError(msg)
    if minimum is not None and number < minimum:
        msg = f"Expected {what} to be at least {minimum}."
        raise ValueError(msg)
    if maximum is not None and number > maximum:
        msg = f"Expected {what} to be at most {maximum}."
        raise ValueError(msg)
    return number


def _optional_number(
    value: object,
    what: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    return None if value is None else _number(value, what, minimum=minimum, maximum=maximum)


def _vector(value: object, what: str) -> Vector3:
    parts = _array(value, what)
    if len(parts) != 3:
        msg = f"Expected {what} to contain exactly three coordinates."
        raise ValueError(msg)
    return (
        _integer(parts[0], f"{what} x"),
        _integer(parts[1], f"{what} y"),
        _integer(parts[2], f"{what} z"),
    )


def _projection(value: object) -> Literal["orthographic", "perspective"]:
    projection = _string(value, "render projection")
    if projection not in ("orthographic", "perspective"):
        msg = f"Invalid render projection {projection!r}."
        raise ValueError(msg)
    return cast(Literal["orthographic", "perspective"], projection)


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
