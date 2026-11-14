"""Attribution retains provider boundaries, source evidence, and observed names."""

import pytest

from squid.builds.domain.credits import Credit, CreditAttribution, CreditKind, CreditRole, creator_names, name_credits
from squid.core.errors import ValidationError


def test_same_name_keeps_distinct_provider_references() -> None:
    generic = Credit(name="Alex")
    discord = Credit(name="Alex", kind=CreditKind.DISCORD, subject="123", username="alex.chat")
    minecraft = Credit(name="Alex", kind=CreditKind.MINECRAFT_JAVA, username="Alex")

    assert len({generic, discord, minecraft}) == 3
    assert all(Credit.from_json(item.to_json()) == item for item in (generic, discord, minecraft))


def test_names_remain_untyped_and_preserve_spelling_and_roles() -> None:
    credits = name_credits(["_Reds_XD", "Tưởng", "_Reds_XD"], legacy=True)
    credits.append(Credit(name="Alex", role=CreditRole.LAYOUT))

    assert creator_names(credits) == ["_Reds_XD", "Tưởng"]
    assert credits[0].attribution is CreditAttribution.LEGACY
    assert credits[0].kind is CreditKind.NAME


def test_sender_default_preserves_discord_subject_and_source() -> None:
    credit = Credit(
        name="A nickname",
        username="some.user",
        kind=CreditKind.DISCORD,
        subject="123",
        attribution=CreditAttribution.ASSUMED_SENDER,
        source_message_id=456,
    )

    assert Credit.from_json(credit.to_json()) == credit
    assert credit.username == "some.user"


@pytest.mark.parametrize("subject", ["0", "-1", "someone", "1.5"])
def test_invalid_discord_subjects_are_rejected(subject: str) -> None:
    with pytest.raises(ValidationError):
        Credit(name="Alex", kind=CreditKind.DISCORD, subject=subject)


def test_generic_label_cannot_be_promoted_to_a_verified_provider_identity() -> None:
    with pytest.raises(ValidationError):
        Credit(name="Alex", subject="123")
    with pytest.raises(ValidationError):
        Credit(name="Alex", username="alex")
    with pytest.raises(ValidationError):
        Credit(name="Alex", attribution=CreditAttribution.ASSUMED_SENDER)
