"""Public search metadata representations."""

from pydantic import BaseModel, ConfigDict

from squid.search.application.fields import FieldDefinition


class SearchField(BaseModel):
    """One query field supported by the public search grammar."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    supports_range: bool
    supports_sort: bool
    aliases: list[str]

    @classmethod
    def from_domain(cls, field: FieldDefinition) -> "SearchField":
        return cls(
            name=field.name,
            type=field.value_type.value,
            supports_range=field.supports_range,
            supports_sort=field.supports_sort,
            aliases=list(field.aliases),
        )
