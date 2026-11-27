"""Account application service tests."""

from dataclasses import replace
from uuid import UUID

import pytest

from squid.core.accounts.application.ports import VerificationLinkResult
from squid.core.accounts.application.services import VERIFICATION_MAX_CONSECUTIVE_FAILURES
from squid.core.accounts.domain import (
    MERGE_PROOF_MAX_AGE_SECONDS,
    MERGE_TICKET_TTL_SECONDS,
    PRIVACY_NOTICE_VERSION,
    AccountMerge,
    ClaimMethod,
    ClaimStatus,
    CreditPreview,
    IdentityProvider,
    IdentityRefresh,
    LinkPreview,
    PrivacyNoticeReceipt,
    ProfileUpdate,
    RecentAccountProof,
)
from squid.core.accounts.errors import (
    AccountAlreadyLinkedError,
    AccountNotFoundError,
    ClaimNotFoundError,
    InvalidMergeCodeError,
    InvalidMergeProofError,
    InvalidVerificationCodeError,
    LinkReservationExpiredError,
    MinecraftAccountNotFoundError,
    NoLinkedMinecraftAccountError,
    VerificationAttemptsExhaustedError,
)
from squid.core.accounts.models import (
    Account,
    AccountIdentity,
    CreatorAlias,
)
from squid.core.actors import ActingContext, AnonymousActor, ServiceActor, UserActor
from squid.core.permissions.errors import PermissionDeniedError
from tests.support.accounts import NOW, FakeAccountRepository, InMemoryAccountService

JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_JAVA_UUID = UUID("22222222-2222-2222-2222-222222222222")
CONSENT = PrivacyNoticeReceipt(PRIVACY_NOTICE_VERSION, NOW)
ATTEMPT = (IdentityProvider.DISCORD, "123")


def test_identity_factories_use_canonical_provider_subjects() -> None:
    assert AccountIdentity.discord(123).subject == "123"
    assert AccountIdentity.java(JAVA_UUID, username="Player").subject == str(JAVA_UUID)
    assert AccountIdentity.bedrock(456, gamertag="Builder").subject == "456"
    with pytest.raises(ValueError, match="unsigned 64-bit"):
        AccountIdentity.bedrock(0)


async def test_discord_login_creates_an_account_without_making_discord_primary() -> None:
    repository = FakeAccountRepository()

    account = await InMemoryAccountService(repository).get_or_create_identity(IdentityProvider.DISCORD, "123")

    (identity,) = account.identities_for(IdentityProvider.DISCORD)
    assert (identity.id, identity.subject, identity.verified_at) == (1, "123", NOW)
    assert account.identities_for(IdentityProvider.JAVA) == ()


async def test_indexing_is_independent_of_old_receipts_and_shared_across_settings() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    service = InMemoryAccountService(repository)
    assert not account.discord_build_indexing_enabled
    await service.set_discord_build_indexing(enabled=True, context=ActingContext.direct(UserActor(discord_user_id=123)))
    assert repository.accounts[account.id].discord_build_indexing_enabled
    await service.set_discord_build_indexing(
        enabled=False, context=ActingContext.direct(UserActor(account_id=account.id))
    )
    assert not repository.accounts[account.id].discord_build_indexing_enabled


async def test_link_rejects_an_invalid_or_conflicting_java_identity() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    linked = InMemoryAccountService(repository)
    with pytest.raises(InvalidVerificationCodeError):
        await linked.link_minecraft_account(
            "bad", consent=CONSENT, context=ActingContext.direct(UserActor(discord_user_id=123, account_id=account.id))
        )

    repository.link_result = VerificationLinkResult(account=account, conflicting_java_uuid=OTHER_JAVA_UUID)
    with pytest.raises(AccountAlreadyLinkedError):
        await linked.link_minecraft_account(
            "valid",
            consent=CONSENT,
            context=ActingContext.direct(UserActor(discord_user_id=123, account_id=account.id)),
        )


