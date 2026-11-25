"""Publishing only affected categories must match recomputing everything.

The test keeps an in-memory publication: a fingerprint per live record and the
members of each live category, as the repository stores them. Random build
edits are applied one at a time through the affected-set path, and after each
the publication must equal a full recompute from nothing. Populations mix
thresholds, restrictions, sizes, versions and families, the cases where a
change reaches categories without the changed build.
"""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from squid.core.builds.domain.expansion import DoorExpansion, ExpansionBranch, ExpansionParameter
from squid.core.records.application.models import (
    CandidateBuild,
    CandidateTag,
    ComputationBatch,
    PriorPublication,
    ScopedCategory,
)
from squid.core.records.application.services import RecordComputationService
from squid.core.records.application.snapshots import RecordSnapshot
from squid.core.records.domain import BuildKind, Candidate, DoorCategory, RecordCriterion, TimingVariant
from squid.core.records.domain.models import RecordTagRole
from squid.core.records.infrastructure.fingerprint import record_fingerprint

_PIN = 2
_TYPE = CandidateTag(id=20, kind=RecordTagRole.PATTERN, name="Regular")
_FLUSH = CandidateTag(id=1, kind=RecordTagRole.RESTRICTION, name="Flush", restriction_type="wiring-placement")
_SILENT = CandidateTag(id=2, kind=RecordTagRole.RESTRICTION, name="Silent", restriction_type="miscellaneous")
_FAMILY = DoorExpansion(
    parameters=(
        ExpansionParameter(name="n", minimum=2, maximum_kind="unbounded", initial_steps=(), repeating_steps=(1,)),
    ),
    branches=(ExpansionBranch(opening_width="n", opening_height="2", opening_depth="1", volume="5n"),),
)


def _width(value: int) -> CandidateTag:
    return CandidateTag(
        id=7,
        kind=RecordTagRole.RESTRICTION,
        name="Width",
        restriction_type="miscellaneous",
        value_type="numeric",
        assigned_value=Decimal(value),
        record_operator="at_most",
    )


@st.composite
def _builds(draw: st.DrawFn, build_id: int) -> CandidateBuild:
    family = draw(st.booleans()) and draw(st.booleans())
    width = draw(st.sampled_from((2, 3, 4)))
    restrictions = tuple(
        tag
        for tag, present in (
            (_FLUSH, draw(st.booleans())),
            (_SILENT, draw(st.booleans())),
            (_width(draw(st.sampled_from((2, 3, 4)))), draw(st.booleans())),
        )
        if present
    )
    versions = frozenset(draw(st.sets(st.sampled_from((1, 2)), max_size=2)))
    door = DoorCategory(
        wiring_restrictions=(),
        animated_restrictions=(),
        size="variable" if family else f"{width}x2",
        types=("Regular",),
        orientation="Door",
    )
    if family:
        return CandidateBuild(
            kind=BuildKind.DOOR,
            candidate=Candidate(build_id),
            version_ids=versions,
            restrictions=restrictions,
            types=(_TYPE,),
            door=door,
            expansion=_FAMILY,
        )
    day = draw(st.none() | st.integers(1, 5))
    return CandidateBuild(
        kind=BuildKind.DOOR,
        candidate=Candidate(
            build_id,
            completion_at=None if day is None else datetime(2020, 1, day, tzinfo=UTC),
            fixed_volume=draw(st.none() | st.integers(8, 14)),
            timing_variants=(TimingVariant((draw(st.none() | st.integers(1, 4)), None, None, None, None, None)),),
        ),
        version_ids=versions,
        restrictions=restrictions,
        types=(_TYPE,),
        door=door,
        door_opening=(width, 2, 1),
    )


@dataclass
class _Publication:
    """What the repository would store: live record contents and live category members."""

    records: dict[tuple[int | None, str, RecordCriterion], str] = field(default_factory=dict)
    members: dict[ScopedCategory, frozenset[int]] = field(default_factory=dict)

    def prior(self, changed: frozenset[int]) -> PriorPublication:
        return PriorPublication(
            live=frozenset(self.members),
            memberships={
                build_id: frozenset(category for category, ids in self.members.items() if build_id in ids)
                for build_id in changed
            },
        )

    def apply(self, batch: ComputationBatch) -> None:
        for category in batch.categories:
            self.members.pop(category, None)
            for key in [key for key in self.records if key[:2] == category]:
                del self.records[key]
        for record in batch.records:
            category = (record.version_id, record.category.identity.key)
            assert category in batch.categories
            self.records[(*category, record.criterion)] = record_fingerprint(record)
            self.members[category] = frozenset(candidate.build_id for candidate in record.category.candidates)


def _snapshot(sources: dict[int, CandidateBuild], **changes: object) -> RecordSnapshot:
    snapshot = RecordSnapshot(
        rules_revision_id=1,
        kind=BuildKind.DOOR,
        version_ids=(None, _PIN),
        sources=tuple(sources[build_id] for build_id in sorted(sources)),
        requested=(),
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        full=True,
    )
    return replace(snapshot, **changes)  # type: ignore[arg-type]


def _published(computation: RecordComputationService, sources: dict[int, CandidateBuild]) -> _Publication:
    publication = _Publication()
    publication.apply(computation.compute_snapshot(_snapshot(sources)))
    return publication


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.data())
def test_incremental_publication_matches_a_full_recompute(data: st.DataObject) -> None:
    computation = RecordComputationService(uow_factory=lambda: None)  # type: ignore[arg-type,return-value]
    count = data.draw(st.integers(1, 5), label="population")
    sources = {build_id: data.draw(_builds(build_id), label=f"build {build_id}") for build_id in range(1, count + 1)}
    publication = _published(computation, sources)

    for step in range(data.draw(st.integers(1, 6), label="edits")):
        build_id = data.draw(st.integers(1, count + 1), label=f"edit {step}")
        if build_id in sources and data.draw(st.booleans(), label=f"delete {step}"):
            del sources[build_id]
        else:
            sources[build_id] = data.draw(_builds(build_id), label=f"replacement {step}")
            count = max(count, build_id)
        changed = frozenset({build_id})
        batch = computation.compute_snapshot(
            _snapshot(sources, full=False, changed_builds=changed, prior=publication.prior(changed))
        )
        publication.apply(batch)

        expected = _published(computation, sources)
        assert publication.records == expected.records
        assert publication.members == expected.members


def test_a_new_threshold_reaches_categories_without_the_changed_build() -> None:
    computation = RecordComputationService(uow_factory=lambda: None)  # type: ignore[arg-type,return-value]
    flush_door = CandidateBuild(
        kind=BuildKind.DOOR,
        candidate=Candidate(1, fixed_volume=10),
        version_ids=frozenset(),
        restrictions=(_FLUSH, _width(2)),
        types=(_TYPE,),
        door=DoorCategory(
            wiring_restrictions=(), animated_restrictions=(), size="2x2", types=("Regular",), orientation="Door"
        ),
        door_opening=(2, 2, 1),
    )
    sources = {1: flush_door}
    publication = _published(computation, sources)
    # Build 2 is not flush, but its width creates "width at most 3" categories,
    # including the flush one that only build 1 belongs to.
    sources[2] = replace(flush_door, candidate=Candidate(2, fixed_volume=12), restrictions=(_width(3),))
    batch = computation.compute_snapshot(
        _snapshot(sources, full=False, changed_builds=frozenset({2}), prior=publication.prior(frozenset({2})))
    )
    keys = {key for version_id, key in batch.categories if version_id is None}
    assert "door:door|2x2|t[20]|Door:r[1,7]:p[7:at_most:3]" in keys
