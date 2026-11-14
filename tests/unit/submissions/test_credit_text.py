"""Text-only credit editing preserves identity references and distinguishes namespaces."""

from squid.builds.domain.credits import Credit, CreditAttribution, CreditKind, CreditRole
from squid.submissions.application.credit_text import format_credit, parse_credit_lines


def test_explicit_provider_prefixes_and_roles() -> None:
    credits = parse_credit_lines("Alice\njava:Alice | layout\ndiscord:123 (Alice) | contributor\nbedrock:Alex")
    assert [credit.kind for credit in credits] == [
        CreditKind.NAME,
        CreditKind.MINECRAFT_JAVA,
        CreditKind.DISCORD,
        CreditKind.MINECRAFT_BEDROCK,
    ]
    assert credits[1].username == "Alice"
    assert (credits[2].name, credits[2].subject, credits[2].role) == ("Alice", "123", CreditRole.CONTRIBUTOR)


def test_unedited_credit_preserves_username_and_source_evidence() -> None:
    credit = Credit(
        name="Display name",
        kind=CreditKind.DISCORD,
        username="discord_name",
        subject="123",
        attribution=CreditAttribution.ASSUMED_SENDER,
        source_message_id=456,
    )
    assert parse_credit_lines(format_credit(credit), [credit]) == [credit]