async def test_link_returns_the_automatically_claimed_alias() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT, java_uuid=JAVA_UUID)
    alias = CreatorAlias(id=1, name="Player", account_id=account.id, claim_method=ClaimMethod.VERIFIED_IGN)
    repository.link_result = _linked(account, claimed_alias=alias)
    assert account.id is not None

    refresh = await InMemoryAccountService(repository).link_minecraft_account(
        "valid", consent=CONSENT, context=ActingContext.direct(UserActor(discord_user_id=123, account_id=account.id))
    )

    assert refresh.claimed_alias is not None
    assert refresh.claimed_alias.id == alias.id


def _linked(account: Account, *, claimed_alias: CreatorAlias | None = None) -> VerificationLinkResult:
    """Build a scripted successful link result with its corresponding identity refresh."""
    assert account.id is not None
    return VerificationLinkResult(
        account=account,
        claimed_alias=claimed_alias,
        refresh=IdentityRefresh(
            account_id=account.id,
            java_uuid=JAVA_UUID,
            current_name="Player",
            claimed_alias=claimed_alias,
        ),
    )


def _preview(**overrides: object) -> LinkPreview:
    defaults: dict[str, object] = {"java_uuid": JAVA_UUID, "username": "Player"}
    return LinkPreview(**(defaults | overrides))  # type: ignore[arg-type]


async def test_a_bad_code_fails_at_reservation_before_any_prompt() -> None:
    repository = FakeAccountRepository()

    with pytest.raises(InvalidVerificationCodeError):
        await InMemoryAccountService(repository).reserve_minecraft_link(
            "bad", context=ActingContext.direct(UserActor(discord_user_id=123))
        )

    # An invalid reservation consumes a verification attempt.
    assert repository.failures[ATTEMPT] == 1


async def test_reserving_returns_the_preview_the_prompt_needs() -> None:
    repository = FakeAccountRepository()
    credit = CreditPreview(name="Player", build_count=12)
    repository.reservable["good"] = _preview(credit=credit)

    reservation = await InMemoryAccountService(repository).reserve_minecraft_link(
        "good", context=ActingContext.direct(UserActor(discord_user_id=123))
    )

    assert reservation.preview.username == "Player"
    assert reservation.preview.credit == credit
    assert reservation.preview.credit is not None
    assert not reservation.preview.credit.is_contested
    assert reservation.token


async def test_reservation_lockout_is_enforced_before_reserving() -> None:
    """Reservation reveals whether a code is valid, so it needs the same attempt limit as redemption."""
    repository = FakeAccountRepository()
    repository.reservable["good"] = _preview()
    accounts = InMemoryAccountService(repository)
    for _ in range(VERIFICATION_MAX_CONSECUTIVE_FAILURES):
        with pytest.raises(InvalidVerificationCodeError):
            await accounts.reserve_minecraft_link("bad", context=ActingContext.direct(UserActor(discord_user_id=123)))

    with pytest.raises(VerificationAttemptsExhaustedError):
        await accounts.reserve_minecraft_link("good", context=ActingContext.direct(UserActor(discord_user_id=123)))


async def test_committing_a_reservation_passes_the_token_through() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    repository.reservable["good"] = _preview()
    repository.link_result = _linked(account)
    accounts = InMemoryAccountService(repository)
    reservation = await accounts.reserve_minecraft_link(
        "good", context=ActingContext.direct(UserActor(discord_user_id=123))
    )

    await accounts.link_minecraft_account(
        "good",
        consent=CONSENT,
        reservation=reservation,
        context=ActingContext.direct(UserActor(discord_user_id=123, account_id=account.id)),
    )

    assert repository.consumed_token == reservation.token


async def test_a_lapsed_reservation_is_not_reported_as_a_bad_code() -> None:
    """An expired reservation should ask the caller to restart confirmation, not replace a valid code."""
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    repository.reservable["good"] = _preview()
    accounts = InMemoryAccountService(repository)
    reservation = await accounts.reserve_minecraft_link(
        "good", context=ActingContext.direct(UserActor(discord_user_id=123))
    )
    await accounts.release_minecraft_link(
        "good", reservation, context=ActingContext.direct(UserActor(discord_user_id=123))
    )

    with pytest.raises(LinkReservationExpiredError):
        await accounts.link_minecraft_account(
            "good",
            consent=CONSENT,
            reservation=reservation,
            context=ActingContext.direct(UserActor(discord_user_id=123, account_id=account.id)),
        )


