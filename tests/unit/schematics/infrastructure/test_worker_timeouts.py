# pyright: reportPrivateUsage=false
"""Worker deadline-selection tests that do not start native subprocesses."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from squid.config import SchematicConfig
from squid.schematics.application.commands import RenderRequest, SimulationRequest
from squid.schematics.domain.models import AutostackLattice, FingerprintPreset, SchematicFormat, SchematicLimits
from squid.schematics.errors import SchematicTimeoutError
from squid.schematics.infrastructure.wire import Frame, Operation
from squid.schematics.infrastructure.worker import SchematicWorkerPool, _Worker


class CallObserved(Exception):
    """Stop a public operation after its selected deadline has been observed."""


def _worker() -> _Worker:
    return _Worker(SchematicConfig(), lambda _pump: None)


async def test_worker_deadline_includes_cold_process_start(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = _worker()

    async def slow_start() -> None:
        await asyncio.sleep(1)

    monkeypatch.setattr(worker, "_ensure_started", slow_start)
    monkeypatch.setattr(worker, "_terminate", AsyncMock(return_value=None))

    with pytest.raises(SchematicTimeoutError):
        await worker.request("capabilities", {}, (), 0.001)


async def test_worker_write_and_read_share_one_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = _worker()

    class SlowInput:
        def write(self, _data: bytes) -> None:
            return

        async def drain(self) -> None:
            await asyncio.sleep(0.02)

    process = type("Process", (), {"stdin": SlowInput(), "stdout": object()})()

    async def slow_response(_stream: object) -> Frame:
        await asyncio.sleep(0.02)
        return Frame({"id": 1, "ok": True, "result": {}})

    monkeypatch.setattr(worker, "_ensure_started", AsyncMock(return_value=process))
    monkeypatch.setattr(worker, "_terminate", AsyncMock(return_value=None))
    monkeypatch.setattr("squid.schematics.infrastructure.worker.wire.read_frame", slow_response)

    with pytest.raises(SchematicTimeoutError):
        await worker.request("capabilities", {}, (), 0.03)


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
