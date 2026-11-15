"""Run with uv run scripts/benchmarks/region_pagination.py; timings are informational."""

from dataclasses import replace
from time import perf_counter

import squid_ui as sl
from squid_ui.ext.discord.limits import V2Limits
from squid_ui.ext.discord.planning.discord import components_v2_target
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.cache import PlanCache
from squid_ui.planning.navigation import default_nav
from squid_ui.presentation import PresentationState
from squid_ui.target_types import DiscordAdapter


def region(key: str, count: int) -> sl.semantic.Region:
    return sl.region(*(sl.paragraph("x" * 80, key=str(i)) for i in range(count)), key=key, title=key)


def main() -> None:
    target = components_v2_target(AdapterProfile(DiscordAdapter, "benchmark", ">=1"))
    r = OccurrenceId(("root", "r"))
    outer = OccurrenceId(("root", "outer"))
    inner = OccurrenceId(("root", "outer", "r"))
    scenarios = (
        ("many-items", region("r", 100), {r: tuple(range(10, 101, 10))}),
        (
            "nested",
            sl.region(region("r", 20), sl.paragraph("tail", key="tail"), key="outer", title="Outer"),
            {outer: (1, 2), inner: (5, 10, 15, 20)},
        ),
        ("competing-limits", region("r", 12), {}),
        ("long-text", sl.region(sl.paragraph("word " * 1500, key="text"), key="r", title="Text"), {}),
    )
    print("scenario,cold_ms,hinted_ms,turn_ms,cold_work,hinted_work,completion")
    for name, document, partitions in scenarios:
        effective = (
            replace(target, limits=V2Limits(total_components=8, total_text=800))
            if name == "competing-limits"
            else target
        )
        options = CompositionOptions(automatic=True, partitions=partitions, work_limit=200_000)
        cache = PlanCache()
        start = perf_counter()
        cold = plan(document, target=effective, composition=options, cache=cache, nav=default_nav)
        cold_ms = (perf_counter() - start) * 1000
        session = PresentationState()
        start = perf_counter()
        warm = plan(document, target=effective, composition=options, cache=cache, session=session, nav=default_nav)
        warm_ms = (perf_counter() - start) * 1000
        session.apply_updates(warm.session_updates)
        assert warm.allocation is not None
        start = perf_counter()
        for index in range(50):
            options = replace(
                options,
                pages={identity: index % len(value.cuts) for identity, value in warm.allocation.regions.items()},
            )
            turn = plan(document, target=effective, composition=options, cache=cache, session=session, nav=default_nav)
            assert turn.metrics.work_used == turn.metrics.target_evaluations == 0
        turn_ms = (perf_counter() - start) * 1000 / 50
        assert cold.report.search is not None
        print(
            f"{name},{cold_ms:.2f},{warm_ms:.2f},{turn_ms:.2f},{cold.metrics.work_used},{warm.metrics.work_used},{cold.report.search.completion}"
        )


if __name__ == "__main__":
    main()
