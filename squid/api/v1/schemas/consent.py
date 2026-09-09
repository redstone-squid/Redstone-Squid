"""The published privacy notice, as served to any client that has to display it."""

from pydantic import ConfigDict, Field

from squid.api.schema import ApiSchema


class PrivacyNoticeDetail(ApiSchema):
    """The current notice, its version, and the locale it was rendered in."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(description="Identifier of the published notice, sent back when recording consent.")
    locale: str = Field(
        description="Locale the body was rendered in, echoed so a client can tell it received a fallback rather than "
        "what it asked for."
    )
    title: str
    body: str = Field(
        description="Paragraphs separated by blank lines. Plain text, never markup: it is rendered into a Discord "
        "card, an HTML page and a terminal, and only one of those could parse anything else."
    )


class ConsentGrantRequest(ApiSchema):
    """Which notice version the client actually displayed before asking."""

    model_config = ConfigDict(extra="forbid")

    version: str | None = Field(
        default=None,
        description="Version of the notice that was displayed. Optional so an older client keeps working; supplied, "
        "it is checked against the published version, so a stale cached notice cannot record consent to text nobody "
        "read.",
    )
