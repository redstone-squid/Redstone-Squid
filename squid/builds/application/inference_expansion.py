"""Convert model expansion claims into partial, source-grounded storage facts."""

import logging
import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from squid.builds.domain.expansion import (
    DoorExpansion,
    ExpansionBranch,
    ExpansionEvidence,
    ExpansionParameter,
    PositiveInteger,
    SourceText,
)

logger = logging.getLogger(__name__)


class InferredExpansionParameter(BaseModel):
    """Allow incomplete model bounds until the application reconciles them."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    name: str = Field(min_length=1, max_length=64)
    meaning: SourceText | None = None
    minimum: int | None = Field(
        default=None, ge=0, strict=True, description="Source-stated lower bound, including explicit ranges."
    )
    maximum_kind: Literal["unknown", "finite", "unbounded"] = Field(
        default="unknown", description="Use finite with a numeric maximum; unbounded only when explicitly stated."
    )
    maximum: int | None = Field(
        default=None, ge=0, strict=True, description="Source-stated upper bound; required with finite."
    )
    initial_steps: list[PositiveInteger] | None = Field(default=None, max_length=64)
    repeating_steps: list[PositiveInteger] | None = Field(default=None, min_length=1, max_length=64)


class InferredDoorExpansion(BaseModel):
    """The model's wire shape, before conditional validation and source checks."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    schema_version: Literal[1] = 1
    raw_description: SourceText | None = Field(
        default=None, description="Verbatim primary-source excerpts; leave null instead of writing a summary."
    )
    parameters: list[InferredExpansionParameter] = Field(default_factory=list, max_length=16)
    branches: list[ExpansionBranch] = Field(default_factory=list, max_length=64)
    evidence: list[ExpansionEvidence] = Field(default_factory=list, max_length=64)

    def to_domain(self, sources: Mapping[int, str]) -> DoorExpansion:
        """Keep partial facts without guessing bounds or trusting invented quotations.

        Sources contain only this candidate's selected primary messages, with
        whitespace normalized. Formula strings are retained only when they occur
        there; unsupported algebra remains unevaluated source text.
        """
        parameters: dict[str, ExpansionParameter] = {}
        for value in self.parameters:
            if value.name in parameters or _quote(value.name, sources) is None:
                continue
            fields = value.model_dump()
            if value.maximum is None and value.maximum_kind == "finite":
                fields["maximum_kind"] = "unknown"
            elif value.maximum is not None:
                fields["maximum_kind"] = "finite"
            if value.minimum is not None and value.maximum is not None and value.minimum > value.maximum:
                fields.update(minimum=None, maximum=None, maximum_kind="unknown")
            try:
                parameters[value.name] = ExpansionParameter.model_validate(fields)
            except ValidationError:
                logger.warning("Discarded invalid inferred expansion parameter")

        evidence = _ground_evidence(self.evidence, sources)
        branches: list[ExpansionBranch] = []
        for branch in self.branches:
            updates: dict[str, object] = {}
            branch_evidence = _ground_evidence(branch.evidence, sources)
            for name in (
                "opening_width",
                "opening_height",
                "opening_depth",
                "domain",
                "volume",
                "claimed_record_domain",
            ):
                text = getattr(branch, name)
                if text is None:
                    continue
                quote = _quote(text, sources)
                if quote is None:
                    updates[name] = None
                elif name in {"domain", "volume", "claimed_record_domain"} and quote not in branch_evidence:
                    branch_evidence.append(quote)
            updates["timings"] = tuple(timing for timing in branch.timings if _quote(timing.expression, sources))
            updates["evidence"] = tuple(branch_evidence[:64])
            branches.append(branch.model_copy(update=updates))
            evidence.extend(item for item in branch_evidence if item not in evidence)
        raw = _quote(self.raw_description, sources) if self.raw_description else None
        raw_description = raw.text if raw is not None else None
        if self.raw_description is not None and raw is None:
            # Replace a generated summary with actual excerpts, never with new prose.
            excerpts: list[str] = []
            for item in evidence:
                if any(item.text in existing for existing in excerpts):
                    continue
                combined = [existing for existing in excerpts if existing not in item.text] + [item.text]
                if len("\n".join(combined)) <= 10_000:
                    excerpts = combined
            raw_description = "\n".join(excerpts) or None
        return DoorExpansion(
            raw_description=raw_description,
            parameters=tuple(parameters.values()),
            branches=tuple(branches),
            evidence=tuple(evidence[:64]),
        )


def _quote(text: str, sources: Mapping[int, str]) -> ExpansionEvidence | None:
    excerpt = " ".join(text.split())
    if not excerpt:
        return None
    identifier = re.fullmatch(r"[^\W\d]\w*", excerpt) is not None
    pattern = rf"(?<![^\W\d]){re.escape(excerpt)}(?!\w)"
    for key, source in sources.items():
        matches = re.search(pattern, source) is not None if identifier else excerpt in source
        if matches:
            return ExpansionEvidence(source_message_id=key, text=excerpt)
    return None


def _ground_evidence(
    evidence: list[ExpansionEvidence] | tuple[ExpansionEvidence, ...], sources: Mapping[int, str]
) -> list[ExpansionEvidence]:
    grounded: list[ExpansionEvidence] = []
    for item in evidence:
        if item.source_message_id not in sources:
            continue
        quote = _quote(item.text, {item.source_message_id: sources[item.source_message_id]})
        if quote is not None and quote not in grounded:
            grounded.append(quote)
    return grounded
