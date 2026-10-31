"""Rendering for every branch of a Minecraft identity refresh.

`IdentityRefresh` reports the no-op case as well as the interesting ones, so the renderer has to
say something for all of them. The contested branch matters most: it used to be indistinguishable
from "nothing happened", which is exactly what left users unaware their new name was taken.
"""

from uuid import UUID

import pytest
from whenever import Instant

from squid.accounts.domain import (
    Account,
    AccountIdentity,
    AliasClaim,
    ClaimStatus,
    CreatorAlias,
    IdentityRefresh,
    LinkPreview,
)
from squid.bot.profile_render import present_claimant
from squid.bot.verify import (
    _link_conflict,
    _link_message,
    _reconciliation_lines,
    _refresh_message,
)
from squid.suggestions.infrastructure.providers.records import _claimant_description

JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_UUID = UUID("22222222-2222-2222-2222-222222222222")
NOW = Instant.from_utc(2026, 8, 16)


def _refresh(**overrides: object) -> IdentityRefresh:
    defaults: dict[str, object] = {
        "account_id": 1,
        "java_uuid": JAVA_UUID,
        "current_name": "NewName",
        "previous_name": "OldName",
    }
    return IdentityRefresh(**(defaults | overrides))  # type: ignore[arg-type]


def test_unchanged_name_says_nothing_changed() -> None:
    message = _refresh_message(_refresh(current_name="Steve", previous_name="Steve"))

    assert "still **Steve**" in message
    assert "changed from" not in message


def test_rename_names_both_the_old_and_new_name() -> None:
    message = _refresh_message(_refresh())

    assert "**OldName**" in message
    assert "**NewName**" in message


def test_a_claimed_credit_is_reported() -> None:
    message = _refresh_message(_refresh(claimed_alias=CreatorAlias(5, "NewName", account_id=1)))

    assert "Build credits under **NewName**" in message


def test_a_contested_name_says_it_was_not_moved_and_names_the_claim() -> None:
    message = _refresh_message(
        _refresh(
            current_name="Contested",
            contested_alias=CreatorAlias(9, "Contested", account_id=2),
            opened_claim=AliasClaim(3, 9, "Contested", 1, ClaimStatus.PENDING, NOW),
        ),
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
    )

    assert "**OldName**" in message
    assert "**OlderName**" in message


def test_a_link_names_the_account_it_linked() -> None:
    message = _link_message(_refresh(current_name="Notch", previous_name=None))

    assert "linked to **Notch**" in message
    # Not the refresh headline: a first link never "changed" or "stayed the same".
    assert "still" not in message
    assert "changed from" not in message


def test_a_link_reports_the_credit_it_claimed() -> None:
    message = _link_message(
        _refresh(current_name="Notch", previous_name=None, claimed_alias=CreatorAlias(5, "Notch", account_id=1)),
    )

    assert "Build credits under **Notch**" in message


def test_a_link_reports_a_contested_credit() -> None:
    """The regression this subplan exists for: linking used to report only the claimed alias.

    A user whose verified name belonged to someone else was told the link succeeded, and never that
    their credit had not moved or that a staff claim was now open.
    """
    message = _link_message(
        _refresh(
            current_name="Notch",
            previous_name=None,
            contested_alias=CreatorAlias(9, "Notch", account_id=2),
            opened_claim=AliasClaim(3, 9, "Notch", 1, ClaimStatus.PENDING, NOW),
        ),
    )

    assert "not moved" in message
    assert "#3" in message


def test_link_and_refresh_describe_a_credit_in_the_same_words() -> None:
    """One vocabulary for the reconciliation, which is the same operation in both commands."""
    refresh = _refresh(claimed_alias=CreatorAlias(5, "NewName", account_id=1), retained_alias_names=("OldName",))

    shared = _reconciliation_lines(refresh)

    assert shared
    assert all(line in _link_message(refresh) for line in shared)
    assert all(line in _refresh_message(refresh) for line in shared)


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


def _claim(claimant: Account | None) -> AliasClaim:
    return AliasClaim(7, 9, "Notch", 42, ClaimStatus.PENDING, NOW, claimant=claimant)


def test_a_claimant_with_discord_is_shown_as_a_mention() -> None:
    """The only handle a reviewer can click, and the only one Discord resolves for us."""
    claim = _claim(Account((AccountIdentity.discord(555),), None, 42, NOW))

    assert present_claimant(claim) == "<@555>"


def test_a_claimant_without_discord_falls_back_to_the_java_name() -> None:
    claim = _claim(Account((AccountIdentity.java(JAVA_UUID, username="Notch"),), None, 42, NOW))

    assert present_claimant(claim) == "Notch"


def test_a_claimant_with_only_a_public_creator_is_named_by_it() -> None:
    creator = UUID("33333333-3333-3333-3333-333333333333")
    claim = _claim(Account((), None, 42, NOW, creator))

    presented = present_claimant(claim)

    assert str(creator) in presented
    assert "42" not in presented


def test_the_internal_id_is_last_and_labelled_as_a_diagnostic() -> None:
    """It identifies a row rather than a person, so it must not read like a name."""
    presented = present_claimant(_claim(None))

    assert "42" in presented
    assert "unidentified" in presented


def test_the_autocomplete_prefers_a_readable_name_over_a_snowflake() -> None:
    """A mention renders as raw `<@id>` in an autocomplete row, so that surface needs its own rule."""
    java_only = _claim(Account((AccountIdentity.java(JAVA_UUID, username="Notch"),), None, 42, NOW))
    with_discord = _claim(
        Account((AccountIdentity.discord(555), AccountIdentity.java(JAVA_UUID, username="Notch")), None, 42, NOW)
    )

    assert _claimant_description(java_only) == "Notch"
    assert _claimant_description(with_discord) == "Notch"
    assert _claimant_description(_claim(None)) == "account 42"


def test_the_autocomplete_description_respects_discords_limit() -> None:
    long_name = "N" * 250
    claim = _claim(Account((AccountIdentity.java(JAVA_UUID, username=long_name),), None, 42, NOW))

    assert len(_claimant_description(claim)) <= 100


# The unlink decision tree that used to live here is gone with the command: unlinking is a
# picked row on the `/account` panel, and its cases are pinned in `test_account_panel.py`
# (docs/plans/command-redesign/07-account.md).


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
    assert _refresh_message(refresh).strip()
