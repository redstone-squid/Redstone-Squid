"""Render Minecraft identity refresh results, including unchanged and contested names.

A successful identity link must still explain any credit that could not be transferred.
"""

from uuid import UUID

import pytest
from whenever import Instant

from squid.accounts.domain import ClaimStatus, IdentityRefresh, LinkPreview
from squid.accounts.models import Account, AccountIdentity, AliasClaim, CreatorAlias
from squid.bot.account_presentation import (
    link_conflict,
    link_message,
    reconciliation_lines,
    refresh_message,
)
from squid.bot.profile_render import present_claimant
from squid.suggestions.domain import SuggestionRequest
from squid.suggestions.infrastructure.providers.records import AliasClaimProvider, _claimant_description
from squid_ui.text import source_text

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
    message = refresh_message(_refresh(current_name="Steve", previous_name="Steve"))

    assert "still **Steve**" in source_text(message)
    assert "changed from" not in source_text(message)


def test_rename_names_both_the_old_and_new_name() -> None:
    message = refresh_message(_refresh())

    assert "**OldName**" in source_text(message)
    assert "**NewName**" in source_text(message)


def test_a_claimed_credit_is_reported() -> None:
    message = refresh_message(_refresh(claimed_alias=CreatorAlias(id=5, name="NewName", account_id=1)))

    assert "Build credits under **NewName**" in source_text(message)


def test_a_contested_name_says_it_was_not_moved_and_names_the_claim() -> None:
    message = refresh_message(
        _refresh(
            current_name="Contested",
            contested_alias=CreatorAlias(id=9, name="Contested", account_id=2),
            opened_claim=AliasClaim(
                id=3, alias_id=9, alias_name="Contested", account_id=1, status=ClaimStatus.PENDING, created_at=NOW
            ),
        ),
    )

    assert "**Contested**" in source_text(message)
    assert "not moved" in source_text(message)
    assert "#3" in source_text(message)


def test_retained_names_are_listed() -> None:
    message = refresh_message(
        _refresh(
            claimed_alias=CreatorAlias(id=5, name="NewName", account_id=1),
            retained_alias_names=("OldName", "OlderName"),
        ),
    )

    assert "**OldName**" in source_text(message)
    assert "**OlderName**" in source_text(message)


def test_a_link_names_the_account_it_linked() -> None:
    message = link_message(_refresh(current_name="Notch", previous_name=None))

    assert "linked to **Notch**" in source_text(message)
    # Not the refresh headline: a first link never "changed" or "stayed the same".
    assert "still" not in source_text(message)
    assert "changed from" not in source_text(message)


def test_a_link_reports_the_credit_it_claimed() -> None:
    message = link_message(
        _refresh(
            current_name="Notch", previous_name=None, claimed_alias=CreatorAlias(id=5, name="Notch", account_id=1)
        ),
    )

    assert "Build credits under **Notch**" in source_text(message)


def test_a_link_reports_a_contested_credit() -> None:
    """A successful link must disclose the unmoved credit and the open staff claim."""
    message = link_message(
        _refresh(
            current_name="Notch",
            previous_name=None,
            contested_alias=CreatorAlias(id=9, name="Notch", account_id=2),
            opened_claim=AliasClaim(
                id=3, alias_id=9, alias_name="Notch", account_id=1, status=ClaimStatus.PENDING, created_at=NOW
            ),
        ),
    )

    assert "not moved" in source_text(message)
    assert "#3" in source_text(message)


def test_link_and_refresh_describe_a_credit_in_the_same_words() -> None:
    """One vocabulary for the reconciliation, which is the same operation in both commands."""
    refresh = _refresh(
        claimed_alias=CreatorAlias(id=5, name="NewName", account_id=1), retained_alias_names=("OldName",)
    )

    shared = reconciliation_lines(refresh)

    assert shared
    assert all(source_text(line) in source_text(link_message(refresh)) for line in shared)
    assert all(source_text(line) in source_text(refresh_message(refresh)) for line in shared)


def _preview(*, held_elsewhere: bool = False) -> LinkPreview:
    return LinkPreview(java_uuid=JAVA_UUID, username="Notch", java_uuid_held_elsewhere=held_elsewhere)


def test_a_fresh_link_has_no_conflict() -> None:
    assert link_conflict(_preview(), ()) is None


def test_relinking_the_same_uuid_is_not_a_conflict() -> None:
    """It is how a renamed player refreshes their name, so it must not be refused."""
    existing = AccountIdentity.java(JAVA_UUID, username="OldName")

    assert link_conflict(_preview(held_elsewhere=True), (existing,)) is None


def test_holding_a_different_java_identity_conflicts() -> None:
    existing = AccountIdentity.java(OTHER_UUID, username="Other")

    assert link_conflict(_preview(), (existing,)) == OTHER_UUID


def test_a_uuid_linked_to_somebody_else_conflicts() -> None:
    assert link_conflict(_preview(held_elsewhere=True), ()) == JAVA_UUID


