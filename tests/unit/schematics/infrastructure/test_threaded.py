"""In-process analyzer refusals.

These pin what the threaded analyzer will not do, which is the only reason it is safe to offer:
it never quietly gives less than the port promises.
"""

import pytest

from squid.config import SchematicConfig
from squid.schematics.application.commands import RenderRequest, SimulationRequest
from squid.schematics.domain.models import FingerprintPreset
from squid.schematics.errors import SchematicRenderUnavailableError, SchematicSupportUnavailableError
from squid.schematics.infrastructure.threaded import ThreadedSchematicAnalyzer


def analyzer() -> ThreadedSchematicAnalyzer:
    return ThreadedSchematicAnalyzer(SchematicConfig())


async def test_compare_refuses_a_deadline_it_cannot_enforce() -> None:
    with pytest.raises(SchematicSupportUnavailableError):
        await analyzer().compare(b"", b"", preset=FingerprintPreset.SHAPE, timeout_seconds=1.0)


async def test_render_and_simulate_refuse() -> None:
    with pytest.raises(SchematicRenderUnavailableError):
        await analyzer().render(b"", request=RenderRequest())
    with pytest.raises(SchematicSupportUnavailableError):
        await analyzer().simulate(b"", request=SimulationRequest())
