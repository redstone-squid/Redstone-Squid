"""Safe, immutable public sponsor attribution values."""

from dataclasses import dataclass
from uuid import UUID

from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

_HTTP_URL = TypeAdapter(AnyHttpUrl)


@dataclass(frozen=True, slots=True)
class PublicSponsor:
    """Allowlisted public snapshot of one sponsoring Paper installation."""

    installation_id: UUID
    display_name: str | None = None
    address: str | None = None
    description: str | None = None
    website_url: str | None = None

    def __post_init__(self) -> None:
        if self.installation_id.int == 0:
            msg = "A public sponsor requires a non-nil installation ID."
            raise ValueError(msg)
        values = (
            ("display_name", "display name", self.display_name, 80),
            ("address", "address", self.address, 255),
            ("description", "description", self.description, 500),
            ("website_url", "website URL", self.website_url, 2048),
        )
        for attribute, label, value, maximum in values:
            if value is None:
                continue
            normalized = value.strip()
            if (
                not normalized
                or normalized != value
                or len(normalized) > maximum
                or (
                    any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in normalized)
                    and attribute == "website_url"
                )
            ):
                msg = f"Sponsor {label} must contain 1 to {maximum} characters."
                raise ValueError(msg)
            object.__setattr__(self, attribute, normalized)
        if self.website_url is not None:
            try:
                validated = _HTTP_URL.validate_python(self.website_url)
            except ValidationError as error:
                msg = "Sponsor website URL must be a valid HTTP(S) URL."
                raise ValueError(msg) from error
            if validated.username is not None or validated.password is not None:
                msg = "Sponsor website URL must be an HTTP(S) URL without embedded credentials."
                raise ValueError(msg)
            object.__setattr__(self, "website_url", str(validated))
