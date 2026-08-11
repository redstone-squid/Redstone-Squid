"""Deterministic fake Mojang and Discord service tests."""

import hashlib
import hmac

import httpx

from tests.fuzz.api.fake_upstreams import (
    CONTROL_HEADER,
    DISCORD_ALICE,
    DISCORD_GUILD,
    DISCORD_TRUSTED_ROLE,
    MINECRAFT_ALICE,
    OAUTH_CODE_ALICE,
    OAUTH_TOKEN_ALICE,
    create_fake_upstream_app,
)

CONTROL_NONCE = "synthetic-control-nonce-with-enough-entropy"


async def test_fake_mojang_has_a_deterministic_profile_and_unknown_boundary() -> None:
    async with _client() as client:
        known = await client.get(f"/mojang/profile/{MINECRAFT_ALICE}")
        unknown = await client.get("/mojang/profile/00000000-0000-0000-0000-000000000999")

    assert known.status_code == 200
    assert known.json()["name"] == "FuzzAlice"
    assert unknown.status_code == 204


async def test_fake_discord_exchanges_one_code_and_resolves_identity_and_member() -> None:
    async with _client() as client:
        token = await client.post("/discord/api/oauth2/token", data={"code": OAUTH_CODE_ALICE})
        identity = await client.get("/discord/api/users/@me", headers={"Authorization": f"Bearer {OAUTH_TOKEN_ALICE}"})
        member = await client.get(
            f"/discord/api/guilds/{DISCORD_GUILD}/members/{DISCORD_ALICE}",
            headers={"Authorization": "Bot synthetic-bot"},
        )

    assert token.json()["access_token"] == OAUTH_TOKEN_ALICE
    assert identity.json()["id"] == str(DISCORD_ALICE)
    assert member.json()["roles"] == [str(DISCORD_TRUSTED_ROLE)]


async def test_control_snapshot_redacts_authorization_and_reset_restores_baseline() -> None:
    api_credential = "synthetic-api-credential-canary"
    expected_hash = hmac.digest(CONTROL_NONCE.encode(), f"Bearer {api_credential}".encode(), hashlib.sha256).hex()
    headers = {CONTROL_HEADER: CONTROL_NONCE}
    async with _client() as client:
        await client.get(
            "/discord/api/users/@me",
            headers={"Authorization": f"Bearer {api_credential}"},
        )
        before = await client.get("/__fuzz/snapshot", headers=headers)
        reset = await client.post("/__fuzz/reset", headers=headers)
        after = await client.get("/__fuzz/snapshot", headers=headers)

    observation = before.json()["requests"][0]
    assert observation["authorization_scheme"] == "Bearer"
    assert observation["authorization_hash"] == expected_hash
    assert api_credential not in before.text
    assert reset.status_code == 204
    assert after.json() == {"requests": []}


async def test_control_routes_hide_from_callers_without_the_nonce() -> None:
    async with _client() as client:
        reset = await client.post("/__fuzz/reset")
        snapshot = await client.get("/__fuzz/snapshot")

    assert reset.status_code == 404
    assert snapshot.status_code == 404


def _client() -> httpx.AsyncClient:
    app = create_fake_upstream_app(CONTROL_NONCE)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fake.test")
