"""Submission render-artifact validation tests."""

import pytest

from squid.bot.submission.submit import _validated_artifact_url


def test_artifact_url_is_trimmed() -> None:
    assert _validated_artifact_url(" https://files.example/render.png\n") == "https://files.example/render.png"


@pytest.mark.parametrize("response", ["", "File upload failed", "javascript:alert(1)"])
def test_artifact_host_error_is_not_persisted_as_a_url(response: str) -> None:
    with pytest.raises(ValueError, match="invalid render URL"):
        _validated_artifact_url(response)
