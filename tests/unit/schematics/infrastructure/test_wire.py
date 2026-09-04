# pyright: reportPrivateUsage=false
"""Worker frame and value-encoding tests.

These run without the native engine: the point of the wire format is that the parent process
never has to load it.
"""

import asyncio
import io
import json

import pytest

from squid.schematics.application.commands import RenderRequest, SimulationRequest
from squid.schematics.domain.models import (
    AnalyzerCapabilities,
    AutostackLattice,
    FingerprintPreset,
    SchematicComparison,
    SchematicSign,
    SimulationResult,
    SimulationSample,
    Vector3,
    VersionLossEntry,
)
from squid.schematics.domain.values import RgbaColor, VerifiedResourcePack
from squid.schematics.errors import AmbiguousSimulationInputError
from squid.schematics.infrastructure import wire
from squid.schematics.infrastructure.wire import Frame, FrameStreamClosed
from squid.schematics.infrastructure.worker import _translate
from squid.schematics.infrastructure.worker_main import _error_payload
from tests.unit.schematics.fakes import make_analysis


async def read_back(frame: Frame) -> Frame:
    """Round-trip a frame through a real asyncio stream, as the supervisor would."""
    reader = asyncio.StreamReader()
    reader.feed_data(frame.encode())
    reader.feed_eof()
    return await wire.read_frame(reader)


async def read_raw_frame(data: bytes) -> Frame:
    """Read exact bytes so malformed headers can bypass `Frame.encode` validation."""
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return await wire.read_frame(reader)


def raw_frame(header: object, body: bytes = b"", *, body_length: int | None = None) -> bytes:
    header_bytes = json.dumps(header, separators=(",", ":")).encode()
    declared_body_length = len(body) if body_length is None else body_length
    return len(header_bytes).to_bytes(4, "big") + declared_body_length.to_bytes(4, "big") + header_bytes + body


async def test_a_frame_round_trips_its_header_and_every_payload() -> None:
    frame = Frame({"id": 3, "op": "compare"}, (b"left-bytes", b"right"))

    decoded = await read_back(frame)

    assert decoded.header["id"] == 3
    assert decoded.header["op"] == "compare"
    assert decoded.payloads == (b"left-bytes", b"right")


async def test_payloads_travel_raw_rather_than_base64_encoded() -> None:
    """Schematics are megabyte-scale; base64 in the header would inflate every transfer."""
    payload = bytes(range(256)) * 16
    frame = Frame({"op": "analyze"}, (payload,))

    assert payload in frame.encode()
    assert (await read_back(frame)).payloads == (payload,)


async def test_a_frame_with_no_payload_decodes_to_no_payloads() -> None:
    assert (await read_back(Frame({"op": "capabilities"}))).payloads == ()


async def test_a_truncated_stream_reports_the_peer_closing_rather_than_hanging() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(Frame({"op": "analyze"}, (b"body",)).encode()[:6])
    reader.feed_eof()

    with pytest.raises(FrameStreamClosed):
        await wire.read_frame(reader)


async def test_a_desynchronised_stream_is_refused_before_allocating_a_huge_buffer() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data((wire.MAX_FRAME_BYTES + 1).to_bytes(4, "big") + b"\x00\x00\x00\x00")
    reader.feed_eof()

    with pytest.raises(FrameStreamClosed):
        await wire.read_frame(reader)


@pytest.mark.parametrize(
    "header",
    [
        [],
        {"parts": None},
        {"parts": [-1]},
        {"parts": [True]},
        {"parts": [wire.MAX_FRAME_BYTES + 1]},
        {"parts": [0] * (wire.MAX_FRAME_PARTS + 1)},
    ],
)
async def test_malformed_frame_headers_are_rejected_before_body_slicing(header: object) -> None:
    with pytest.raises(FrameStreamClosed):
        await read_raw_frame(raw_frame(header))


async def test_payload_part_totals_must_equal_the_declared_body() -> None:
    with pytest.raises(FrameStreamClosed, match="do not equal"):
        await read_raw_frame(raw_frame({"parts": [1]}, body_length=2))


