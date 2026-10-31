"""Exercise native render configuration without requiring a GPU adapter."""

import base64
import io
import zipfile

import nucleation
import pytest

from squid.schematics.application.commands import RenderRequest
from squid.schematics.infrastructure import nucleation_adapter

pytestmark = pytest.mark.schematic


def test_render_reaches_the_renderer_with_native_configuration(
    monkeypatch: pytest.MonkeyPatch, piston_door: bytes
) -> None:
    """Catch binding changes before invoking the GPU-dependent renderer."""
    output = b"rendered-image"
    received: list[nucleation.RenderConfig] = []

    def render_png(
        schematic: nucleation.Schematic, pack: nucleation.ResourcePack, config: nucleation.RenderConfig
    ) -> str:
        assert schematic.block_count() > 0
        assert isinstance(pack, nucleation.ResourcePack)
        assert isinstance(config, nucleation.RenderConfig)
        received.append(config)
        return base64.b64encode(output).decode()

    monkeypatch.setattr(nucleation.Renderer, "render_png_b64_with_pack", render_png)
    with io.BytesIO() as buffer:
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("pack.mcmeta", '{"pack":{"pack_format":15,"description":"render adapter test"}}')
        pack_bytes = buffer.getvalue()

    actual = nucleation_adapter.render(
        piston_door,
        request=RenderRequest(width=320, height=240),
        resource_pack=pack_bytes,
    )

    assert actual == output
    assert len(received) == 1
