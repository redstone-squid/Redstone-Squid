"""One non-fuzzing lifecycle check for the complete disposable API stack."""

import asyncio

import docker
import httpx
import pytest

from tests.fuzz.api.docker_stack import DockerRunningApi, docker_api_environment
from tests.fuzz.api.fake_upstreams import MINECRAFT_ALICE


@pytest.mark.docker
async def test_disposable_api_stack_resets_every_mutable_store_and_cleans_up() -> None:
    """Exercise real API, PostgreSQL, Redis, fake reset, and exact resource cleanup once."""
    environment = docker_api_environment()
    run_label = f"dev.redstone-squid.api-fuzz.run={environment.identity.run_id}"
    redis_sentinel = f"{environment.identity.redis_namespace}:sentinel".encode()

    try:
        async with environment as generic_running:
            assert isinstance(generic_running, DockerRunningApi)
            running = generic_running
            assert running.database_controller.observer_cannot_write()
            assert running.verification_code_count() == 0
            assert running.redis_keys() == {redis_sentinel}
            assert running.fake_snapshot().requests == []

            async with httpx.AsyncClient(
                base_url=running.base_url,
                timeout=5,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.post(
                    "/v1/verify",
                    headers={
                        "Authorization": running.secrets.api_secret,
                        "Idempotency-Key": "api-fuzz-lifecycle-1",
                    },
                    json={"uuid": MINECRAFT_ALICE},
                )
                assert response.status_code == 201

            assert running.verification_code_count() == 1
            assert len(running.redis_keys()) > 1
            assert [request.path for request in running.fake_snapshot().requests] == [
                f"/mojang/profile/{MINECRAFT_ALICE}"
            ]

            await running.reset()

            async with httpx.AsyncClient(timeout=2, follow_redirects=False, trust_env=False) as client:
                assert (await client.get(f"{running.base_url}/readyz")).status_code == 200
            assert running.verification_code_count() == 0
            assert running.redis_keys() == {redis_sentinel}
            assert running.fake_snapshot().requests == []
    finally:
        await asyncio.to_thread(_assert_no_run_resources, run_label)


def _assert_no_run_resources(run_label: str) -> None:
    client = docker.from_env()
    try:
        assert client.containers.list(all=True, filters={"label": run_label}) == []
        assert client.networks.list(filters={"label": run_label}) == []
        assert client.volumes.list(filters={"label": run_label}) == []
    finally:
        client.close()