def test_blocking_frame_reader_applies_the_same_header_validation() -> None:
    with pytest.raises(FrameStreamClosed, match="do not equal"):
        wire.read_frame_sync(io.BytesIO(raw_frame({"parts": [1]}, body_length=2)))


@pytest.mark.parametrize(
    "frame",
    [
        Frame({"id": 1, "op": "unknown", "params": {}}),
        Frame({"id": 1, "op": "compare", "params": {}}, (b"only-one",)),
        Frame({"id": True, "op": "capabilities", "params": {}}),
        Frame({"id": 1, "op": "capabilities", "params": []}),
    ],
)
def test_worker_requests_require_known_operations_objects_and_payload_arity(frame: Frame) -> None:
    with pytest.raises(FrameStreamClosed):
        wire.decode_worker_request(frame)


@pytest.mark.parametrize(
    ("operation", "arity"),
    [
        ("capabilities", 0),
        ("analyze", 1),
        ("convert", 1),
        ("compare", 2),
        ("render", 1),
        ("simulate", 1),
        ("autostack", 1),
    ],
)
def test_every_worker_request_operation_enforces_its_payload_arity(operation: wire.Operation, arity: int) -> None:
    payloads = (b"payload",) * arity
    decoded = wire.decode_worker_request(Frame({"id": 1, "op": operation, "params": {}}, payloads))
    assert decoded.operation == operation

    with pytest.raises(FrameStreamClosed, match="payload"):
        wire.decode_worker_request(Frame({"id": 1, "op": operation, "params": {}}, (*payloads, b"extra")))
    if payloads:
        with pytest.raises(FrameStreamClosed, match="payload"):
            wire.decode_worker_request(Frame({"id": 1, "op": operation, "params": {}}, payloads[:-1]))


@pytest.mark.parametrize(
    "frame",
    [
        Frame({"id": 2, "ok": True, "result": {}}),
        Frame({"id": 1, "ok": "yes", "result": {}}),
        Frame({"id": 1, "ok": True, "result": []}),
        Frame({"id": 1, "ok": True, "result": {}}, (b"unexpected",)),
        Frame({"id": 1, "ok": False, "error": {"kind": "unknown", "message": "x", "context": {}}}),
    ],
)
def test_worker_responses_require_matching_ids_objects_enums_and_payload_arity(frame: Frame) -> None:
    with pytest.raises(FrameStreamClosed):
        wire.validate_worker_response(frame, request_id=1, operation="capabilities")


@pytest.mark.parametrize(
    ("operation", "arity"),
    [
        ("capabilities", 0),
        ("analyze", 0),
        ("convert", 1),
        ("compare", 0),
        ("render", 1),
        ("simulate", 0),
        ("autostack", 1),
    ],
)
def test_every_worker_response_operation_enforces_its_payload_arity(operation: wire.Operation, arity: int) -> None:
    payloads = (b"payload",) * arity
    wire.validate_worker_response(
        Frame({"id": 1, "ok": True, "result": {}}, payloads),
        request_id=1,
        operation=operation,
    )

    with pytest.raises(FrameStreamClosed, match="payload"):
        wire.validate_worker_response(
            Frame({"id": 1, "ok": True, "result": {}}, (*payloads, b"extra")),
            request_id=1,
            operation=operation,
        )
    if payloads:
        with pytest.raises(FrameStreamClosed, match="payload"):
            wire.validate_worker_response(
                Frame({"id": 1, "ok": True, "result": {}}, payloads[:-1]),
                request_id=1,
                operation=operation,
            )


@pytest.mark.parametrize(
    ("operation", "arities"),
    [
        ("capabilities", (0,)),
        ("analyze", (1,)),
        ("convert", (1,)),
        ("compare", (2,)),
        ("render", (1, 2)),
        ("simulate", (1,)),
        ("autostack", (1,)),
    ],
)
def test_every_durable_operation_enforces_its_payload_arity(
    operation: wire.Operation, arities: tuple[int, ...]
) -> None:
    for arity in arities:
        assert wire.validate_durable_request_payloads(operation, (b"payload",) * arity) == operation

    with pytest.raises(ValueError, match="payload"):
        wire.validate_durable_request_payloads(operation, (b"payload",) * (max(arities) + 1))
    if min(arities) > 0:
        with pytest.raises(ValueError, match="payload"):
            wire.validate_durable_request_payloads(operation, ())


