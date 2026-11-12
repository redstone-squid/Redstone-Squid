"""Video recipes reject requests beyond the worker's finite render budget."""

import pytest
from pydantic import ValidationError

from squid.api.v1.schemas.schematics import VideoCreateRequest
from squid.schematics.application.video import VideoInteraction, VideoRenderRequest
from squid.schematics.infrastructure.wire import decode_video_request


@pytest.mark.parametrize(
    "overrides",
    [
        {"width": 641},
        {"height": 722},
        {"fps": 60},
        {"duration_ticks": 201},
        {"yaw": float("nan")},
        {"zoom": float("inf")},
        {"width": True},
        {"interactions": [{"tick": 120}]},
        {"interactions": [{"tick": 5}, {"tick": 4}]},
        {"interactions": [{"tick": 0}] * 17},
        {"interactions": [{"tick": 0, "position": [1.5, 0, 0]}]},
        {"interactions": [{"tick": 0, "position": [2**32, 0, 0]}]},
    ],
)
def test_invalid_video_recipes_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        decode_video_request(overrides)


def test_video_wire_round_trip_preserves_interaction_order() -> None:
    request = VideoRenderRequest(
        interactions=(
            VideoInteraction(tick=20, position=(1, 2, 3)),
            VideoInteraction(tick=20, position=(4, 5, 6)),
            VideoInteraction(tick=80),
        )
    )
    assert decode_video_request(request.model_dump(mode="json")) == request


def test_short_video_can_omit_activations() -> None:
    request = VideoRenderRequest(duration_ticks=1, interactions=(), orbit_degrees=0)
    assert request.interactions == ()


def test_public_recipe_rejects_out_of_range_interactions() -> None:
    with pytest.raises(ValidationError):
        VideoCreateRequest.model_validate({"duration_ticks": 5})


def test_public_static_orbit_does_not_implicitly_activate_an_input() -> None:
    recipe = VideoCreateRequest.model_validate({"simulation": {"enabled": False}}).to_request()
    assert not recipe.simulation_enabled
    assert recipe.interactions == ()
    assert recipe.orbit_degrees == 180


def test_public_fixed_camera_preserves_simulation() -> None:
    recipe = VideoCreateRequest.model_validate({"camera": {"mode": "fixed"}}).to_request()
    assert recipe.simulation_enabled
    assert recipe.orbit_degrees == 0
