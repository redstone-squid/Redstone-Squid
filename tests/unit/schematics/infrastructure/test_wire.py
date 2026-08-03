"""Worker frame and value-encoding tests.

These run without the native engine: the point of the wire format is that the parent process
never has to load it.
"""

import asyncio

import pytest

from squid.schematics.domain.models import (
    AnalyzerCapabilities,
    AutostackLattice,
    FingerprintPreset,
    SchematicComparison,
    SchematicSign,
    SimulationResult,
    SimulationSample,
    VersionLossEntry,
)
from squid.schematics.infrastructure import wire
from squid.schematics.infrastructure.wire import Frame, FrameStreamClosed
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


def test_a_simulation_result_survives_encoding_and_decoding() -> None:
    result = SimulationResult(
        ticks_run=40,
        settled_tick=32,
        samples=(SimulationSample(tick=4, x=1, y=2, z=3, powered=True, signal_strength=15),),
        notes=("propagation delay only",),
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