async def test_committing_a_reservation_is_not_charged_again() -> None:
    """The guess was already capped at reservation time; charging twice halves the real budget."""
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    repository.reservable["good"] = _preview()
    accounts = InMemoryAccountService(repository)
    reservation = await accounts.reserve_minecraft_link(
        "good", context=ActingContext.direct(UserActor(discord_user_id=123))
    )
    repository.link_result = VerificationLinkResult()

    # The code vanished between prompt and commit, which is not a guess.
    repository.reservations.add(reservation.token)
    with pytest.raises(InvalidVerificationCodeError):
        await accounts.link_minecraft_account(
            "good",
            consent=CONSENT,
            reservation=reservation,
            context=ActingContext.direct(UserActor(discord_user_id=123, account_id=account.id)),
        )

    assert repository.failures.get(ATTEMPT, 0) == 0


async def test_consecutive_wrong_codes_lock_the_attempting_identity() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    accounts = InMemoryAccountService(repository)

    for _ in range(VERIFICATION_MAX_CONSECUTIVE_FAILURES):
        with pytest.raises(InvalidVerificationCodeError):
            await accounts.link_minecraft_account(
                "bad",
                consent=CONSENT,
                context=ActingContext.direct(UserActor(discord_user_id=123, account_id=account.id)),
            )

    # Lockout rejects even valid codes. Checking correctness first would still let guesses succeed.
    repository.link_result = _linked(account)
    with pytest.raises(VerificationAttemptsExhaustedError) as caught:
        await accounts.link_minecraft_account(
            "valid",
            consent=CONSENT,
            context=ActingContext.direct(UserActor(discord_user_id=123, account_id=account.id)),
        )
    assert caught.value.retry_after > 0
    assert caught.value.public_context == {"retry_after": caught.value.retry_after}


async def test_a_lockout_is_scoped_to_one_identity() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    accounts = InMemoryAccountService(repository)
    for _ in range(VERIFICATION_MAX_CONSECUTIVE_FAILURES):
        with pytest.raises(InvalidVerificationCodeError):
            await accounts.link_minecraft_account(
                "bad",
                consent=CONSENT,
                context=ActingContext.direct(UserActor(discord_user_id=123, account_id=account.id)),
            )

    # A different identity retains its own attempt budget, so one caller cannot lock everyone out.
    repository.link_result = _linked(account)

    linked = await accounts.link_minecraft_account(
        "valid", consent=CONSENT, context=ActingContext.direct(UserActor(discord_user_id=456, account_id=account.id))
    )

    assert linked.account_id == account.id


async def test_a_success_clears_the_failure_count() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    accounts = InMemoryAccountService(repository)
    for _ in range(VERIFICATION_MAX_CONSECUTIVE_FAILURES - 1):
        with pytest.raises(InvalidVerificationCodeError):
            await accounts.link_minecraft_account(
                "bad",
                consent=CONSENT,
                context=ActingContext.direct(UserActor(discord_user_id=123, account_id=account.id)),
            )

    repository.link_result = _linked(account)
    await accounts.link_minecraft_account(
        "valid", consent=CONSENT, context=ActingContext.direct(UserActor(discord_user_id=123, account_id=account.id))
    )
    assert repository.failures.get(ATTEMPT, 0) == 0

    # Failures are consecutive, so the budget is whole again rather than one attempt from a lockout.
    repository.link_result = VerificationLinkResult()
    for _ in range(VERIFICATION_MAX_CONSECUTIVE_FAILURES - 1):
        with pytest.raises(InvalidVerificationCodeError):
            await accounts.link_minecraft_account(
                "bad",
                consent=CONSENT,
                context=ActingContext.direct(UserActor(discord_user_id=123, account_id=account.id)),
            )
    assert repository.lockouts == {}


