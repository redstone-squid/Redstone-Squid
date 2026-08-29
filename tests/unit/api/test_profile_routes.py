"""Profile, visibility, and identity-management route contracts."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from whenever import Instant

from squid.accounts.domain import (
    UNSET,
    AccountIdentity,
    AccountMerge,
    AccountProfile,
    IdentityProvider,
    MergePreview,
    MergeTicket,
    ProfileLink,
)
from squid.accounts.errors import InvalidMergeCodeError, LastIdentityError
from squid.api.security import UNBOUNDED, Caller
from squid.api.v1.me import (
    clear_profile,
    complete_merge,
    create_merge_code,
    list_identities,
    preview_merge,
    set_identity_visibility,
    unlink_identity,
    update_profile,
)
from squid.api.v1.schemas.me import IdentityVisibilityRequest, MergeRequest, ProfileUpdateRequest
from squid.core.errors import AuthenticationError

JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")
SURVIVING_ID = UUID("22222222-2222-4222-8222-222222222222")
ABSORBED_ID = UUID("33333333-3333-4333-8333-333333333333")
DISCORD = replace(AccountIdentity.discord(7), id=1)
JAVA = replace(AccountIdentity.java(JAVA_UUID, username="Steve"), id=2)


def _caller() -> Caller:
    return Caller(kind="account", subject="account:1", nodes=UNBOUNDED, account_id=1)


def _accounts(**overrides: object) -> Any:
    defaults: dict[str, object] = {
        "list_identities": AsyncMock(return_value=(DISCORD, JAVA)),
        "update_profile": AsyncMock(return_value=AccountProfile.empty(1)),
        "set_identity_visibility": AsyncMock(return_value=replace(JAVA, is_public=False)),
        "unlink_identity": AsyncMock(return_value=JAVA),
        "clear_profile": AsyncMock(return_value=AccountProfile.empty(1)),
    }
    return cast(Any, SimpleNamespace(**(defaults | overrides)))


class TestProfileUpdate:
    async def test_omitted_fields_are_left_alone_and_null_clears(self) -> None:
        accounts = _accounts()
        body = ProfileUpdateRequest.model_validate({"bio": None, "display_name": "Steve"})

        await update_profile(body, accounts, _caller())

        update = accounts.update_profile.await_args.args[1]
        assert update.display_name == "Steve"
        assert update.bio is None
        # Never sent, so it must not reach the service as a clear.
        assert update.pronouns is UNSET

    async def test_links_reach_the_service_as_domain_values(self) -> None:
        accounts = _accounts()
        body = ProfileUpdateRequest.model_validate({"links": [{"label": "Site", "url": "https://example.com"}]})

        await update_profile(body, accounts, _caller())

        assert accounts.update_profile.await_args.args[1].links == (ProfileLink("Site", "https://example.com"),)

    async def test_hidden_null_is_treated_as_false_rather_than_a_clear(self) -> None:
        accounts = _accounts()

        await update_profile(ProfileUpdateRequest.model_validate({"hidden": None}), accounts, _caller())

        assert accounts.update_profile.await_args.args[1].hidden is False

    async def test_the_response_renders_the_saved_profile(self) -> None:
        profile = AccountProfile(account_id=1, display_name="Steve", avatar_identity_id=2)
        accounts = _accounts(update_profile=AsyncMock(return_value=profile))

        response = await update_profile(ProfileUpdateRequest(), accounts, _caller())

        assert response.display_name == "Steve"
        assert response.avatar is not None
        assert response.avatar.provider is IdentityProvider.JAVA

    async def test_an_unauthenticated_caller_is_rejected(self) -> None:
        anonymous = Caller(kind="anonymous", subject="anonymous")

        with pytest.raises(AuthenticationError):
            await update_profile(ProfileUpdateRequest(), _accounts(), anonymous)

    async def test_unknown_fields_are_refused(self) -> None:
        with pytest.raises(ValueError, match=r"extra_forbidden|Extra inputs"):
            ProfileUpdateRequest.model_validate({"nickname": "Steve"})


class TestIdentities:
    async def test_listing_includes_hidden_identities(self) -> None:
        accounts = _accounts(list_identities=AsyncMock(return_value=(DISCORD, replace(JAVA, is_public=False))))

        response = await list_identities(accounts, _caller())

        assert [identity.id for identity in response] == [1, 2]
        assert [identity.is_public for identity in response] == [True, False]

    async def test_visibility_is_set_on_the_callers_own_account(self) -> None:
        accounts = _accounts()

        response = await set_identity_visibility(2, IdentityVisibilityRequest(public=False), accounts, _caller())

        accounts.set_identity_visibility.assert_awaited_once_with(1, 2, is_public=False)
        assert response.is_public is False

    async def test_unlink_returns_the_removed_identity(self) -> None:
        accounts = _accounts()

        response = await unlink_identity(2, accounts, _caller())

        accounts.unlink_identity.assert_awaited_once_with(1, 2)
        assert response.provider is IdentityProvider.JAVA

    async def test_unlinking_the_last_identity_surfaces_a_conflict(self) -> None:
        accounts = _accounts(unlink_identity=AsyncMock(side_effect=LastIdentityError(account_id=1)))

        with pytest.raises(LastIdentityError):
            await unlink_identity(1, accounts, _caller())


class TestStaffClear:
    async def test_clearing_targets_the_named_account(self) -> None:
        accounts = _accounts()

        response = await clear_profile(42, accounts)

        accounts.clear_profile.assert_awaited_once_with(42)
        assert response.display_name is None
        assert response.hidden is False


class TestMerge:
    def _merge_accounts(self, **overrides: object) -> Any:
        preview = MergePreview(
            absorbed_public_creator_id=ABSORBED_ID,
            alias_names=("Notch",),
            identity_count=2,
            build_count=3,
        )
        merge = AccountMerge(1, 2, SURVIVING_ID, ABSORBED_ID)
        ticket = MergeTicket(1, Instant.from_utc(2026, 8, 18), Instant.from_utc(2026, 8, 18, 0, 10))
        defaults: dict[str, object] = {
            "create_merge_code": AsyncMock(return_value=("ABCD2345", ticket)),
            "preview_merge": AsyncMock(return_value=preview),
            "complete_merge": AsyncMock(return_value=merge),
        }
        return cast(Any, SimpleNamespace(**(defaults | overrides)))

    async def test_a_code_is_minted_for_the_calling_account(self) -> None:
        """Minted by the side being absorbed: it is that side's consent to disappear."""
        accounts = self._merge_accounts()

        response = await create_merge_code(accounts, _caller())

        accounts.create_merge_code.assert_awaited_once_with(1)
        assert response.code == "ABCD2345"

    async def test_the_preview_names_what_would_move(self) -> None:
        accounts = self._merge_accounts()

        response = await preview_merge(MergeRequest(code="ABCD2345"), accounts, _caller())

        assert response.absorbed_creator_id == ABSORBED_ID
        assert response.alias_names == ["Notch"]
        assert response.build_count == 3

    async def test_completing_reports_the_permanent_redirect(self) -> None:
        accounts = self._merge_accounts()

        response = await complete_merge(MergeRequest(code="ABCD2345"), accounts, _caller())

        accounts.complete_merge.assert_awaited_once_with(1, "ABCD2345")
        assert response.surviving_creator_id == SURVIVING_ID
        assert response.redirected_creator_id == ABSORBED_ID

    async def test_a_bad_code_surfaces_as_a_validation_error(self) -> None:
        accounts = self._merge_accounts(complete_merge=AsyncMock(side_effect=InvalidMergeCodeError))

        with pytest.raises(InvalidMergeCodeError):
            await complete_merge(MergeRequest(code="NOPE"), accounts, _caller())

    async def test_an_unauthenticated_caller_cannot_mint(self) -> None:
        anonymous = Caller(kind="anonymous", subject="anonymous")

        with pytest.raises(AuthenticationError):
            await create_merge_code(self._merge_accounts(), anonymous)
