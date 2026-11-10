"""Runtime policy validation at the settings boundary."""

import pytest
from pydantic import ValidationError

from squid.settings.domain.runtime import RuntimeSettings


@pytest.mark.parametrize(
    "changes",
    [
        {"welcome_forward_chance": -0.1},
        {"welcome_forward_chance": 1.1},
        {"welcome_forward_chance": float("nan")},
        {"build_vote_pass_threshold": 0},
        {"build_vote_fail_threshold": 1},
        {"delete_vote_pass_threshold": 32768},
        {"delete_vote_fail_threshold": -32769},
        {"notification_retention_days": 0},
        {"notification_retention_days": 3651},
        {"welcome_channel_id": 0},
        {"build_log_channel_ids": [-1]},
        {"unknown_setting": True},
    ],
)
def test_invalid_runtime_settings_are_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RuntimeSettings.model_validate(changes)
