# pyright: reportPrivateUsage=false
"""Worker deadline-selection tests that do not start native subprocesses."""

import asyncio

import pytest

from squid.config import SchematicConfig
from squid.schematics.application.commands import RenderRequest, SimulationRequest
from squid.schematics.domain.models import AutostackLattice, FingerprintPreset, SchematicFormat, SchematicLimits
from squid.schematics.errors import SchematicTimeoutError
from squid.schematics.infrastructure.wire import Frame, Operation
from squid.schematics.infrastructure.worker import SchematicWorkerPool


class CallObserved(Exception):
    """Stop a public operation after its selected deadline has been observed."""


async def test_queue_wait_can_consume_the_whole_operation_deadline() -> None:
    pool = SchematicWorkerPool(SchematicConfig(workers=1, restart_backoff_seconds=0.01))
    pool._available = asyncio.Semaphore(0)

    with pytest.raises(SchematicTimeoutError) as raised:
        await pool._call_unmeasured("analyze", {}, (b"data",), 0.001)

    assert raised.value.operation == "analyze"
    assert pool._workers[0]._process is None


async def test_every_public_operation_selects_its_configured_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    config = SchematicConfig(
        workers=1,
        parse_timeout_seconds=1.0,
        compare_timeout_seconds=2.0,
        convert_timeout_seconds=3.0,
        render_timeout_seconds=4.0,
        simulate_timeout_seconds=5.0,
    )
    pool = SchematicWorkerPool(config)
    observed: list[tuple[Operation, float]] = []

    async def observe(
        operation: Operation,
        _params: object,
        _payloads: object,
        timeout: float,
    ) -> Frame:
        observed.append((operation, timeout))
        raise CallObserved

    monkeypatch.setattr(pool, "_call", observe)
    lattice = AutostackLattice(
        mode="1d",
        vectors=((1, 0, 0),),
        coverage=1.0,
        cell_min=(0, 0, 0),
        cell_max=(0, 0, 0),
        region_min=(0, 0, 0),
        region_max=(1, 0, 0),
    )

    with pytest.raises(CallObserved):
        await pool.capabilities()
    with pytest.raises(CallObserved):
        await pool.analyze(b"data", limits=SchematicLimits())
    with pytest.raises(CallObserved):
        await pool.convert(b"data", target=SchematicFormat.LITEMATIC)
    with pytest.raises(CallObserved):
        await pool.compare(b"left", b"right", preset=FingerprintPreset.SHAPE)
    with pytest.raises(CallObserved):
        await pool.render(b"data", request=RenderRequest())
    with pytest.raises(CallObserved):
        await pool.simulate(b"data", request=SimulationRequest())
    with pytest.raises(CallObserved):
        await pool.autostack(b"data", lattice=lattice, counts=(2,))

    assert observed == [
        ("capabilities", 1.0),
        ("analyze", 1.0),
        ("convert", 3.0),
        ("compare", 2.0),
        ("render", 4.0),
        ("simulate", 5.0),
        ("autostack", 3.0),
    ]


async def test_compare_uses_the_stricter_caller_owned_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = SchematicWorkerPool(SchematicConfig(workers=1, compare_timeout_seconds=10.0))
    observed: list[float] = []

    async def observe(
        _operation: Operation,
        _params: object,
        _payloads: object,
        timeout: float,
    ) -> Frame:
        observed.append(timeout)
        raise CallObserved

    monkeypatch.setattr(pool, "_call", observe)

    with pytest.raises(CallObserved):
        await pool.compare(b"left", b"right", preset=FingerprintPreset.SHAPE, timeout_seconds=0.5)

    assert observed == [0.5]
