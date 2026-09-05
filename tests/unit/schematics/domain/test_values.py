"""Validated value contracts shared by schematic request boundaries."""

import math

import pytest

from squid.core.errors import ValidationError
from squid.schematics.application.commands import RenderRequest
from squid.schematics.domain.values import RgbaColor, VerifiedResourcePack


@pytest.mark.parametrize(
    "channels",
    [
        (-0.1, 0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 1.1),
        (math.nan, 0.0, 0.0, 0.0),
        (0.0, math.inf, 0.0, 0.0),
    ],
)
def test_rgba_channels_are_finite_and_normalized(channels: tuple[float, float, float, float]) -> None:
    with pytest.raises(ValidationError):
        RgbaColor(*channels)


@pytest.mark.parametrize("channels", [(), (0, 0, 0), (0, 0, 0, 0, 0)])
def test_rgba_wire_arrays_have_exactly_four_channels(channels: tuple[int, ...]) -> None:
    with pytest.raises(ValidationError, match="exactly four"):
        RgbaColor.from_channels(channels)


def test_render_recipe_keeps_rgba_channels_as_a_stable_tuple() -> None:
    request = RenderRequest(background=RgbaColor(0.1, 0.2, 0.3, 0.4))

    assert request.recipe_fields()[-1] == (0.1, 0.2, 0.3, 0.4)


def test_resource_pack_derives_and_verifies_its_digest() -> None:
    pack = VerifiedResourcePack.from_bytes(b"pack")

    assert pack.sha256 == "4862f447f2c7f272fa2f4aaf89dadb3b1ac09105bd5864f8d1a0c9452bb0a226"
    assert pack.media_type == "application/zip"
    with pytest.raises(ValueError, match="do not match"):
        VerifiedResourcePack(b"pack", "0" * 64)


def test_resource_pack_rejects_an_unexpected_media_type() -> None:
    with pytest.raises(ValueError, match="application/zip"):
        VerifiedResourcePack(b"pack", VerifiedResourcePack.from_bytes(b"pack").sha256, "application/octet-stream")  # type: ignore[arg-type]


def test_resource_pack_rejects_mutable_data() -> None:
    with pytest.raises(ValidationError, match="immutable bytes"):
        VerifiedResourcePack(bytearray(b"pack"), VerifiedResourcePack.from_bytes(b"pack").sha256)  # type: ignore[arg-type]
