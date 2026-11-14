"""Typed build attribution, independently of account ownership and identity verification."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from squid.core.errors import JSONValue, ValidationError


class CreditKind(StrEnum):
    """The namespace of an observed creator reference."""

    NAME = "name"
    DISCORD = "discord"
    MINECRAFT_JAVA = "minecraft_java"
    MINECRAFT_BEDROCK = "minecraft_bedrock"


class CreditRole(StrEnum):
    """How the credited party contributed to the build."""

    CREATOR = "creator"
    DESIGNER = "designer"
    LAYOUT = "layout"
    CONTRIBUTOR = "contributor"


class CreditAttribution(StrEnum):
    """How the attribution was obtained."""

    EXPLICIT = "explicit"
    ASSUMED_SENDER = "assumed_sender"
    CONFIRMED = "confirmed"
    LEGACY = "legacy"


@dataclass(frozen=True, slots=True, kw_only=True)
class Credit:
    """A credited name or provider identity and the evidence for its build contribution.

    Names and usernames are snapshots, never proof that two references identify the
    same person. A provider subject is a Discord ID, Java UUID, or Bedrock XUID when
    known. Account ownership is resolved separately from these attribution claims.
    """

    name: str
    kind: CreditKind = CreditKind.NAME
    username: str | None = None
    subject: str | None = None
    role: CreditRole = CreditRole.CREATOR
    attribution: CreditAttribution = CreditAttribution.EXPLICIT
    source_message_id: int | None = None

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name or len(name) > 255:
            msg = "A credit needs a name of 1-255 characters."
            raise ValidationError(msg)
        object.__setattr__(self, "name", name)
        if self.username is not None:
            username = self.username.strip()
            if not username or len(username) > 255 or self.kind is CreditKind.NAME:
                msg = "A username requires a provider-specific credit."
                raise ValidationError(msg)
            object.__setattr__(self, "username", username)
        if self.subject is not None:
            if self.kind is CreditKind.NAME:
                msg = "A generic name cannot carry a provider subject."
                raise ValidationError(msg)
            if self.kind is not CreditKind.MINECRAFT_JAVA and (
                not self.subject.isascii() or not self.subject.isdecimal()
            ):
                msg = "The credit's provider subject is invalid."
                raise ValidationError(msg)
            try:
                subject = str(UUID(self.subject)) if self.kind is CreditKind.MINECRAFT_JAVA else str(int(self.subject))
                if self.kind is not CreditKind.MINECRAFT_JAVA and int(subject) <= 0:
                    msg = "The credit's provider subject is invalid."
                    raise ValidationError(msg)
            except ValueError as error:
                msg = "The credit's provider subject is invalid."
                raise ValidationError(msg) from error
            object.__setattr__(self, "subject", subject)
        if self.source_message_id is not None and (
            isinstance(self.source_message_id, bool) or self.source_message_id <= 0
        ):
            msg = "A credit source message ID must be positive."
            raise ValidationError(msg)
        if self.attribution is CreditAttribution.ASSUMED_SENDER and (
            self.kind is not CreditKind.DISCORD or self.subject is None or self.source_message_id is None
        ):
            msg = "Sender attribution requires a Discord ID and source message."
            raise ValidationError(msg)

    def to_json(self) -> dict[str, JSONValue]:
        return {
            "kind": self.kind.value,
            "name": self.name,
            "username": self.username,
            "subject": self.subject,
            "role": self.role.value,
            "attribution": self.attribution.value,
            "source_message_id": self.source_message_id,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, JSONValue]) -> Credit:
        """Decode a credit without accepting coercions of names or numeric identifiers."""
        name = value.get("name")
        username = value.get("username")
        subject = value.get("subject")
        message_id = value.get("source_message_id")
        if (
            not isinstance(name, str)
            or (username is not None and not isinstance(username, str))
            or (subject is not None and not isinstance(subject, str))
            or (message_id is not None and (not isinstance(message_id, int) or isinstance(message_id, bool)))
        ):
            msg = "Invalid credit values."
            raise ValidationError(msg)
        try:
            return cls(
                name=name,
                kind=CreditKind(value.get("kind", "name")),
                username=username,
                subject=subject,
                role=CreditRole(value.get("role", "creator")),
                attribution=CreditAttribution(value.get("attribution", "explicit")),
                source_message_id=message_id,
            )
        except (TypeError, ValueError) as error:
            msg = "Invalid credit kind, role, or attribution."
            raise ValidationError(msg) from error


def name_credits(names: Iterable[str], *, legacy: bool = False) -> list[Credit]:
    """Preserve untyped names without guessing a provider or merging identities."""
    attribution = CreditAttribution.LEGACY if legacy else CreditAttribution.EXPLICIT
    return [Credit(name=name, attribution=attribution) for name in dict.fromkeys(names) if name.strip()]


def creator_names(credits: Iterable[Credit]) -> list[str]:
    """The compatibility display of primary creator and designer credits."""
    return list(
        dict.fromkeys(credit.name for credit in credits if credit.role in {CreditRole.CREATOR, CreditRole.DESIGNER})
    )


class StagedCredits:
    """Expose the string-only creator view for older adapters."""

    credits: list[Credit]

    @property
    def creators_ign(self) -> list[str]:
        return creator_names(self.credits)

    @creators_ign.setter
    def creators_ign(self, names: Iterable[str]) -> None:
        self.credits = name_credits(names, legacy=True)
