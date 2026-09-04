"""The schematic worker child process.

Run as ``python -m squid.schematics.infrastructure.worker_main``. Reads request frames from
stdin, writes response frames to stdout, and logs JSON records to stderr, which the supervisor
re-emits under each record's own logger name.

This process exists so that the native engine's failure modes stay contained. wgpu, rayon, and
MCHPRS each spawn their own threads; a panic in one of them, or a Rust profile built with
``panic = "abort"``, terminates the *process*. Here that costs one worker and an automatic
respawn instead of the bot.

Guardrails are installed **before** the engine is imported, so a malicious file cannot exhaust
the host during module initialisation either.
"""

import base64
import faulthandler
import json
import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any, cast

try:
    import resource
except ImportError:  # pragma: no cover - Windows has no rlimits
    resource = None  # type: ignore[assignment]

from squid.config import load_worker_log_config, load_worker_observability_config
from squid.core.errors import DomainError, SquidError
from squid.logging_config import configure_worker_logging
from squid.observability import configure_observability, extracted_trace_span
from squid.schematics.application.commands import SimulationRequest
from squid.schematics.domain.models import (
    FingerprintPreset,
    SchematicFormat,
    SchematicLimits,
)
from squid.schematics.domain.values import VerifiedResourcePack
from squid.schematics.errors import (
    AmbiguousSimulationInputError,
    InvalidSchematicError,
    SchematicTooLargeError,
)
from squid.schematics.infrastructure import wire
from squid.schematics.infrastructure.wire import ErrorKind, Frame

logger = logging.getLogger(__name__)


class _RequestContextFilter(logging.Filter):
    """Stamp the request being served onto every record the child emits.

    Attached to the handler rather than one logger so that records from the native engine's
    own loggers are correlated too. `serve` handles one request at a time, so plain mutable
    state is enough — there is no interleaving to protect against.
    """

    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, Any] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in self.fields.items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


_request_context = _RequestContextFilter()

STATM_PATH = Path("/proc/self/statm")
"""Linux's per-process memory counters, whose first field is the mapped size in pages.

Absent on the other POSIX hosts this module still has to start on, which is why every read of
it tolerates failure rather than treating the file as guaranteed.
"""

_RESOURCE_PACK: VerifiedResourcePack | None = None
"""Cached across requests. Reading and parsing a vanilla resource pack is expensive, and it is
the only reason this is a persistent worker rather than a one-shot subprocess. A `Schematic`
is deliberately *never* cached here: `simulate` attaches a live world to one."""


def apply_guardrails(limits: Mapping[str, int]) -> None:
    """Cap this process's resources before the native engine is imported.

    POSIX only; on other platforms the per-operation deadline and the supervisor's kill are
    the only bounds, which is acceptable because deployment is Linux.

    `RLIMIT_NPROC` is deliberately **not** set. It counts processes per real UID rather than
    per process, so any value low enough to constrain a fork bomb also stops the engine's
    rayon and wgpu pools from creating threads on a busy host. Runaway children are handled
    instead by `start_new_session=True` plus a process-group kill in the supervisor.
    """
    faulthandler.enable()
    if resource is None:  # pragma: no cover - Windows
        return

    _set_limit(resource.RLIMIT_AS, limits["memory_bytes"] + _current_address_space_bytes())  # pyrefly: ignore[missing-attribute]
    _set_limit(resource.RLIMIT_CPU, limits["cpu_seconds"])  # pyrefly: ignore[missing-attribute]
    _set_limit(resource.RLIMIT_FSIZE, limits["file_size_bytes"])  # pyrefly: ignore[missing-attribute]
    try:
        os.nice(5)  # pyrefly: ignore[missing-attribute]
    except OSError:  # pragma: no cover - permitted to fail in restricted sandboxes
        logger.debug("Could not lower worker priority.", exc_info=True)


def _current_address_space_bytes(statm_path: Path = STATM_PATH) -> int:
    """This process's already-mapped virtual address space, so `RLIMIT_AS` bounds the budget
    a payload gets rather than the interpreter's own baseline footprint.

    That baseline is not portable: Termux's bionic/Scudo allocator reserves on the order of
    10 GB of address space before a single line of this module runs, dwarfing the configured
    memory budget and making an absolute `RLIMIT_AS` fail during interpreter start-up, before
    the engine is even imported. glibc hosts have a much smaller baseline, which is why this
    went unnoticed there. `resource.getrusage` reports peak resident set size, not mapped
    address space, so it can't stand in for this.

    `statm_path` is a parameter because a real `/proc/self/statm` reports whatever the host
    happens to have mapped, so tests can neither pin the arithmetic nor reach the fallback.
    """
    assert resource is not None
    try:
        with statm_path.open("rb") as handle:
            size_pages = int(handle.readline().split()[0])
    except OSError, ValueError, IndexError:
        return 0
    return size_pages * resource.getpagesize()  # pyrefly: ignore[missing-attribute]


