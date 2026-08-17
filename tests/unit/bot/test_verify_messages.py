"""Rendering for every branch of a Minecraft identity refresh.

`IdentityRefresh` reports the no-op case as well as the interesting ones, so the renderer has to
say something for all of them. The contested branch matters most: it used to be indistinguishable
from "nothing happened", which is exactly what left users unaware their new name was taken.
"""

from uuid import UUID

import pytest
from whenever import Instant

from squid.accounts.domain import (
    AccountIdentity,
    AliasClaim,
    ClaimStatus,
    CreatorAlias,
    IdentityRefresh,
    LinkPreview,
)
from squid.bot.verify import _link_conflict, _refresh_message

JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_UUID = UUID("22222222-2222-2222-2222-222222222222")
NOW = Instant.from_utc(2026, 8, 16)
LOCALE = "en"


def _refresh(**overrides: object) -> IdentityRefresh:
    defaults: dict[str, object] = {
        "account_id": 1,
        "java_uuid": JAVA_UUID,
        "current_name": "NewName",
        "previous_name": "OldName",
    }
    return IdentityRefresh(**(defaults | overrides))  # type: ignore[arg-type]


def test_unchanged_name_says_nothing_changed() -> None:
    message = _refresh_message(_refresh(current_name="Steve", previous_name="Steve"), LOCALE)

    assert "still **Steve**" in message
    assert "changed from" not in message


def test_rename_names_both_the_old_and_new_name() -> None:
    message = _refresh_message(_refresh(), LOCALE)

    assert "**OldName**" in message
    assert "**NewName**" in message


def test_a_claimed_credit_is_reported() -> None:
    message = _refresh_message(_refresh(claimed_alias=CreatorAlias(5, "NewName", account_id=1)), LOCALE)

    assert "Build credits under **NewName**" in message


def test_a_contested_name_says_it_was_not_moved_and_names_the_claim() -> None:
    message = _refresh_message(
        _refresh(
            current_name="Contested",
            contested_alias=CreatorAlias(9, "Contested", account_id=2),
            opened_claim=AliasClaim(3, 9, "Contested", 1, ClaimStatus.PENDING, NOW),
        ),
        LOCALE,
    )

    assert "**Contested**" in message
    assert "not moved" in message
    assert "#3" in message


def test_retained_names_are_listed() -> None:
    message = _refresh_message(
        _refresh(
            claimed_alias=CreatorAlias(5, "NewName", account_id=1),
            retained_alias_names=("OldName", "OlderName"),
        ),
        LOCALE,
    )

    assert "**OldName**" in message
    assert "**OlderName**" in message


def _preview(*, held_elsewhere: bool = False) -> LinkPreview:
    return LinkPreview(java_uuid=JAVA_UUID, username="Notch", java_uuid_held_elsewhere=held_elsewhere)


def test_a_fresh_link_has_no_conflict() -> None:
    assert _link_conflict(_preview(), None) is None


def test_relinking_the_same_uuid_is_not_a_conflict() -> None:
    """It is how a renamed player refreshes their name, so it must not be refused."""
    existing = AccountIdentity.java(JAVA_UUID, username="OldName")

    assert _link_conflict(_preview(held_elsewhere=True), existing) is None


def test_holding_a_different_java_identity_conflicts() -> None:
    existing = AccountIdentity.java(OTHER_UUID, username="Other")

    assert _link_conflict(_preview(), existing) == OTHER_UUID


def test_a_uuid_linked_to_somebody_else_conflicts() -> None:
    """Detected before the prompt now; it used to surface only after consent was given."""
    assert _link_conflict(_preview(held_elsewhere=True), None) == JAVA_UUID


def test_a_uuid_linked_elsewhere_conflicts_even_with_another_identity_held() -> None:
    existing = AccountIdentity.java(OTHER_UUID, username="Other")

    # The caller's own mismatch is reported, because unlinking that is the action they must take.
    assert _link_conflict(_preview(held_elsewhere=True), existing) == OTHER_UUID


@pytest.mark.parametrize(
    "refresh",
    [
        pytest.param(_refresh(current_name="Steve", previous_name="Steve"), id="unchanged"),
        pytest.param(_refresh(), id="renamed-nothing-claimed"),
        pytest.param(_refresh(claimed_alias=CreatorAlias(5, "NewName", account_id=1)), id="renamed-claimed"),
        pytest.param(
            _refresh(
                contested_alias=CreatorAlias(9, "NewName", account_id=2),
                opened_claim=AliasClaim(3, 9, "NewName", 1, ClaimStatus.PENDING, NOW),
            ),
            id="contested",
        ),
        pytest.param(_refresh(retained_alias_names=("OldName",)), id="retained-only"),
    ],
)
def test_every_branch_renders_something(refresh: IdentityRefresh) -> None:
    """No combination may produce an empty message, which Discord rejects outright."""
    assert _refresh_message(refresh, LOCALE).strip()
