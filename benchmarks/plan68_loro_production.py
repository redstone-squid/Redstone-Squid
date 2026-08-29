"""Measure the production Loro adapter against versioned representative fixtures.

Run with ``uv run --package squid-replication --extra loro python benchmarks/plan68_loro_production.py``.
The fixture ceilings are regression tripwires, not latency objectives; they intentionally leave substantial
headroom for slower CI hosts.
"""

import argparse
import json
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from squid_reactivity import ActionCommit, ConflictDetail, on_action_commit, transaction
from squid_replication import LoroBackend, Replica, ReplicationChangeToken

_FIXTURE = Path(__file__).with_name("fixtures") / "loro_document_v1.json"


def _elapsed_ms(callback) -> tuple[Any, float]:
    started = time.perf_counter_ns()
    result = callback()
    return result, (time.perf_counter_ns() - started) / 1_000_000


def measure(profile_name: str) -> dict[str, float | int | str]:
    """Build and measure one named fixture profile."""
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    profile: Mapping[str, Any] = fixture["profiles"][profile_name]
    document = Replica("benchmark", backend=LoroBackend(peer_id=1)).open(f"fixture-{profile_name}")
    commits: list[ActionCommit] = []
    movable_ids = [
        uuid.uuid5(uuid.NAMESPACE_URL, f"{profile_name}:part:{index}") for index in range(profile["movable_items"])
    ]
    tree_ids = [
        uuid.uuid5(uuid.NAMESPACE_URL, f"{profile_name}:node:{index}") for index in range(profile["tree_nodes"])
    ]

    def commit() -> None:
        with transaction():
            on_action_commit(lambda outcome, continuation: commits.append(outcome))
            document.text("description").insert(0, "redstone " * (profile["text_bytes"] // 9))
            document.counter("votes").increment(2**53 + 17)
            document.set("tags").add("benchmark")
            for index in range(profile["list_items"]):
                document.list("steps").insert(index, {"index": index, "ticks": [index % 4, index % 7]})
            for index, item_id in enumerate(movable_ids):
                document.movable_list("parts").insert(
                    index,
                    {"kind": "component", "position": [index, index % 16, index // 16]},
                    item_id=item_id,
                )
            for index in range(profile["map_entries"]):
                document.map("reviewers").set(f"reviewer-{index}", {"approved": index % 3 == 0, "round": index})
            for index, node_id in enumerate(tree_ids):
                document.tree("outline").create(node_id=node_id, metadata={"label": f"section-{index}"})

    _, commit_ms = _elapsed_ms(commit)
    _, snapshot_ms = _elapsed_ms(document.snapshot)
    checkpoint, export_ms = _elapsed_ms(document.checkpoint)
    restored = Replica("restore", backend=LoroBackend(peer_id=2)).open(f"fixture-{profile_name}")
    _, import_ms = _elapsed_ms(lambda: restored.import_update(checkpoint))
    token = commits[0].participant_changes[0].token
    assert isinstance(token, ReplicationChangeToken)
    inverse, plan_inverse_ms = _elapsed_ms(token.plan_inverse)
    assert not isinstance(inverse, ConflictDetail)
    return {
        "profile": profile_name,
        "commit_ms": commit_ms,
        "snapshot_ms": snapshot_ms,
        "export_ms": export_ms,
        "import_ms": import_ms,
        "plan_inverse_ms": plan_inverse_ms,
        "update_bytes": len(checkpoint),
    }


def assert_ceilings(result: Mapping[str, float | int | str]) -> None:
    """Raise when a result crosses its fixture's generous hard ceiling."""
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    profile = fixture["profiles"][result["profile"]]
    for metric, ceiling in profile["ceilings_ms"].items():
        actual = result[f"{metric}_ms"]
        if actual > ceiling:
            message = f"{result['profile']} {metric} took {actual:.1f} ms, above its {ceiling} ms ceiling"
            raise RuntimeError(message)
    if result["update_bytes"] > profile["ceiling_update_bytes"]:
        message = f"{result['profile']} update exceeded its encoded-byte ceiling"
        raise RuntimeError(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("profiles", nargs="*", choices=("p50", "p95", "p99"), default=("p50", "p95", "p99"))
    args = parser.parse_args()
    results = [measure(profile) for profile in args.profiles]
    for result in results:
        assert_ceilings(result)
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
