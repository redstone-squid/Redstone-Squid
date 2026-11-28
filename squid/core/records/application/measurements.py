"""Measurements shown for a record holder or a prospective competitor."""

from itertools import zip_longest
from typing import cast

from squid.core.records.application.models import ComputedRecordResult
from squid.core.records.domain import Candidate, RecordCriterion, TimingVariant, reduce_timing_variants


def metric_snapshot(
    computed: ComputedRecordResult, candidate: Candidate, *, include_competitor: bool = False
) -> dict[str, object]:
    if computed.door_evidence is not None:
        evaluation = computed.door_evidence.evaluation
        configurations = tuple(
            c
            for c in evaluation.configurations
            if c.build_id == candidate.build_id
            and (
                c.id in evaluation.holder_configuration_ids
                or (include_competitor and candidate.build_id not in computed.resolution.holder_ids)
            )
        )
        metrics = tuple(
            fixed_metric_snapshot(
                computed.criterion,
                Candidate(c.build_id, c.completion_at, c.volume, tuple(TimingVariant(v) for v in c.timing_variants)),
            )
            for c in configurations
        )
        snapshot: dict[str, object] = {
            "opening": list(computed.door_evidence.evaluation.opening),
            "configuration_ids": [c.id for c in configurations],
        }
        # A measurement is known only when every possible winning configuration agrees.
        for key in ("volume", "completion_at"):
            values = [metric[key] for metric in metrics if key in metric]
            if values:
                snapshot[key] = values[0] if all(value == values[0] for value in values) else None
        timings = [metric["timing"] for metric in metrics]
        if timings:
            snapshot["timing"] = None
            if all(isinstance(timing, list) for timing in timings):
                vectors = cast(list[list[int | None]], timings)
                snapshot["timing"] = [
                    first if all(value == first for value in rest) else None for first, *rest in zip_longest(*vectors)
                ]
        return snapshot
    return fixed_metric_snapshot(computed.criterion, candidate)


def fixed_metric_snapshot(criterion: RecordCriterion, candidate: Candidate) -> dict[str, object]:
    snapshot: dict[str, object] = {"volume": candidate.fixed_volume}
    if criterion is RecordCriterion.FIRST:
        snapshot["completion_at"] = candidate.completion_at.isoformat() if candidate.completion_at is not None else None
    reduction = reduce_timing_variants(candidate.timing_variants)
    snapshot["timing"] = list(reduction.timing.values) if reduction.timing is not None else None
    return snapshot