def test_an_analysis_survives_encoding_and_decoding() -> None:
    lattice = AutostackLattice(
        mode="2d",
        vectors=((4, 0, 0), (0, 2, 0)),
        coverage=0.66,
        cell_min=(8, 0, 0),
        cell_max=(11, 1, 0),
        region_min=(0, 0, 0),
        region_max=(20, 2, 0),
        label="2D array",
    )
    analysis = make_analysis(lattice=lattice, signs=(SchematicSign(1, 2, 3, "line one\nline two"),))

    assert wire.decode_analysis(json.loads(json.dumps(wire.encode_analysis(analysis)))) == analysis


def test_a_comparison_survives_encoding_and_decoding() -> None:
    comparison = SchematicComparison(
        preset=FingerprintPreset.SHAPE,
        identical=False,
        footprint_distance=0.726,
        edit_distance=1,
        support=0.94,
        summary='{"distance":1}',
    )

    assert wire.decode_comparison(json.loads(json.dumps(wire.encode_comparison(comparison)))) == comparison


def test_capabilities_survive_encoding_and_decoding() -> None:
    capabilities = AnalyzerCapabilities(
        available=True,
        analyzer_version="nucleation-0.9.2",
        can_render=True,
        can_simulate=True,
        render_backends=("vulkan",),
    )

    assert wire.decode_capabilities(json.loads(json.dumps(wire.encode_capabilities(capabilities)))) == capabilities


def test_render_requests_keep_the_rgba_json_array_shape() -> None:
    request = RenderRequest(
        width=640,
        height=480,
        projection="perspective",
        sphere_fit=False,
        yaw=45.0,
        pitch=20.0,
        zoom=1.5,
        background=RgbaColor(0.1, 0.2, 0.3, 1.0),
    )

    encoded = wire.encode_render_request(request)
    round_tripped = json.loads(json.dumps(encoded))

    assert round_tripped["background"] == [0.1, 0.2, 0.3, 1.0]
    assert wire.decode_render_request(round_tripped) == request


def test_render_request_decode_rejects_non_array_rgba_values() -> None:
    encoded = dict(wire.encode_render_request(RenderRequest()))
    encoded["background"] = {"red": 0.0, "green": 0.0, "blue": 0.0, "alpha": 0.0}

    with pytest.raises(TypeError, match="JSON array"):
        wire.decode_render_request(encoded)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("projection", "fisheye"),
        ("sphere_fit", 1),
        ("background", [0.0, 0.0, "0.0", 1.0]),
        ("background", [0.0, 0.0, float("nan"), 1.0]),
    ],
)
def test_render_request_decode_rejects_invalid_enums_booleans_and_rgba(field: str, value: object) -> None:
    encoded = dict(wire.encode_render_request(RenderRequest()))
    encoded[field] = value

    with pytest.raises((TypeError, ValueError)):
        wire.decode_render_request(encoded)


@pytest.mark.parametrize("position", [[1, 2], [1, 2, "3"], [1, 2, 3, 4]])
def test_simulation_request_decode_rejects_invalid_vectors(position: list[object]) -> None:
    encoded = dict(wire.encode_simulation_request(SimulationRequest()))
    encoded["input_position"] = position

    with pytest.raises((TypeError, ValueError)):
        wire.decode_simulation_request(encoded)


def test_lattice_decode_rejects_invalid_modes_and_vector_counts() -> None:
    lattice = AutostackLattice(
        mode="1d",
        vectors=((4, 0, 0),),
        coverage=1.0,
        cell_min=(0, 0, 0),
        cell_max=(3, 0, 0),
        region_min=(0, 0, 0),
        region_max=(7, 0, 0),
    )
    encoded = json.loads(json.dumps(wire.encode_lattice(lattice)))
    encoded["mode"] = "3d"
    with pytest.raises(ValueError, match="mode"):
        wire.decode_lattice(encoded)

    encoded["mode"] = "1d"
    encoded["vectors"] = [[4, 0, 0], [0, 4, 0]]
    with pytest.raises(ValueError, match="exactly 1"):
        wire.decode_lattice(encoded)


