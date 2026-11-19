"""Concrete door evaluations and the source facts retained with published results."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from squid.core.builds.domain.expansion import DoorExpansion
from squid.core.builds.domain.expansion_evaluation import ExpansionDiagnostic, PointStatus
from squid.core.records.domain import BaseTitle, TitleResolution


@dataclass(frozen=True, slots=True)
class LiveDoorConfiguration:
    """A fixed build or one family branch; metrics use blocks and game ticks.

    ID is local to this response. A null branch index represents a fixed build
    or a whole family without usable branches; the latter has unmapped membership
    and diagnostics. Only configurations with supported membership compete.
    Family completion dates remain unknown until per-configuration evidence is modeled.
    """

    id: int
    build_id: int
    branch_index: int | None
    membership: PointStatus
    parameters: tuple[tuple[str, int], ...] = ()
    volume: int | None = None
    timing_variants: tuple[tuple[int | None, ...], ...] = ()
    completion_at: datetime | None = None
    diagnostics: tuple[ExpansionDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class LiveDoorRecord:
    """A concrete evaluation whose configuration IDs are local to this result.

    Resolution IDs identify builds. Configuration IDs identify the alternatives
    selected by the resolver. Configurations with uncertain membership remain
    in the evidence but do not compete. If all tied alternatives belong to one
    build, that build can hold the record while its exact winning configuration
    remains provisional.
    """

    opening: tuple[int, int, int]
    base_title: BaseTitle
    version_id: int | None
    evaluated_at: datetime
    resolution: TitleResolution
    holder_configuration_ids: tuple[int, ...]
    provisional_configuration_ids: tuple[int, ...]
    configurations: tuple[LiveDoorConfiguration, ...]


class DoorExpansionSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    build_id: int
    expansion: DoorExpansion


class DoorRecordEvidence(BaseModel):
    """The evaluated configurations and source documents used by one published title."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    calculator_version: Literal["door-point-1", "door-point-2"] = "door-point-2"
    evaluation: LiveDoorRecord
    sources: tuple[DoorExpansionSource, ...]
