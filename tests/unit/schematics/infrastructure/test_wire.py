# pyright: reportPrivateUsage=false
"""Worker frame and value-encoding tests.

These run without the native engine: the point of the wire format is that the parent process
never has to load it.
"""

import asyncio
import json

import pytest

from squid.schematics.application.commands import RenderRequest
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

    assert wire.decode_analysis(wire.encode_analysis(analysis)) == analysis


def test_a_comparison_survives_encoding_and_decoding() -> None:
    comparison = SchematicComparison(
        preset=FingerprintPreset.SHAPE,
        identical=False,
        footprint_distance=0.726,
        edit_distance=1,
        support=0.94,
        summary='{"distance":1}',
    )

    assert wire.decode_comparison(wire.encode_comparison(comparison)) == comparison


def test_capabilities_survive_encoding_and_decoding() -> None:
    capabilities = AnalyzerCapabilities(
        available=True,
        analyzer_version="nucleation-0.9.2",
        can_render=True,
        can_simulate=True,
        render_backends=("vulkan",),
    )

    assert wire.decode_capabilities(wire.encode_capabilities(capabilities)) == capabilities


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

    assert wire.decode_simulation(wire.encode_simulation(result)) == result


def test_loss_entries_survive_encoding_and_decoding() -> None:
    losses = (
        VersionLossEntry(version="1.16.5", kind="block", severity="Loss", path="Regions/Main", detail="dropped"),
        VersionLossEntry(version="1.16.5", kind="block", severity="Approximated", path="x", detail="mapped"),
    )

    assert wire.decode_losses(wire.encode_losses(losses)) == losses


def test_a_missing_loss_report_decodes_to_no_losses() -> None:
    assert wire.decode_losses(None) == ()


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
def test_a_malformed_candidate_list_degrades_to_no_candidates(context: dict[str, object]) -> None:
    """The child is our own code, but it is still JSON arriving down a pipe."""
    rebuilt = _translate({"kind": "ambiguous_simulation_input", "context": context}, "simulate")

    assert isinstance(rebuilt, AmbiguousSimulationInputError)
    assert rebuilt.candidates == ()
    assert rebuilt.rejected is None
