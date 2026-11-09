from uuid import UUID

import pytest
from whenever import Instant

from squid.core.errors import InvalidStateError
from squid.minecraft_auth.domain import MinecraftClientOrigin
from squid.minecraft_auth.infrastructure.repository import PostgresMinecraftAuthorizationRepository
from squid.minecraft_auth.models import PlayerAuthorizationChallenge, PlayerGrant


def test_grant_mismatch_raises_a_structured_secret_free_invariant_error() -> None:
    challenge_id = UUID("11111111-1111-4111-8111-111111111111")
    grant_id = UUID("22222222-2222-4222-8222-222222222222")
    record = PlayerAuthorizationChallenge(
        id=challenge_id,
        approved_by_account_id=7,
        device_code_hash=b"d" * 32,
        user_code_hash=b"u" * 32,
        pkce_s256_challenge="p" * 43,
        created_at=Instant.parse_iso("2026-08-31T00:00:00Z"),
        approved_at=Instant.parse_iso("2026-08-31T00:01:00Z"),
        expires_at=Instant.parse_iso("2026-08-31T00:05:00Z"),
        java_uuid=UUID("33333333-3333-4333-8333-333333333333"),
        origin=MinecraftClientOrigin.FABRIC,
        installation_id=None,
        installation_credential_version=None,
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