@pytest.mark.parametrize("payload", ["", "%%%", "YWJj==="])
def test_worker_base64_decode_rejects_missing_or_malformed_text(payload: str) -> None:
    with pytest.raises(ValueError, match=r"contain base64|Invalid base64"):
        wire.decode_base64(payload, "render pack")


@pytest.mark.parametrize("timeout", [0, 0.0, -1, float("nan"), float("inf")])
def test_optional_operation_timeout_must_be_finite_and_positive(timeout: float) -> None:
    with pytest.raises(ValueError, match=r"operation timeout|finite"):
        wire.decode_optional_timeout(timeout)


def test_resource_pack_metadata_and_raw_bytes_rebuild_a_verified_value() -> None:
    pack = VerifiedResourcePack.from_bytes(b"resource-pack")
    metadata = json.loads(json.dumps(wire.encode_resource_pack(pack)))

    assert wire.decode_resource_pack(metadata, pack.data) == pack
    with pytest.raises(ValueError, match="do not match"):
        wire.decode_resource_pack(metadata, b"tampered")


def test_a_simulation_result_survives_encoding_and_decoding() -> None:
    result = SimulationResult(
        ticks_run=40,
        settled_tick=32,
        input_position=(1, 2, 3),
        input_source="insign",
        last_piston_tick=9,
        block_changes=51,
        piston_events=12,
        redstone_events=24,
        trustworthy=True,
        samples=(SimulationSample(tick=4, x=1, y=2, z=3, powered=True, signal_strength=15),),
        notes=("capture-backed",),
    )

    assert wire.decode_simulation(json.loads(json.dumps(wire.encode_simulation(result)))) == result


def test_loss_entries_survive_encoding_and_decoding() -> None:
    losses = (
        VersionLossEntry(version="1.16.5", kind="block", severity="Loss", path="Regions/Main", detail="dropped"),
        VersionLossEntry(version="1.16.5", kind="block", severity="Approximated", path="x", detail="mapped"),
    )

    assert wire.decode_losses(wire.encode_losses(losses)) == losses


def test_a_missing_loss_report_is_rejected() -> None:
    with pytest.raises(TypeError, match="conversion losses"):
        wire.decode_losses(None)


@pytest.mark.parametrize(
    ("candidates", "rejected"),
    [
        ([(12, 5, -3), (0, 1, 2)], None),
        ([(0, 1, 2)], (9, 9, 9)),
        ([], None),
    ],
)
def test_candidate_input_coordinates_survive_the_worker_pipe(
    candidates: list[Vector3], rejected: Vector3 | None
) -> None:
    """The refusal is useless without them, and the child cannot ship an exception object."""
    original = AmbiguousSimulationInputError(candidates=candidates, rejected=rejected)

    # Through real JSON, because that is what the pipe carries: tuples arrive as lists.
    rebuilt = _translate(json.loads(json.dumps(_error_payload(original))), "simulate")

    assert isinstance(rebuilt, AmbiguousSimulationInputError)
    assert rebuilt.candidates == tuple(sorted(candidates))
    assert rebuilt.rejected == rejected
    assert rebuilt.public_detail() == original.public_detail()
    assert rebuilt.context["operation"] == "simulate"


@pytest.mark.parametrize("context", [{}, {"candidates": "not a list", "rejected": [1, 2]}, {"candidates": [[1, 2]]}])
def test_a_malformed_candidate_list_is_rejected(context: dict[str, object]) -> None:
    """The child is our own code, but it is still JSON arriving down a pipe."""
    with pytest.raises((TypeError, ValueError)):
        _translate({"kind": "ambiguous_simulation_input", "message": "ambiguous", "context": context}, "simulate")