def test_a_uuid_linked_elsewhere_conflicts_even_with_another_identity_held() -> None:
    existing = AccountIdentity.java(OTHER_UUID, username="Other")

    # The caller's own mismatch is reported, because unlinking that is the action they must take.
    assert link_conflict(_preview(held_elsewhere=True), (existing,)) == OTHER_UUID


def test_multiple_java_identities_report_the_first_persisted_mismatch() -> None:
    first = AccountIdentity.java(OTHER_UUID, username="Other")
    second_uuid = UUID("00000000-0000-4000-8000-000000000777")
    second = AccountIdentity.java(second_uuid, username="Second")

    assert link_conflict(_preview(), (first, second)) == OTHER_UUID


def _claim(claimant: Account | None) -> AliasClaim:
    return AliasClaim(
        id=7,
        alias_id=9,
        alias_name="Notch",
        account_id=42,
        status=ClaimStatus.PENDING,
        created_at=NOW,
        claimant=claimant,
    )


def test_a_claimant_with_discord_is_shown_as_a_mention() -> None:
    """A Discord mention gives the reviewer a clickable identity."""
    claim = _claim(Account(identities=(AccountIdentity.discord(555),), consent=None, id=42, created_at=NOW))

    assert present_claimant(claim) == "<@555>"


def test_a_claimant_without_discord_falls_back_to_the_java_name() -> None:
    claim = _claim(
        Account(identities=(AccountIdentity.java(JAVA_UUID, username="Notch"),), consent=None, id=42, created_at=NOW)
    )

    assert present_claimant(claim) == "Notch"


def test_a_claimant_with_only_a_public_creator_is_named_by_it() -> None:
    creator = UUID("33333333-3333-3333-3333-333333333333")
    claim = _claim(Account(identities=(), consent=None, id=42, created_at=NOW, public_creator_id=creator))

    presented = present_claimant(claim)

    assert str(creator) in presented
    assert "42" not in presented


def test_the_internal_id_is_last_and_labelled_as_a_diagnostic() -> None:
    """It identifies a row rather than a person, so it must not read like a name."""
    presented = present_claimant(_claim(None))

    assert "42" in presented
    assert "unidentified" in presented


def test_the_autocomplete_prefers_a_readable_name_over_a_snowflake() -> None:
    """The canonical presenter disables mentions where Discord would render their raw syntax."""
    java_only = _claim(
        Account(identities=(AccountIdentity.java(JAVA_UUID, username="Notch"),), consent=None, id=42, created_at=NOW)
    )
    with_discord = _claim(
        Account(
            identities=(AccountIdentity.discord(555), AccountIdentity.java(JAVA_UUID, username="Notch")),
            consent=None,
            id=42,
            created_at=NOW,
        )
    )

    assert _claimant_description(java_only) == "Notch"
    assert _claimant_description(with_discord) == "Notch"
    assert _claimant_description(_claim(None)) == present_claimant(_claim(None), mention=False)


def test_the_autocomplete_description_respects_discords_limit() -> None:
    long_name = "N" * 250
    claim = _claim(
        Account(identities=(AccountIdentity.java(JAVA_UUID, username=long_name),), consent=None, id=42, created_at=NOW)
    )

    assert len(_claimant_description(claim)) <= 100


async def test_registered_claim_provider_uses_the_canonical_presentation() -> None:
    claim = _claim(Account(identities=(AccountIdentity.discord(555),), consent=None, id=42, created_at=NOW))

    class Claims:
        async def pending_alias_claims(self, *, with_claimants: bool = False) -> tuple[AliasClaim, ...]:
            assert with_claimants
            return (claim,)

    (candidate,) = await AliasClaimProvider(Claims()).candidates(SuggestionRequest(source="alias_claims_pending"))

    assert candidate.suggestion.description == present_claimant(claim, mention=False)


# Account-panel unlink interactions are covered in ``test_account_panel.py``.


@pytest.mark.parametrize(
    "refresh",
    [
        pytest.param(_refresh(current_name="Steve", previous_name="Steve"), id="unchanged"),
        pytest.param(_refresh(), id="renamed-nothing-claimed"),
        pytest.param(_refresh(claimed_alias=CreatorAlias(id=5, name="NewName", account_id=1)), id="renamed-claimed"),
        pytest.param(
            _refresh(
                contested_alias=CreatorAlias(id=9, name="NewName", account_id=2),
                opened_claim=AliasClaim(
                    id=3, alias_id=9, alias_name="NewName", account_id=1, status=ClaimStatus.PENDING, created_at=NOW
                ),
            ),
            id="contested",
        ),
        pytest.param(_refresh(retained_alias_names=("OldName",)), id="retained-only"),
    ],
)
def test_every_branch_renders_something(refresh: IdentityRefresh) -> None:
    """No combination may produce an empty message, which Discord rejects outright."""
    assert source_text(refresh_message(refresh)).strip()
