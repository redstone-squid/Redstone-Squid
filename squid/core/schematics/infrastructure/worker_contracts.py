"""JSON shapes exchanged outside schematic worker request frames."""

from pydantic import BaseModel, ConfigDict, Field


class WorkerLimits(BaseModel):
    """Resource ceilings passed to the child before it loads native extensions."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    memory_bytes: int = Field(default=4 * 1024 * 1024 * 1024, gt=0)
    cpu_seconds: int = Field(default=900, gt=0)
    file_size_bytes: int = Field(default=256 * 1024 * 1024, gt=0)


class WorkerLogRecord(BaseModel):
    """Core child log fields; context fields remain open for request correlation."""

    model_config = ConfigDict(extra="allow", frozen=True, strict=True)

    name: str
    levelname: str
    message: str
    pathname: str = ""
    lineno: int = 0
    created: int | float | None = None
    exc_info: str | None = None
