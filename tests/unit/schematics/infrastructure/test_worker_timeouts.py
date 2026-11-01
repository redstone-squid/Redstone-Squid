# pyright: reportPrivateUsage=false
"""Worker deadlines exercised with Python children and explicit scheduling failures."""

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
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


@asynccontextmanager
async def running_child(script: str) -> AsyncIterator[tuple[SchematicWorkerPool, asyncio.subprocess.Process]]:
    """Own a Python child's worker and stderr pump until the context exits."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sys; sys.stdout.buffer.write(b'ready\\n'); sys.stdout.buffer.flush()\n" + script,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    pool = SchematicWorkerPool(SchematicConfig(workers=1, restart_backoff_seconds=10.0))
    worker = pool._workers[0]
    with anyio.fail_after(5):
        async with pool.running():
            try:
                assert process.stdout is not None
                assert await process.stdout.readline() == b"ready\n"
                worker._process = process
                worker._start_pump(process)
                yield pool, process
            finally:
                # A failed assertion must not leave a sleeping child behind or spend the
                # graceful shutdown budget waiting for a deliberately unresponsive peer.
                with anyio.move_on_after(5, shield=True):
                    if process.returncode is None:
                        process.kill()
                    await process.wait()


async def test_cold_start_and_hung_cleanup_have_separate_bounded_deadlines(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = _worker()
    cleanup_started = anyio.Event()

    async def hung_cleanup() -> None:
        cleanup_started.set()
        await anyio.sleep_forever()

    monkeypatch.setattr(worker, "_ensure_started", anyio.sleep_forever)
    monkeypatch.setattr(worker, "_terminate", hung_cleanup)

    with pytest.raises(SchematicTimeoutError), anyio.fail_after(0.5):
        await worker.request("capabilities", {}, (), 0.01)

    assert cleanup_started.is_set()


async def test_timeout_reaps_the_child_without_waiting_for_restart_backoff() -> None:
    async with running_child("import time; time.sleep(60)") as (pool, process):
        with pytest.raises(SchematicTimeoutError), anyio.fail_after(1):
            await pool._call_unmeasured("analyze", {}, (b"data",), 0.02)

        assert process.returncode is not None
        worker = pool._workers[0]
        assert worker._process is None
        assert worker._stderr_pump is None
        assert worker._restart_not_before > asyncio.get_running_loop().time()
        assert tuple(pool._idle) == (worker,)
        assert pool._waiting == 0
        assert not pool._available.locked()


async def test_worker_write_and_read_share_one_deadline(caplog: pytest.LogCaptureFixture) -> None:
    response = Frame({"id": 1, "ok": True, "result": {}}).encode()
    script = f"""
import sys
import time

prefix = sys.stdin.buffer.read(8)
header_size = int.from_bytes(prefix[:4], "big")
body_size = int.from_bytes(prefix[4:], "big")
time.sleep(0.5)
sys.stdin.buffer.read(header_size + body_size)
print("request drained", file=sys.stderr, flush=True)
time.sleep(0.5)
sys.stdout.buffer.write({response!r})
sys.stdout.buffer.flush()
sys.stdin.buffer.read()
"""
    async with running_child(script) as (pool, process):
        # This payload exceeds pipe capacity, so drain() really waits for the child's
        # first delay. Giving read_frame() a fresh deadline would incorrectly succeed.
        with pytest.raises(SchematicTimeoutError):
            await pool._workers[0].request("analyze", {}, (b"x" * (4 * 1024 * 1024),), 0.8)

        assert any("request drained" in record.getMessage() for record in caplog.records)
        assert process.returncode is not None


async def test_queue_wait_can_consume_the_whole_operation_deadline() -> None:
    pool = SchematicWorkerPool(SchematicConfig(workers=1, restart_backoff_seconds=0.01))
    async with pool._available:
        with pytest.raises(SchematicTimeoutError) as raised:
            await pool._call_unmeasured("analyze", {}, (b"data",), 0.001)

    assert raised.value.operation == "analyze"
    assert pool._workers[0]._process is None
    assert pool._waiting == 0
    assert not pool._available.locked()


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

    with pytest.raises(CallObserved):
        await pool.compare(b"left", b"right", preset=FingerprintPreset.SHAPE, timeout_seconds=0.5)

    assert observed == [
        ("capabilities", 1.0),
        ("analyze", 1.0),
        ("convert", 3.0),
        ("compare", 2.0),
        ("render", 4.0),
        ("simulate", 5.0),
        ("autostack", 3.0),
        ("compare", 0.5),
    ]
