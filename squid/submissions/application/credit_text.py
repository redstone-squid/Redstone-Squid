"""Compact credit input for text-only clients; unchanged entries retain their evidence."""

from collections.abc import Sequence

from squid.builds.domain.credits import Credit, CreditKind, CreditRole
from squid.core.errors import ValidationError

_PREFIXES = {"discord": CreditKind.DISCORD, "java": CreditKind.MINECRAFT_JAVA, "bedrock": CreditKind.MINECRAFT_BEDROCK}


def format_credit(credit: Credit) -> str:
    prefix = next((key for key, kind in _PREFIXES.items() if kind is credit.kind), None)
    reference = credit.name
    if prefix is not None:
        identifier = credit.subject or credit.username or credit.name
        reference = f"{prefix}:{identifier}"
        if identifier != credit.name:
            reference += f" ({credit.name})"
    return reference if credit.role is CreditRole.CREATOR else f"{reference} | {credit.role.value}"


def parse_credit_lines(text: str, previous: Sequence[Credit] = ()) -> list[Credit]:
    """Accept names, discord:IDs/usernames, java:IGNs, bedrock:gamertags and optional '| role'."""
    retained = {format_credit(credit): credit for credit in previous}
    credits: list[Credit] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line in retained:
            credits.append(retained[line])
            continue
        reference, separator, role_text = line.rpartition(" | ")
        if not separator:
            reference, role_text = line, "creator"
        try:
            role = CreditRole(role_text)
        except ValueError as error:
            message = "Choose creator, designer, layout, or contributor after ' | '."
            raise ValidationError(message) from error
        prefix, colon, identifier = reference.partition(":")
        kind = _PREFIXES.get(prefix) if colon else None
        if kind is None:
            credits.append(Credit(name=reference, role=role))
            continue
        identifier, label_separator, label = identifier.partition(" (")
        name = label.removesuffix(")") if label_separator else identifier
        is_subject = (
            identifier.isascii() and identifier.isdecimal()
            if kind is not CreditKind.MINECRAFT_JAVA
            else "-" in identifier
        )
        credits.append(
            Credit(
                name=name,
                kind=kind,
                role=role,
                subject=identifier if is_subject else None,
                username=None if is_subject else identifier.removeprefix("@"),
            )
        )
    return credits