def _set_limit(which: int, soft: int) -> None:
    """Lower one resource limit, never raising it above the inherited hard limit."""
    assert resource is not None
    try:
        _, hard = resource.getrlimit(which)  # pyrefly: ignore[missing-attribute]
        ceiling = soft if hard == resource.RLIM_INFINITY else min(soft, hard)  # pyrefly: ignore[missing-attribute]
        resource.setrlimit(which, (ceiling, hard))  # pyrefly: ignore[missing-attribute]
    except OSError, ValueError:  # pragma: no cover - depends on host policy
        logger.warning("Could not apply resource limit %s.", which, exc_info=True)


def handle(operation: str, params: Mapping[str, Any], payloads: tuple[bytes, ...]) -> tuple[Mapping[str, Any], bytes]:
    """Dispatch one request, returning its JSON result and any binary payload.

    Imported lazily so that :func:`apply_guardrails` has already run by the time the native
    extension is loaded.
    """
    from squid.schematics.infrastructure import nucleation_adapter as engine

    if operation == "capabilities":
        return {"capabilities": wire.encode_capabilities(engine.capabilities())}, b""

    if operation == "analyze":
        source_format = params.get("source_format")
        analysis = engine.analyze(
            payloads[0],
            limits=SchematicLimits(**cast(dict[str, int], params["limits"])),
            with_lattice=bool(params.get("with_lattice", False)),
            source_format=SchematicFormat(source_format) if source_format else None,
            lattice_max_block_count=int(params.get("lattice_max_block_count", 200_000)),
        )
        return {"analysis": wire.encode_analysis(analysis)}, b""

    if operation == "convert":
        data_version = params.get("data_version")
        converted, losses = engine.convert(
            payloads[0],
            target=SchematicFormat(params["target"]),
            data_version=int(data_version) if data_version is not None else None,
        )
        return {"losses": wire.encode_losses(losses)}, converted

    if operation == "compare":
        comparison = engine.compare(payloads[0], payloads[1], preset=FingerprintPreset(params["preset"]))
        return {"comparison": wire.encode_comparison(comparison)}, b""

    if operation == "render":
        return {}, engine.render(
            payloads[0],
            request=wire.decode_render_request(cast(Mapping[str, Any], params["request"])),
            resource_pack=_resource_pack(params),
        )

    if operation == "simulate":
        result = engine.simulate(payloads[0], request=_simulation_request(params))
        return {"simulation": wire.encode_simulation(result)}, b""

    if operation == "autostack":
        stacked = engine.autostack(
            payloads[0],
            lattice=wire.decode_lattice(cast(Mapping[str, Any], params["lattice"])),
            counts=tuple(int(count) for count in params["counts"]),
        )
        return {}, stacked

    msg = f"Unknown schematic worker operation {operation!r}."
    raise InvalidSchematicError(msg, developer_action="The supervisor sent an operation this worker cannot handle.")


def _simulation_request(params: Mapping[str, Any]) -> SimulationRequest:
    request = cast(Mapping[str, Any], params["request"])
    return SimulationRequest(
        input_position=(
            tuple(int(axis) for axis in request["input_position"])  # type: ignore[arg-type]
            if request.get("input_position") is not None
            else None
        ),
        watch_positions=tuple(tuple(int(axis) for axis in position) for position in request.get("watch_positions", ())),  # type: ignore[arg-type]
        max_ticks=int(request.get("max_ticks", 200)),
    )


def _resource_pack(params: Mapping[str, Any]) -> VerifiedResourcePack:
    """Return the cached resource pack, accepting a fresh one when the supervisor sends it."""
    global _RESOURCE_PACK
    encoded = params.get("resource_pack_b64")
    if encoded is not None:
        metadata = cast(Mapping[str, Any], params["resource_pack"])
        _RESOURCE_PACK = wire.decode_resource_pack(metadata, base64.b64decode(cast(str, encoded)))
    if _RESOURCE_PACK is None:
        msg = "No resource pack has been supplied to this worker."
        raise InvalidSchematicError(msg, developer_action="Send resource_pack_b64 with the first render request.")
    return _RESOURCE_PACK