async def test_holding_a_correct_code_is_never_charged_as_a_failure() -> None:
    """A linking conflict is not a failed code guess and must not consume the attempt budget."""
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    repository.link_result = VerificationLinkResult(account=account, conflicting_java_uuid=OTHER_JAVA_UUID)
    accounts = InMemoryAccountService(repository)

    for _ in range(VERIFICATION_MAX_CONSECUTIVE_FAILURES + 2):
        with pytest.raises(AccountAlreadyLinkedError):
            await accounts.link_minecraft_account(
                "valid",
                consent=CONSENT,
                context=ActingContext.direct(UserActor(discord_user_id=123, account_id=account.id)),
            )

    assert repository.failures == {}
    assert repository.lockouts == {}


async def test_alias_claim_needs_no_privacy_receipt() -> None:
    repository = FakeAccountRepository()
    seeded = repository.seed_account(123)
    assert seeded.id is not None
    repository.aliases[1] = CreatorAlias(id=1, name="OldName")

    claim = await InMemoryAccountService(repository).request_alias_claim(
        "OldName", context=ActingContext.direct(UserActor(account_id=seeded.id))
    )
    assert claim.account_id == seeded.id


async def test_staff_resolution_records_the_staff_account() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    repository.aliases[1] = CreatorAlias(id=1, name="OldName")
    claim = await InMemoryAccountService(repository).request_alias_claim(
        "OldName", context=ActingContext.direct(UserActor(account_id=account.id))
    )

    with pytest.raises(PermissionDeniedError):
        await InMemoryAccountService(repository).approve_alias_claim(
            claim.id, context=ActingContext.direct(UserActor(account_id=99))
        )

    resolved = await InMemoryAccountService(repository).approve_alias_claim(
        claim.id, context=ActingContext.direct(UserActor(account_id=99, is_bot_owner=True))
    )

    assert resolved.account_id == account.id
    assert resolved.resolved_by_account_id == 99
    assert resolved.status is ClaimStatus.APPROVED
    with pytest.raises(ClaimNotFoundError):
        await InMemoryAccountService(repository).approve_alias_claim(
            claim.id, context=ActingContext.direct(UserActor(account_id=99, is_bot_owner=True))
        )


async def test_merge_requires_recent_proof_of_two_distinct_accounts() -> None:
    repository = FakeAccountRepository()
    repository.merge_result = AccountMerge(1, 2, UUID(int=1), UUID(int=2))
    accounts = InMemoryAccountService(repository)

    result = await accounts.merge_accounts(
        RecentAccountProof(1, NOW),
        RecentAccountProof(2, NOW),
        now=NOW,
        context=ActingContext.direct(UserActor(account_id=1)),
    )
    assert result == repository.merge_result

    with pytest.raises(InvalidMergeProofError):
        await accounts.merge_accounts(
            RecentAccountProof(1, NOW),
            RecentAccountProof(1, NOW),
            now=NOW,
            context=ActingContext.direct(UserActor(account_id=1)),
        )
    with pytest.raises(InvalidMergeProofError):
        await accounts.merge_accounts(
            RecentAccountProof(1, NOW.subtract(minutes=11)),
            RecentAccountProof(2, NOW),
            now=NOW,
            context=ActingContext.direct(UserActor(account_id=1)),
        )


async def test_code_generation_validates_java_identity() -> None:
    repository = FakeAccountRepository()
    generated = await InMemoryAccountService(repository).generate_verification_code(
        JAVA_UUID,
        context=ActingContext.direct(
            ServiceActor(service_id="verification-relay", credential_scopes=frozenset({"account.verify.relay"}))
        ),
    )
    assert generated == 123456
    assert repository.created_code == (JAVA_UUID, "123456", "Player")

    with pytest.raises(MinecraftAccountNotFoundError):
        await InMemoryAccountService(FakeAccountRepository(), username=None).generate_verification_code(
            JAVA_UUID,
            context=ActingContext.direct(
                ServiceActor(service_id="verification-relay", credential_scopes=frozenset({"account.verify.relay"}))
            ),
        )


async def test_code_generation_retries_a_code_another_player_holds() -> None:
    repository = FakeAccountRepository()
    repository.taken_codes = {"111111"}
    codes = iter((111111, 222222))
    accounts = InMemoryAccountService(repository, verification_codes=lambda: next(codes))

    assert (
        await accounts.generate_verification_code(
            JAVA_UUID,
            context=ActingContext.direct(
                ServiceActor(service_id="verification-relay", credential_scopes=frozenset({"account.verify.relay"}))
            ),
        )
        == 222222
    )
    assert repository.created_code == (JAVA_UUID, "222222", "Player")


