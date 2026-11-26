"""Native DTOs preserve public JSON while withholding ownership metadata."""

from uuid import UUID

from litestar import Litestar, Response, get
from litestar.testing import TestClient
from whenever import Instant

from squid.api.v1.accounts import AccountMergeDTO, LinkedIdentityDTO, MergePreviewDTO
from squid.core.accounts.domain import AccountMerge, IdentityProvider, MergePreview
from squid.core.accounts.models import AccountIdentity

NOW = Instant.parse_iso("2026-08-10T12:00:00Z")
CREATOR_ID = UUID("22222222-2222-4222-8222-222222222222")
REDIRECT_ID = UUID("33333333-3333-4333-8333-333333333333")


def test_linked_identity_dto_keeps_response_headers_and_hides_provider_avatar_key() -> None:
    identity = AccountIdentity(
        id=8,
        account_id=42,
        provider=IdentityProvider.DISCORD,
        subject="123",
        display_name="Builder",
        verified_at=NOW,
        is_public=False,
        avatar_key="private-provider-avatar-key",
    )

    @get("/", return_dto=LinkedIdentityDTO)
    async def endpoint() -> Response[AccountIdentity]:
        return Response(identity, headers={"ETag": '"account-42-r3"'})

    app = Litestar([endpoint], type_encoders={Instant: Instant.format_iso}, openapi_config=None)
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert response.headers["ETag"] == '"account-42-r3"'
    assert response.json() == {
        "id": 8,
        "provider": "discord",
        "subject": "123",
        "display_name": "Builder",
        "verified_at": "2026-08-10T12:00:00Z",
        "is_public": False,
    }


def test_merge_dtos_rename_public_ids_without_exposing_internal_account_ids() -> None:
    @get("/preview", return_dto=MergePreviewDTO)
    async def preview() -> MergePreview:
        return MergePreview(REDIRECT_ID, ("Builder",), 2, 4)

    @get("/complete", return_dto=AccountMergeDTO)
    async def complete() -> AccountMerge:
        return AccountMerge(42, 51, CREATOR_ID, REDIRECT_ID)

    app = Litestar([preview, complete], openapi_config=None)
    with TestClient(app) as client:
        assert client.get("/preview").json() == {
            "absorbed_creator_id": str(REDIRECT_ID),
            "alias_names": ["Builder"],
            "identity_count": 2,
            "build_count": 4,
        }
        assert client.get("/complete").json() == {
            "surviving_creator_id": str(CREATOR_ID),
            "redirected_creator_id": str(REDIRECT_ID),
        }
