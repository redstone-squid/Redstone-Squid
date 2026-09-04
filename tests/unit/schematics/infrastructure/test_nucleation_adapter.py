# pyright: reportPrivateUsage=false
"""Nucleation adapter contract tests."""

from typing import Any

import pytest
from pytest_mock import MockerFixture

pytest.importorskip("nucleation")

from squid.schematics.application.commands import RenderRequest
from squid.schematics.domain.models import SchematicFormat
from squid.schematics.domain.values import RgbaColor
from squid.schematics.errors import InvalidSchematicError
from squid.schematics.infrastructure import nucleation_adapter


class RecordingRenderConfig:
    """Record the native configuration calls without requiring a GPU render."""

    calls: list[tuple[str, tuple[object, ...]]] = []

    @classmethod
    def create(cls, width: int, height: int) -> RecordingRenderConfig:
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
        background=RgbaColor(0.1, 0.2, 0.3, 1.0),
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


def test_analysis_rejects_an_unresolved_source_format(mocker: MockerFixture) -> None:
    schematic = mocker.Mock()
    schematic.tight_dimensions.return_value = mocker.Mock(x=1, y=1, z=1)

    with pytest.raises(InvalidSchematicError, match="source format"):
        nucleation_adapter._metrics(schematic, b"not a recognised container", source_format=None)


def test_required_metadata_failures_are_not_hidden(mocker: MockerFixture) -> None:
    schematic = mocker.Mock()
    dimensions = mocker.Mock(x=1, y=1, z=1)
    schematic.tight_dimensions.return_value = dimensions
    schematic.dimensions.return_value = dimensions
    schematic.block_count.return_value = 1
    schematic.entity_count.return_value = 0
    schematic.palette_json.return_value = '["minecraft:air","minecraft:stone"]'
    schematic.region_names_json.return_value = '["Main"]'
    schematic.source_data_version.return_value = -1
    schematic.name.return_value = "demo"
    schematic.author.side_effect = RuntimeError("unexpected metadata failure")

    with pytest.raises(RuntimeError, match="unexpected metadata failure"):
        nucleation_adapter._metrics(schematic, b"data", source_format=SchematicFormat.LITEMATIC)


def test_optional_sign_engine_failure_drops_only_sign_evidence(mocker: MockerFixture) -> None:
    schematic = mocker.Mock()
    schematic.extract_signs_json.side_effect = Exception("NucleationError.NotFound")

    assert nucleation_adapter._signs(schematic) == ()


@pytest.mark.parametrize("payload", ["", "%%%", "abc", "YWJj==="])
def test_engine_base64_is_strictly_validated(payload: str) -> None:
    with pytest.raises(InvalidSchematicError, match=r"no test export|malformed base64"):
        nucleation_adapter._base64_bytes(payload, "test export")


@pytest.mark.parametrize(
    "payload",
    [None, {}, [{"version": "1.20", "kind": "block", "severity": "Unknown", "path": "x", "detail": "y"}]],
)
def test_conversion_loss_json_rejects_malformed_required_evidence(payload: object) -> None:
    with pytest.raises(InvalidSchematicError):
        nucleation_adapter._loss_entries(payload)