async def test_refresh_looks_up_the_current_name_and_delegates() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(1, consent=CONSENT, java_uuid=JAVA_UUID)
    assert account.id is not None

    refresh = await InMemoryAccountService(repository, username="RenamedPlayer").refresh_java_identity(
        account.id, context=ActingContext.direct(UserActor(account_id=account.id))
    )

    assert repository.refreshed == (account.id, JAVA_UUID, "RenamedPlayer")
    assert refresh.renamed is True
    assert refresh.previous_name == "Player"
    assert refresh.current_name == "RenamedPlayer"


async def test_refresh_without_a_linked_java_identity_is_rejected() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(1, consent=CONSENT)
    assert account.id is not None

    with pytest.raises(NoLinkedMinecraftAccountError):
        await InMemoryAccountService(repository).refresh_java_identity(
            account.id, context=ActingContext.direct(UserActor(account_id=account.id))
        )


async def test_refresh_of_a_uuid_mojang_no_longer_knows_is_rejected() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(1, consent=CONSENT, java_uuid=JAVA_UUID)
    assert account.id is not None

    with pytest.raises(MinecraftAccountNotFoundError):
        await InMemoryAccountService(repository, username=None).refresh_java_identity(
            account.id, context=ActingContext.direct(UserActor(account_id=account.id))
        )


async def test_refresh_of_an_unknown_account_is_rejected() -> None:
    with pytest.raises(AccountNotFoundError):
        await InMemoryAccountService(FakeAccountRepository()).refresh_java_identity(
            999, context=ActingContext.direct(UserActor(account_id=999))
        )


async def test_refresh_can_name_which_java_identity_to_refresh() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(1, consent=CONSENT, java_uuid=JAVA_UUID)
    assert account.id is not None

    with pytest.raises(NoLinkedMinecraftAccountError):
        await InMemoryAccountService(repository).refresh_java_identity(
            account.id, java_uuid=OTHER_JAVA_UUID, context=ActingContext.direct(UserActor(account_id=account.id))
        )


async def test_refresh_without_uuid_deliberately_selects_most_recent_java_identity() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(1, consent=CONSENT, java_uuid=JAVA_UUID)
    assert account.id is not None
    first = account.identity_for(IdentityProvider.JAVA, str(JAVA_UUID))
    assert first is not None
    newest = replace(
        AccountIdentity.java(
            OTHER_JAVA_UUID,
            username="NewerPlayer",
            verified_at=NOW.add(minutes=1),
        ),
        id=3,
    )
    repository.accounts[account.id] = replace(account, identities=(*account.identities, newest))

    await InMemoryAccountService(repository, username="RefreshedPlayer").refresh_java_identity(
        account.id, context=ActingContext.direct(UserActor(account_id=account.id))
    )

    assert repository.refreshed == (account.id, OTHER_JAVA_UUID, "RefreshedPlayer")


async def test_refresh_exact_uuid_ignores_a_more_recent_java_identity() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(1, consent=CONSENT, java_uuid=JAVA_UUID)
    assert account.id is not None
    newest = replace(
        AccountIdentity.java(
            OTHER_JAVA_UUID,
            username="NewerPlayer",
            verified_at=NOW.add(minutes=1),
        ),
        id=3,
    )
    repository.accounts[account.id] = replace(account, identities=(*account.identities, newest))

    await InMemoryAccountService(repository, username="RefreshedPlayer").refresh_java_identity(
        account.id, java_uuid=JAVA_UUID, context=ActingContext.direct(UserActor(account_id=account.id))
    )

    assert repository.refreshed == (account.id, JAVA_UUID, "RefreshedPlayer")