def _error_payload(exc: Exception) -> Mapping[str, Any]:
    """Describe a failure in terms the supervisor can turn back into a typed exception."""
    kind: ErrorKind = "internal"
    context: dict[str, Any] = {}
    if isinstance(exc, SchematicTooLargeError):
        kind = "too_large"
        context = {"actual": exc.actual, "limit": exc.limit, "measure": exc.measure}
    elif isinstance(exc, AmbiguousSimulationInputError):
        # Checked before its `InvalidSchematicError` base, so the candidate coordinates the
        # caller needs survive the pipe instead of collapsing into a generic rejection.
        kind = "ambiguous_simulation_input"
        context = {"candidates": [list(candidate) for candidate in exc.candidates], "rejected": exc.rejected}
    elif isinstance(exc, InvalidSchematicError):
        kind = "invalid"
        context = dict(exc.context)
    elif isinstance(exc, SquidError):
        context = dict(exc.context)
    return {"kind": kind, "message": str(exc), "context": context}


def _read_exactly(stream: IO[bytes], size: int) -> bytes:
    """Read `size` bytes from a blocking binary stream, or fewer at end of file."""
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def serve(stdin: IO[bytes], stdout: IO[bytes]) -> None:
    """Handle framed requests until the supervisor closes stdin.

    One request at a time, matching the supervisor's per-worker lock. Concurrency comes from
    running several workers, not from interleaving inside one.
    """
    while True:
        prefix = _read_exactly(stdin, 2 * wire.LENGTH_PREFIX_BYTES)
        if len(prefix) < 2 * wire.LENGTH_PREFIX_BYTES:
            return
        header_length = int.from_bytes(prefix[: wire.LENGTH_PREFIX_BYTES], "big")
        body_length = int.from_bytes(prefix[wire.LENGTH_PREFIX_BYTES :], "big")
        header = cast(Mapping[str, Any], json.loads(_read_exactly(stdin, header_length).decode("utf-8")))
        body = _read_exactly(stdin, body_length)

        payloads: list[bytes] = []
        offset = 0
        for size in header.get("parts", []):
            payloads.append(body[offset : offset + int(size)])
            offset += int(size)

        request_id = header.get("id")
        operation = str(header["op"])
        params = cast(Mapping[str, Any], header.get("params", {}))
        attributes: dict[str, str] = {"squid.schematic.operation": operation}
        schematic_format = params.get("source_format") or params.get("target")
        if isinstance(schematic_format, str):
            attributes["squid.schematic.format"] = schematic_format
        context: dict[str, Any] = {
            "squid.schematic.request_id": request_id,
            "squid.schematic.operation": operation,
        }
        if header.get("job_id") is not None:
            context["squid.schematic.job_id"] = header["job_id"]
        _request_context.fields = context
        try:
            with extracted_trace_span(f"schematic.worker {operation}", header, attributes) as span:
                try:
                    result, output = handle(operation, params, tuple(payloads))
                    response = Frame({"id": request_id, "ok": True, "result": result}, (output,) if output else ())
                except Exception as exc:
                    if isinstance(exc, SquidError):
                        span.set_attribute("squid.error.code", exc.code.value)
                    # A rejected upload is an ordinary outcome, not an incident: the supervisor
                    # re-raises it as a typed error and the user sees a translated message. Only
                    # failures we did not anticipate deserve a stderr traceback.
                    expected = isinstance(exc, DomainError)
                    if not expected:
                        span.set_error(exc)
                    logger.log(
                        logging.DEBUG if expected else logging.WARNING,
                        "Schematic operation %s failed: %s",
                        operation,
                        exc,
                        exc_info=not expected,
                    )
                    response = Frame({"id": request_id, "ok": False, "error": _error_payload(exc)})
        finally:
            _request_context.fields = {}

        stdout.write(response.encode())
        stdout.flush()


def main() -> None:
    """Install guardrails from the supervisor's environment, then serve requests."""
    log_config = load_worker_log_config()
    # "INFO" matches WorkerProcessConfig.logging, so the child's floor is the supervisor's.
    configure_worker_logging(level=log_config.level, root_level=log_config.root_level or "INFO")
    stderr_handler = logging.getHandlerByName("stderr")
    if stderr_handler is not None:
        stderr_handler.addFilter(_request_context)
    limits = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    apply_guardrails(
        {
            "memory_bytes": int(limits.get("memory_bytes", 2 * 1024 * 1024 * 1024)),
            "cpu_seconds": int(limits.get("cpu_seconds", 900)),
            "file_size_bytes": int(limits.get("file_size_bytes", 64 * 1024 * 1024)),
        }
    )
    # Not "worker": that is the supervising database worker, and sharing a service.name makes
    # a schematic child's spans and metrics indistinguishable from its parent's.
    observability = configure_observability(load_worker_observability_config(), service_name="schematic-worker")
    try:
        serve(sys.stdin.buffer, sys.stdout.buffer)
    finally:
        observability.shutdown()


if __name__ == "__main__":
    main()
