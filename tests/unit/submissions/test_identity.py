"""Draft and run IDs are deterministic for retries and still sort by creation time."""

from uuid import NAMESPACE_URL, uuid5, uuid7

from squid.core.submissions.application.inference_runs import candidate_id
from squid.core.submissions.domain.identity import DISCORD_EPOCH_MS, discord_uuid7, stable_uuid7


def test_a_stable_uuid7_is_a_valid_v7_carrying_the_timestamp() -> None:
    value = stable_uuid7(1_795_632_961_813, "name")

    assert value.version == 7
    assert value.variant == "specified in RFC 4122"
    assert value.time == 1_795_632_961_813
    assert value == stable_uuid7(1_795_632_961_813, "name")
    assert value != stable_uuid7(1_795_632_961_813, "other")


def test_discord_ids_sort_by_the_snowflake_time() -> None:
    earlier, later = (1_000 << 22), (2_000 << 22)

    assert discord_uuid7(earlier, "b") < discord_uuid7(later, "a")
    assert discord_uuid7(earlier, "a").time == DISCORD_EPOCH_MS + 1_000


def test_candidates_share_their_run_time_but_old_runs_keep_their_ids() -> None:
    run = uuid7()
    legacy = uuid5(NAMESPACE_URL, "discord-inference:1")

    assert candidate_id(run, 0).time == run.time
    assert candidate_id(run, 0) != candidate_id(run, 1)
    assert candidate_id(legacy, 0) == uuid5(legacy, "candidate:0")