async def test_an_account_without_discord_can_link_and_unlink_minecraft() -> None:
    """A Bedrock-only account can manage its Java identity without acquiring a Discord identity."""
    repository = FakeAccountRepository()
    account = repository.seed_account(555, provider=IdentityProvider.BEDROCK, consent=CONSENT, java_uuid=JAVA_UUID)
    assert account.id is not None
    assert account.identities_for(IdentityProvider.DISCORD) == ()
    alias = CreatorAlias(id=1, name="Player", account_id=account.id, claim_method=ClaimMethod.VERIFIED_IGN)
    repository.link_result = _linked(account, claimed_alias=alias)
    accounts = InMemoryAccountService(repository)

    linked = await accounts.link_minecraft_account(
        "valid", consent=CONSENT, context=ActingContext.direct(UserActor(discord_user_id=123, account_id=account.id))
    )

    assert linked.claimed_alias is not None
    assert linked.claimed_alias.id == alias.id
    java = repository.accounts[account.id].identity_for(IdentityProvider.JAVA, str(JAVA_UUID))
    assert java is not None
    assert java.id is not None
    await repository.unlink_identity(account.id, java.id)
    assert repository.accounts[account.id].identities_for(IdentityProvider.JAVA) == ()


async def test_linking_against_a_missing_account_does_not_create_one() -> None:
    repository = FakeAccountRepository()

    with pytest.raises(AccountNotFoundError):
        await InMemoryAccountService(repository).link_minecraft_account(
            "valid", consent=CONSENT, context=ActingContext.direct(UserActor(discord_user_id=123, account_id=999))
        )

    assert repository.accounts == {}


class TestProfiles:
    async def test_public_profile_applies_visibility(self) -> None:
        repository = FakeAccountRepository()
        account = repository.seed_account(1, consent=CONSENT)
        assert account.id is not None
        assert account.public_account_id is not None
        accounts = InMemoryAccountService(repository)
        await repository.upsert_profile(account.id, ProfileUpdate(display_name="Notch", hidden=True))

        public = await accounts.get_public_profile(
            account.public_account_id, context=ActingContext.direct(AnonymousActor())
        )

        assert public is not None
        assert public.hidden
        assert public.display_name is None

    async def test_public_profile_is_none_for_an_unknown_creator(self) -> None:
        assert (
            await InMemoryAccountService(FakeAccountRepository()).get_public_profile(
                UUID(int=99), context=ActingContext.direct(AnonymousActor())
            )
            is None
        )


class TestIdentityManagement:
    async def test_list_identities_includes_hidden_ones(self) -> None:
        repository = FakeAccountRepository()
        account = repository.seed_account(1, consent=CONSENT, java_uuid=JAVA_UUID)
        assert account.id is not None
        accounts = InMemoryAccountService(repository)
        await repository.set_identity_visibility(account.id, 2, is_public=False)

        identities = await accounts.list_identities(context=ActingContext.direct(UserActor(account_id=account.id)))

        assert len(identities) == 2
        assert not next(identity for identity in identities if identity.id == 2).is_public


