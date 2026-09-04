"""Typed attachment enrichment shared by submission entry points."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from squid.bot.submission.attachments import ClassifiedAttachment
from squid.builds.domain.models import SchematicDuplicateInfo, SchematicDuplicateSource
from squid.schematics.application import DuplicateCandidate, IngestedSchematic, IngestRequest

type AttachmentFailureStage = Literal["classification", "download", "mirror", "analysis", "record"]


@dataclass(frozen=True, slots=True)
class AttachmentFailure:
    """A user-visible failure affecting one attachment and one processing stage."""

    stage: AttachmentFailureStage
    detail: str


@dataclass(frozen=True, slots=True)
class AttachmentLifecycle:
    """One attachment's identity and accumulated submission facts."""

    identity: str
    filename: str
    classification: ClassifiedAttachment | None = None
    request: IngestRequest | None = None
    analysis: IngestedSchematic | None = None
    failure: AttachmentFailure | None = None
    media_url: str | None = None
    primary: bool = False

    def __post_init__(self) -> None:
        if not self.identity:
            msg = "Attachment identity cannot be empty."
            raise ValueError(msg)
        if self.analysis is not None and (
            self.classification is None or self.classification.kind != "schematic" or self.request is None
        ):
            msg = "An analysis requires a classified schematic request."
            raise ValueError(msg)
        if self.primary and self.analysis is None:
            msg = "Only a successfully analyzed schematic can be primary."
            raise ValueError(msg)
        if self.media_url is not None and (self.classification is None or self.classification.kind == "schematic"):
            msg = "Only an image or video can carry a mirrored media URL."
            raise ValueError(msg)

    @property
    def usable_schematic(self) -> bool:
        """Whether this attachment can be selected and persisted as a schematic."""
        return self.analysis is not None


def default_only_usable(attachments: Sequence[AttachmentLifecycle]) -> tuple[AttachmentLifecycle, ...]:
    """Default the sole usable schematic and leave every ambiguous set unselected."""
    usable = [item.identity for item in attachments if item.usable_schematic]
    selected = usable[0] if len(usable) == 1 else None
    return tuple(replace(item, primary=item.identity == selected) for item in attachments)


def select_primary(attachments: Sequence[AttachmentLifecycle], identity: str) -> tuple[AttachmentLifecycle, ...]:
    """Select one usable schematic by stable attachment identity."""
    if not any(item.identity == identity and item.usable_schematic for item in attachments):
        msg = f"Attachment {identity!r} is not a usable schematic."
        raise ValueError(msg)
    return tuple(replace(item, primary=item.identity == identity) for item in attachments)


def primary_schematic(attachments: Sequence[AttachmentLifecycle]) -> AttachmentLifecycle | None:
    """Return the one explicitly selected usable schematic, if there is one."""
    selected = [item for item in attachments if item.primary and item.usable_schematic]
    return selected[0] if len(selected) == 1 else None


def merge_duplicate_evidence(
    matches: Iterable[tuple[AttachmentLifecycle, DuplicateCandidate]],
    titles: Mapping[int, str],
) -> list[SchematicDuplicateInfo]:
    """Merge duplicate candidates by build while retaining every source attachment."""
    tier_order = {"identical": 0, "structural-match": 1, "near": 2}
    strongest: dict[int, DuplicateCandidate] = {}
    sources: dict[int, dict[str, SchematicDuplicateSource]] = {}
    for attachment, candidate in matches:
        existing = strongest.get(candidate.build_id)
        if existing is None or (
            tier_order[candidate.tier],
            candidate.footprint_distance,
        ) < (tier_order[existing.tier], existing.footprint_distance):
            strongest[candidate.build_id] = candidate
        sources.setdefault(candidate.build_id, {})[attachment.identity] = {
            "attachment_id": attachment.identity,
            "filename": attachment.filename,
        }

    ordered = sorted(
        strongest.values(),
        key=lambda candidate: (
            tier_order[candidate.tier],
            candidate.footprint_distance,
            candidate.build_id,
        ),
    )
    return [
        {
            "build_id": candidate.build_id,
            "title": titles.get(candidate.build_id, f"Build #{candidate.build_id}"),
            "tier": candidate.tier,
            "footprint_distance": candidate.footprint_distance,
            "source_attachments": sorted(
                sources[candidate.build_id].values(),
                key=lambda source: (source["filename"].casefold(), source["attachment_id"]),
            ),
        }
        for candidate in ordered
    ]
