"""Validated JSON shapes for durable Discord state and session records."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WireComponent(WireModel):
    path: str
    type_id: str
    state: dict[str, Any]
    shared_with: str | None = None


class WirePosition(WireModel):
    anchor: str | None = None
    offset: int
    direction: Literal["around"] = "around"


class WireCursor(WireModel):
    position: WirePosition
    extent: int
    fingerprint: str
    reveal_request: str | None = None


class WireSelection(WireModel):
    selected: list[str]


class WireDisclosure(WireModel):
    open: bool


class WirePresentation(WireModel):
    # Old snapshots carried planner strategies that no longer affect recovery.
    model_config = ConfigDict(extra="ignore", strict=True)

    cursors: dict[str, WireCursor]
    selections: dict[str, WireSelection]
    disclosures: dict[str, WireDisclosure]
    declared_protocol: Literal[1] = 1
    declared: dict[str, dict[str, Any]] = Field(default_factory=dict)


class WireTarget(WireModel):
    triple: str
    version: int
    fingerprint: str
    adapter_capabilities: list[str]


class WireState(WireModel):
    protocol: Literal[1]
    component_key: str
    component_version: int
    components: list[WireComponent]
    presentation: WirePresentation
    target: WireTarget


class WireAddress(WireModel):
    frontend: str
    values: dict[str, str | int]


class WireMount(WireModel):
    protocol: Literal[1]
    state: WireState
    address: WireAddress
    expires_at: float | None = Field(default=None, allow_inf_nan=False)


class WireSessionRoot(WireModel):
    id: str
    state: WireState
    address: WireAddress
    parent_id: str | None
    actor_id: int | None


class WireUserScope(WireModel):
    type: Literal["user"] = "user"
    user_id: int


class WireGuildScope(WireModel):
    type: Literal["guild"] = "guild"
    guild_id: int


class WireUserGuildScope(WireModel):
    type: Literal["user_guild"] = "user_guild"
    user_id: int
    guild_id: int


class WireGlobalScope(WireModel):
    type: Literal["global"] = "global"


class WireCustomScope(WireModel):
    type: Literal["custom"] = "custom"
    value: Any


class WireSessionKey(WireModel):
    name: str = Field(min_length=1)
    scope: Annotated[
        WireUserScope | WireGuildScope | WireUserGuildScope | WireGlobalScope | WireCustomScope,
        Field(discriminator="type"),
    ]


class WireSession(WireModel):
    protocol: Literal[1]
    id: str
    key: WireSessionKey
    actor_id: int | None
    opened_at: float = Field(allow_inf_nan=False)
    expires_at: float | None = Field(allow_inf_nan=False)
    message_roots: list[WireSessionRoot]
    members: list[Annotated[int, Field(gt=0)]]
    capacity: Annotated[int, Field(gt=0)] | None
    quota: Annotated[int, Field(gt=0)] | None
    domain: Annotated[str, Field(min_length=1)] | None
