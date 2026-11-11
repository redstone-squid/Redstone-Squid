"""Editable guild settings and voting configuration share one revision."""

from typing import Self

from pydantic import BaseModel, ConfigDict, PositiveInt, field_validator, model_validator

from squid.core.i18n import SUPPORTED_LOCALES
from squid.settings.domain.models import Setting
from squid.voting.domain.models import VoteConfiguration, VoteKind, normalize_vote_options

CHANNEL_FIELDS: dict[Setting, str] = {
    "Smallest": "smallest_channel_id",
    "Fastest": "fastest_channel_id",
    "First": "first_channel_id",
    "Builds": "builds_channel_id",
    "Vote": "voting_channel_id",
}


class GuildSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    smallest_channel_id: PositiveInt | None = None
    fastest_channel_id: PositiveInt | None = None
    first_channel_id: PositiveInt | None = None
    builds_channel_id: PositiveInt | None = None
    voting_channel_id: PositiveInt | None = None
    locale: str | None = None
    voting: dict[VoteKind, VoteConfiguration]

    @field_validator("locale")
    @classmethod
    def supported_locale(cls, value: str | None) -> str | None:
        if value is not None and value not in SUPPORTED_LOCALES:
            message = "Choose a supported bot language"
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def valid_voting(self) -> Self:
        if set(self.voting) != set(VoteKind):
            message = "Guild settings must include every voting kind"
            raise ValueError(message)
        for kind, configuration in self.voting.items():
            if configuration.preset is not None:
                normalize_vote_options(configuration.preset.options, kind=kind)
            roles = [weight.role_id for weight in configuration.weights]
            if len(roles) != len(set(roles)):
                message = "A role may have only one weight per voting kind"
                raise ValueError(message)
        return self
