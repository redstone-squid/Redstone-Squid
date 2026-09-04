from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from whenever import Instant

from squid.core.errors import InvalidStateError
from squid.minecraft_auth.domain import MinecraftClientOrigin, PlayerGrant
from squid.minecraft_auth.infrastructure.models import PlayerChallengeRecord
from squid.minecraft_auth.infrastructure.repository import PostgresMinecraftAuthorizationRepository


def test_grant_mismatch_raises_a_structured_secret_free_invariant_error() -> None:
    challenge_id = UUID("11111111-1111-4111-8111-111111111111")
    grant_id = UUID("22222222-2222-4222-8222-222222222222")
    record = cast(
        PlayerChallengeRecord,
        SimpleNamespace(
            id=challenge_id,
            approved_by_account_id=7,
            java_uuid=UUID("33333333-3333-4333-8333-333333333333"),
            origin=MinecraftClientOrigin.FABRIC.value,
            installation_id=None,
            installation_credential_version=None,
        ),
    )
    grant = PlayerGrant(
        id=grant_id,
        challenge_id=challenge_id,
        token_hash=b"secret digest must not escape",
        account_id=8,
        java_uuid=record.java_uuid,
        origin=MinecraftClientOrigin.FABRIC,
        installation_id=None,
        installation_credential_version=None,
        issued_at=Instant.parse_iso("2026-08-31T00:00:00Z"),
        expires_at=Instant.parse_iso("2026-08-31T00:05:00Z"),
    )

    with pytest.raises(InvalidStateError) as exc_info:
        PostgresMinecraftAuthorizationRepository._validate_grant_matches(record, grant)

    assert exc_info.value.context == {"challenge_id": str(challenge_id), "grant_id": str(grant_id)}
    assert "secret" not in str(exc_info.value)
