"""Conflict context on `AliasAlreadyClaimedError`.

Review thread 3766207128 asked for "context about which account claimed it". A creator profile is
public data — `GET /v1/creators/{creator_id}` serves it unauthenticated — so the public half names
the creator, while the internal account ID stays in the log-only half.
"""

from uuid import UUID

import pytest

from squid.accounts.errors import AliasAlreadyClaimedError
from squid.api.errors import ProblemDetail, _status_for_error
from squid.bot.errors import build_error_notice

PUBLIC_CREATOR = UUID("33333333-3333-3333-3333-333333333333")
HOLDER_ACCOUNT_ID = 4242
DISCORD_ID = 9876543210


def test_a_bare_conflict_still_names_the_alias() -> None:
    error = AliasAlreadyClaimedError("Notch")

    assert error.public_context == {"name": "Notch"}
    assert error.holder_public_creator_id is None


def test_the_public_half_carries_the_creator_and_the_private_half_the_account() -> None:
    error = AliasAlreadyClaimedError(
        "Notch",
        holder_public_creator_id=PUBLIC_CREATOR,
        holder_account_id=HOLDER_ACCOUNT_ID,
    )

    assert error.public_context == {"name": "Notch", "public_creator_id": str(PUBLIC_CREATOR)}
    assert error.context["holder_account_id"] == HOLDER_ACCOUNT_ID


def test_the_internal_account_id_never_reaches_a_public_surface() -> None:
    """The whole point of the split, asserted through both renderers rather than by inspection."""
    error = AliasAlreadyClaimedError(
        "Notch",
        holder_public_creator_id=PUBLIC_CREATOR,
        holder_account_id=HOLDER_ACCOUNT_ID,
    ).with_holder_name("Notch")

    bot_text = build_error_notice(error, "en").detail
    problem = ProblemDetail(
        title=error.localized_title("en"),
        status=_status_for_error(error),
        detail=error.localized_public_detail("en"),
        code=error.code,
        resource=error.resource,
        context=error.public_context or None,
    )
    rendered = f"{bot_text} {problem.model_dump_json()}"

    assert str(HOLDER_ACCOUNT_ID) not in rendered
    assert "holder_account_id" not in rendered
    assert str(DISCORD_ID) not in rendered
    # The safe identifiers are present, so this is not passing by rendering nothing at all.
    assert str(PUBLIC_CREATOR) in rendered
    assert "Notch" in rendered


def test_naming_the_holder_rewrites_the_user_facing_message() -> None:
    error = AliasAlreadyClaimedError("Notch", holder_public_creator_id=PUBLIC_CREATOR)

    before = error.localized_public_detail("en")
    named = error.with_holder_name("Herobrine").localized_public_detail("en")

    assert "another account" in before
    assert "Herobrine" in named
    assert "Notch" in named


def test_the_conflict_maps_to_409() -> None:
    assert _status_for_error(AliasAlreadyClaimedError("Notch")) == 409


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (None, "Ask staff to review"),
        ("Approve with `reassign: True` to move the name to the claimant.", "reassign"),
    ],
)
def test_each_raise_site_can_give_its_own_next_action(action: str | None, expected: str) -> None:
    """A user is told to ask staff; a reviewer is told about the flag that already exists."""
    error = AliasAlreadyClaimedError("Notch", end_user_action=action)

    assert expected in error.localized_public_detail("en")
