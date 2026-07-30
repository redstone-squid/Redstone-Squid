"""Timestamp model configuration tests."""

import pytest
from sqlalchemy import inspect
from whenever import Instant

from squid.builds.infrastructure.models import Build, BuildEditHistory, RestrictionAlias
from squid.messages.infrastructure.models import Message
from squid.persistence.types import InstantUTC
from squid.users.infrastructure.models import User, VerificationCode
from squid.voting.infrastructure.models import VoteSession


@pytest.mark.parametrize(
    ("model", "column_name"),
    [
        (Build, "edited_time"),
        (Build, "locked_at"),
        (Build, "submission_time"),
        (BuildEditHistory, "created_at"),
        (Message, "updated_at"),
        (RestrictionAlias, "created_at"),
        (User, "created_at"),
        (VerificationCode, "created"),
        (VerificationCode, "expires"),
        (VoteSession, "created_at"),
    ],
)
def test_timestamp_columns_use_instant_utc(model: type[object], column_name: str) -> None:
    mapper = inspect(model)
    assert mapper is not None
    column_type = mapper.columns[column_name].type

    assert isinstance(column_type, InstantUTC)
    assert column_type.python_type is Instant
