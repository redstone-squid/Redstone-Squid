"""Run one bounded local API fuzz smoke campaign in a disposable Docker stack."""

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from tests.fuzz.api.docker_stack import docker_api_environment
from tests.fuzz.api.schemathesis import LOCAL_SMOKE, CampaignOutcome, CampaignState, run_campaign

_ROOT = Path(__file__).resolve().parents[1]
_CANONICAL_CONTRACT = _ROOT / "contracts" / "openapi.json"
_ARTIFACT_ROOT = _ROOT / ".fuzz" / "api"


def exit_code_for(state: CampaignState) -> int:
    """Map the exact terminal campaign state to a stable process exit code."""
    if state is CampaignState.PASS:
        return 0
    if state is CampaignState.PRODUCT_FINDING:
        return 1
    return 2


async def run_smoke(*, seed: int) -> CampaignOutcome:
    """Start one disposable stack and run the fixed 20-second smoke profile."""
    async with docker_api_environment() as running:
        return await run_campaign(
            running,
            artifact_root=_ARTIFACT_ROOT,
            canonical_path=_CANONICAL_CONTRACT,
            profile=LOCAL_SMOKE,
            seed=seed,
        )


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the local smoke profile and print its machine-readable outcome."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=0)
    parsed = parser.parse_args(arguments)
    if not 0 <= parsed.seed < 2**64:
        parser.error("--seed must be an unsigned 64-bit integer")
    outcome = asyncio.run(run_smoke(seed=parsed.seed))
    print(
        json.dumps(
            {
                "artifact_directory": str(outcome.paths.root.relative_to(_ROOT)),
                "forced_kill": outcome.forced_kill,
                "returncode": outcome.returncode,
                "state": outcome.state,
            },
            sort_keys=True,
        )
    )
    return exit_code_for(outcome.state)


if __name__ == "__main__":
    raise SystemExit(main())
