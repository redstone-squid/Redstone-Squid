# pyright: reportPrivateUsage=false
"""Nucleation adapter contract tests."""

from typing import Any

import pytest

pytest.importorskip("nucleation")

from squid.schematics.application.commands import RenderRequest
from squid.schematics.infrastructure import nucleation_adapter


class RecordingRenderConfig:
    """Record the native configuration calls without requiring a GPU render."""

    calls: list[tuple[str, tuple[object, ...]]] = []

    @classmethod
    def create(cls, width: int, height: int) -> "RecordingRenderConfig":
        cls.calls.append(("create", (width, height)))
        return cls()

    def set_isometric(self) -> None:
        self.calls.append(("set_isometric", ()))

    def set_orthographic(self, orthographic: bool) -> None:
        self.calls.append(("set_orthographic", (orthographic,)))

    def set_sphere_fit(self, sphere_fit: bool) -> None:
        self.calls.append(("set_sphere_fit", (sphere_fit,)))

    def set_yaw(self, yaw: float) -> None:
        self.calls.append(("set_yaw", (yaw,)))

    def set_pitch(self, pitch: float) -> None:
        self.calls.append(("set_pitch", (pitch,)))

    def set_zoom(self, zoom: float) -> None:
        self.calls.append(("set_zoom", (zoom,)))

    def set_background(self, red: float, green: float, blue: float, alpha: float) -> None:
        self.calls.append(("set_background", (red, green, blue, alpha)))


def test_render_config_matches_the_pinned_native_api() -> None:
    """The native object is enough to catch constructor and method signature drift."""
    config = nucleation_adapter._render_config(RenderRequest())

    assert config is not None


def test_render_config_applies_defaults_in_reset_safe_order(monkeypatch: pytest.MonkeyPatch) -> None:
    RecordingRenderConfig.calls = []
    monkeypatch.setattr(nucleation_adapter.nucleation, "RenderConfig", RecordingRenderConfig)

    nucleation_adapter._render_config(RenderRequest(width=640, height=480))

    assert RecordingRenderConfig.calls == [
        ("create", (640, 480)),
        ("set_isometric", ()),
        ("set_orthographic", (True,)),
        ("set_sphere_fit", (True,)),
        ("set_background", (0.0, 0.0, 0.0, 0.0)),
    ]


def test_render_config_applies_projection_and_camera_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    RecordingRenderConfig.calls = []
    monkeypatch.setattr(nucleation_adapter.nucleation, "RenderConfig", RecordingRenderConfig)
    request = RenderRequest(
        projection="perspective",
        sphere_fit=False,
        yaw=90.0,
        pitch=20.0,
        zoom=1.5,
        background=(0.1, 0.2, 0.3, 1.0),
    )

    config: Any = nucleation_adapter._render_config(request)

    assert isinstance(config, RecordingRenderConfig)
    assert RecordingRenderConfig.calls == [
        ("create", (768, 768)),
        ("set_isometric", ()),
        ("set_orthographic", (False,)),
        ("set_sphere_fit", (False,)),
        ("set_yaw", (90.0,)),
        ("set_pitch", (20.0,)),
        ("set_zoom", (1.5,)),
        ("set_background", (0.1, 0.2, 0.3, 1.0)),
    ]
