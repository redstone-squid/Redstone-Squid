"""Eager component preparation and wall-time diagnostics, separate from layout work."""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

import anyio

from squid_ui.runtime.definition import AnyComponent

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PreparationReport:
    component: AnyComponent
    paths: tuple[str, ...]
    elapsed: float
    outcome: Literal["ready", "failed", "cancelled"]
    reused: bool
    error: BaseException | None = None


@dataclass(frozen=True, slots=True)
class PreparationBatch:
    elapsed: float
    components: tuple[PreparationReport, ...]


class ComponentPreparation:
    """Load independent instances concurrently without starting mounted services.

    Reports describe the latest batch, including failures and cancellation. The
    slow threshold only emits diagnostics; it never cancels required loading.
    """

    def __init__(self, *, slow_threshold: float = 0.25) -> None:
        if slow_threshold < 0:
            message = "slow preparation threshold cannot be negative"
            raise ValueError(message)
        self.slow_threshold = slow_threshold
        self.latest = PreparationBatch(0, ())
        self.reports: dict[AnyComponent, PreparationReport] = {}

    async def prepare(self, placements: Mapping[str, AnyComponent]) -> PreparationBatch:
        paths: dict[AnyComponent, list[str]] = {}
        for path, component in placements.items():
            paths.setdefault(component, []).append(path)
        reports: dict[AnyComponent, PreparationReport] = {}
        started = perf_counter()

        async def load(component: AnyComponent, locations: Sequence[str]) -> None:
            began = perf_counter()
            reused = component._loaded
            outcome: Literal["ready", "failed", "cancelled"] = "ready"
            failure: BaseException | None = None
            try:
                if not reused:
                    await component.on_load()
                    component._loaded = True
            except BaseException as error:
                failure = error
                outcome = "cancelled" if isinstance(error, anyio.get_cancelled_exc_class()) else "failed"
                raise
            finally:
                elapsed = perf_counter() - began
                reports[component] = PreparationReport(component, tuple(locations), elapsed, outcome, reused, failure)
                if not reused and elapsed >= self.slow_threshold:
                    _logger.warning(
                        "Component preparation took %.3fs: %s at %s (%s)",
                        elapsed,
                        type(component).__name__,
                        ", ".join(locations),
                        outcome,
                    )

        try:
            if len(paths) == 1:
                component, locations = next(iter(paths.items()))
                await load(component, locations)
            else:
                async with anyio.create_task_group() as tasks:
                    for component, locations in paths.items():
                        tasks.start_soon(load, component, locations)
        finally:
            self.reports.update(reports)
            self.latest = PreparationBatch(
                perf_counter() - started, tuple(reports[component] for component in paths if component in reports)
            )
        return self.latest
