"""Stable, transport-neutral API capability identifiers and compatibility bounds."""

from typing import Final, Literal

type RendererControl = Literal["text", "number", "choice", "multi_choice", "duration", "boolean"]

API_VERSION: Final = "1.0.0"

API_FEATURES: Final = frozenset(
    {
        "cli-device-auth",
        "submission-drafts",
        "submission-finalization",
        "submission-forms",
        "submission-media",
    }
)

RENDERER_CAPABILITIES: Final = ("repeatable_text",)
RENDERER_CONTROLS: Final[tuple[RendererControl, ...]] = (
    "text",
    "number",
    "choice",
    "multi_choice",
    "duration",
    "boolean",
)
