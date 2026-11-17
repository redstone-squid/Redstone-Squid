"""Run with uv run scripts/benchmarks/ui_planning_cache.py; no network or delivery."""

from dataclasses import dataclass
from statistics import median
from time import perf_counter

import squid_ui as sl
from squid_ui.ext.discord import DISCORD_V2_DPY27
from squid_ui.ext.discord import scene as discord_scene
from squid_ui.pages import PagePosition
from squid_ui.planning import CompositionOptions, OccurrenceId, PlanningSnapshot, plan
from squid_ui.presentation_state import PresentationState


@dataclass
class Receiver:
    revision: int

    async def handle(self, event: sl.PressEvent) -> None:
        pass


def run(scenario: str, enabled: bool, count: int = 60) -> tuple[float, int, int, int]:
    session = PresentationState()
    snapshot = PlanningSnapshot[discord_scene.ComponentsV2](enabled=enabled)
    paragraphs = tuple(sl.paragraph(f"Item {index}", key=str(index)) for index in range(12))
    handler = Receiver(0).handle
    samples: list[float] = []
    evaluations = 0
    options = CompositionOptions(automatic=True, partitions={OccurrenceId(("root", "r")): (6, 12)})
    document = sl.Document(
        (
            sl.region(*paragraphs, key="r", title="Region"),
            sl.action_controls(sl.action_control("Run", handler, key="run"), key="actions"),
        )
    )
    for index in range(count + 1):
        if scenario == "cold-start":
            session = PresentationState()
            snapshot = PlanningSnapshot[discord_scene.ComponentsV2](enabled=enabled)
        if scenario in ("callbacks", "leaf-edit"):
            children = paragraphs
            if scenario == "leaf-edit":
                children = (*paragraphs[:-1], sl.paragraph(f"Edited {index}", key="11"))
            document = sl.Document(
                (
                    sl.region(*children, key="r", title="Region"),
                    sl.action_controls(sl.action_control("Run", Receiver(index).handle, key="run"), key="actions"),
                )
            )
        positions = {"$/r": PagePosition(page=index % 2)} if scenario == "page-turn" else None
        start = perf_counter()
        result = plan(
            document,
            target=DISCORD_V2_DPY27,
            session=session,
            snapshot=snapshot,
            composition=options,
            positions=positions,
        )
        elapsed = perf_counter() - start
        session.apply_updates(result.session_updates)
        snapshot.commit(result, session)
        if index:
            samples.append(elapsed * 1000)
            evaluations += result.metrics.target_evaluations
    stats = snapshot.fingerprints.snapshot()
    return median(samples), evaluations, stats.hits, stats.retained_bytes


def main() -> None:
    print("scenario,reuse,median_ms,target_evaluations,fingerprint_hits,accounted_bytes")
    for scenario in ("cold-start", "exact", "callbacks", "page-turn", "leaf-edit"):
        for enabled in (False, True):
            elapsed, evaluations, hits, size = run(scenario, enabled)
            print(f"{scenario},{enabled},{elapsed:.3f},{evaluations},{hits},{size}")


if __name__ == "__main__":
    main()