class TestMergeCodes:
    """The two-sided proof: minting is the absorbed side, redeeming is the survivor."""

    async def test_a_minted_code_previews_and_completes(self) -> None:
        repository = FakeAccountRepository()
        survivor = repository.seed_account(1, consent=CONSENT)
        absorbed = repository.seed_account(2, consent=CONSENT)
        assert survivor.id is not None
        assert absorbed.id is not None
        repository.merge_result = AccountMerge(survivor.id, absorbed.id, UUID(int=1), UUID(int=2))
        accounts = InMemoryAccountService(repository)

        code, ticket = await accounts.create_merge_code(context=ActingContext.direct(UserActor(account_id=absorbed.id)))
        preview = await accounts.preview_merge(code, context=ActingContext.direct(UserActor(account_id=survivor.id)))

        assert ticket.account_id == absorbed.id
        assert preview.absorbed_public_account_id == absorbed.public_account_id
        assert preview.identity_count == 1

        merge = await accounts.complete_merge(
            code, now=NOW, context=ActingContext.direct(UserActor(account_id=survivor.id))
        )

        assert merge.surviving_account_id == survivor.id
        assert merge.absorbed_account_id == absorbed.id

    async def test_a_code_cannot_be_spent_twice(self) -> None:
        repository = FakeAccountRepository()
        survivor = repository.seed_account(1, consent=CONSENT)
        absorbed = repository.seed_account(2, consent=CONSENT)
        assert survivor.id is not None
        assert absorbed.id is not None
        repository.merge_result = AccountMerge(survivor.id, absorbed.id, UUID(int=1), UUID(int=2))
        accounts = InMemoryAccountService(repository)
        code, _ = await accounts.create_merge_code(context=ActingContext.direct(UserActor(account_id=absorbed.id)))
        await accounts.complete_merge(code, now=NOW, context=ActingContext.direct(UserActor(account_id=survivor.id)))

        with pytest.raises(InvalidMergeCodeError):
            await accounts.complete_merge(
                code, now=NOW, context=ActingContext.direct(UserActor(account_id=survivor.id))
            )

    async def test_minting_replaces_the_previous_code(self) -> None:
        """Replacing a ticket must also invalidate the previously issued code."""
        repository = FakeAccountRepository()
        survivor = repository.seed_account(1, consent=CONSENT)
        absorbed = repository.seed_account(2, consent=CONSENT)
        assert survivor.id is not None
        assert absorbed.id is not None
        accounts = InMemoryAccountService(repository)

        first, _ = await accounts.create_merge_code(context=ActingContext.direct(UserActor(account_id=absorbed.id)))
        await accounts.create_merge_code(context=ActingContext.direct(UserActor(account_id=absorbed.id)))

        with pytest.raises(InvalidMergeCodeError):
            await accounts.preview_merge(first, context=ActingContext.direct(UserActor(account_id=survivor.id)))

    async def test_an_unknown_code_is_refused(self) -> None:
        repository = FakeAccountRepository()
        survivor = repository.seed_account(1, consent=CONSENT)
        assert survivor.id is not None

        with pytest.raises(InvalidMergeCodeError):
            await InMemoryAccountService(repository).complete_merge(
                "NOTACODE", context=ActingContext.direct(UserActor(account_id=survivor.id))
            )

    async def test_an_expired_ticket_is_refused(self) -> None:
        repository = FakeAccountRepository()
        survivor = repository.seed_account(1, consent=CONSENT)
        absorbed = repository.seed_account(2, consent=CONSENT)
        assert survivor.id is not None
        assert absorbed.id is not None
        accounts = InMemoryAccountService(repository)
        code, _ = await accounts.create_merge_code(context=ActingContext.direct(UserActor(account_id=absorbed.id)))
        repository.expire_merge_tickets()

        with pytest.raises(InvalidMergeCodeError):
            await accounts.complete_merge(
                code, now=NOW, context=ActingContext.direct(UserActor(account_id=survivor.id))
            )

    async def test_merging_an_account_into_itself_is_refused(self) -> None:
        repository = FakeAccountRepository()
        account = repository.seed_account(1, consent=CONSENT)
        assert account.id is not None
        accounts = InMemoryAccountService(repository)
        code, _ = await accounts.create_merge_code(context=ActingContext.direct(UserActor(account_id=account.id)))

        with pytest.raises(InvalidMergeCodeError):
            await accounts.preview_merge(code, context=ActingContext.direct(UserActor(account_id=account.id)))

    async def test_the_ticket_window_matches_the_proof_window(self) -> None:
        """Keep ticket expiry aligned with the maximum age accepted for merge proof."""
        assert MERGE_TICKET_TTL_SECONDS == MERGE_PROOF_MAX_AGE_SECONDS


async def test_an_account_can_be_created_with_its_consent_receipt_in_one_write() -> None:
    """Store consent with the new account so a separate failing call cannot leave the receipt missing."""
    repository = FakeAccountRepository()
    service = InMemoryAccountService(repository)
    consent = PrivacyNoticeReceipt.grant_current()

    account = await service.get_or_create_identity(IdentityProvider.DISCORD, "99", consent=consent)

    assert account.consent == consent


async def test_an_existing_account_is_not_handed_a_receipt_it_never_agreed_to() -> None:
    """Account lookup must not grant consent to an existing account as a side effect."""
    repository = FakeAccountRepository()
    service = InMemoryAccountService(repository)
    existing = repository.seed_account(99, provider=IdentityProvider.DISCORD)

    account = await service.get_or_create_identity(
        IdentityProvider.DISCORD, "99", consent=PrivacyNoticeReceipt.grant_current()
    )

    assert account.id == existing.id
    assert account.consent is None
