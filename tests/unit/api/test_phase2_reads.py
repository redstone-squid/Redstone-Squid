"""Public tags, versions, creator profiles, schematic previews, and voting-session reads."""

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast, override
from unittest.mock import Mock
from uuid import UUID

import pytest
from litestar import Litestar, Request
from litestar.di import Provide
from litestar.testing import TestClient

from squid.api.v1.schemas.votes import VoteSessionDetail
from squid.config import SchematicConfig
from squid.core.accounts.application import AccountService
from squid.core.accounts.domain import CreditedAlias, PublicCreatorProfile
from squid.core.accounts.models import CreatorAlias
from squid.core.actors import ActingContext
from squid.core.artifacts.infrastructure import LocalArtifactStore
from squid.core.builds.application import BuildQueryService
from squid.core.builds.domain import BuildStatus
from squid.core.builds.history import BuildPublication
from squid.core.builds.models import Build, DoorBuild
from squid.core.media.application.build_media import PublishedBuildMedia
from squid.core.media.domain.jobs import MediaArtifactRole
from squid.core.media.models import MediaArtifactRecord
from squid.core.schematics.application import RenderedSchematic, RenderRequest, RenderSkipReason, SchematicService
from squid.core.schematics.bootstrap import create_schematic_service
from squid.core.schematics.errors import SchematicNotFoundError, SchematicRenderRefusedError
from squid.core.tags.application import TagService
from squid.core.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.core.tags.models import TagDefinition
from squid.core.versions.application.services import VersionService
from squid.core.versions.domain import MinecraftVersion, VersionEntry
from squid.core.voting.application import VoteService
from squid.core.voting.domain import (
    VoteAlias,
    VoteChoice,
    VoteKind,
    VoteMenu,
    VoteOption,
    Voter,
    VoteSelection,
    VoteSessionSnapshot,
    VoteVisibility,
)
from squid.runtime import ApiServices
from tests.support.voting import build_snapshot, poll_snapshot
from tests.unit.api.fakes import MockDatabaseManager, MockSchematics

CREATOR_PUBLIC_ID = UUID("22222222-2222-2222-2222-222222222222")
RENDER_RECIPE_HASH = "b" * 64

# Overrides keep service signatures checked while omitting repository construction.
# These fakes implement only the methods reached by the routes tested here.


class PublicTagFake(TagService):
    def __init__(self, definition: TagDefinition) -> None:
        self.definition = definition

    @override
    async def public_definitions(
        self,
        *,
        kind: TagSemanticKind | None = None,
        authority: TagAuthority | None = None,
        q: str = "",
        context: ActingContext,
    ) -> Sequence[TagDefinition]:
        return (self.definition,)

    @override
    async def public_definition(self, tag_id: int | str, *, context: ActingContext) -> TagDefinition | None:
        return self.definition if tag_id == self.definition.id else None


class VersionFake(VersionService):
    def __init__(self) -> None:
        pass

    @override
    async def list_all(self, *, context: ActingContext) -> list[VersionEntry]:
        return [
            VersionEntry(1, MinecraftVersion("Java", 1, 21, 5), data_version=4325),
            VersionEntry(2, MinecraftVersion("Bedrock", 1, 21, 50)),
        ]


class AccountFake(AccountService):
    def __init__(self) -> None:
        pass

    @override
    async def get_creator_alias(self, alias: int | str, *, context: ActingContext) -> CreatorAlias | None:
        if alias != 7 and (not isinstance(alias, str) or alias.casefold() != "builder"):
            return None
        return CreatorAlias(id=7, name="Builder", account_id=42)

    @override
    async def get_public_profile(self, public_id: UUID, *, context: ActingContext) -> PublicCreatorProfile | None:
        if public_id != CREATOR_PUBLIC_ID:
            return None
        return PublicCreatorProfile(
            public_id=CREATOR_PUBLIC_ID,
            hidden=False,
            aliases=(CreditedAlias("Builder", build_count=2), CreditedAlias("OldBuilder")),
            display_name="Builder",
        )


class SchematicFake(SchematicService):
    def __init__(self) -> None:
        pass

    @override
    async def render_content(
        self, recipe_hash: str, *, context: ActingContext, max_bytes: int = 8 * 1024 * 1024
    ) -> bytes:
        # Raising rather than returning None, because that is what the real service does
        # when the hash is unknown, and the route has no branch for a missing preview.
        if recipe_hash != RENDER_RECIPE_HASH:
            raise SchematicNotFoundError(context={"recipe_hash": recipe_hash})
        return b"\x89PNG\r\n\x1a\npreview"


class RefusingRenderFake(SchematicService):
    """A build whose attachment can never be previewed, whatever the camera does."""

    def __init__(self) -> None:
        pass

    @override
    def render_recipe(self, **_overrides: object) -> RenderRequest:  # type: ignore[override]
        return RenderRequest()

    @override
    async def render_now(
        self, build_id: int, *, context: ActingContext, request: RenderRequest | None = None
    ) -> RenderedSchematic:
        reason = RenderSkipReason.OVER_VOLUME_BUDGET
        raise SchematicRenderRefusedError(reason.value, reason.description)


class ConfirmedBuildFake(BuildQueryService):
    @override
    async def publication(self, build: Build, *, context: ActingContext) -> BuildPublication:
        return BuildPublication()

    def __init__(self) -> None:
        pass

    @override
    async def get_public(self, build_id: int, *, context: ActingContext) -> Build:
        return DoorBuild(
            id=build_id,
            submitter_account_id=1,
            submission_status=BuildStatus.APPROVED,
            versions=["1.21"],
            door_width=2,
            door_height=2,
            patterns=["Regular"],
            orientation="Door",
        )


class PublishedMediaFake(PublishedBuildMedia):
    def __init__(self, tmp_path: Path, data: bytes) -> None:
        super().__init__(Mock(), LocalArtifactStore(tmp_path), public_base_url="https://example.com")
        self.artifact = MediaArtifactRecord(
            upload_id=UUID("12345678-1234-5678-1234-567812345678"),
            role=MediaArtifactRole.OUTPUT,
            object_key="output",
            content_type="image/png",
            byte_size=len(data),
            width=640,
            height=320,
            sha256=sha256(data).hexdigest(),
        )
        (tmp_path / self.artifact.object_key).write_bytes(data)

    @override
    async def mirrored_images(self, build_id: int, *, context: ActingContext) -> list[tuple[str, UUID]]:
        return []

    @override
    async def outputs(
        self, build_id: int, *, context: ActingContext, upload_id: UUID | None = None
    ) -> list[MediaArtifactRecord]:
        return [self.artifact] if build_id == 7 and upload_id in {None, self.artifact.upload_id} else []


class VoteFake(VoteService):
    def __init__(self, session: VoteSessionSnapshot) -> None:
        self.session = session

    @override
    async def get_session(self, vote_session_id: int, *, context: ActingContext) -> VoteSessionSnapshot | None:
        return self.session if vote_session_id == self.session.id else None


@dataclass(frozen=True, slots=True)
class PartialReadServices:
    tags: TagService | None = None
    versions: VersionService | None = None
    accounts: AccountService | None = None
    build_queries: BuildQueryService | None = None
    schematics: SchematicService | None = None
    votes: VoteService | None = None
    published_build_media: PublishedBuildMedia | None = None


def _override(
    app: Litestar,
    *,
    tags: TagService | None = None,
    versions: VersionService | None = None,
    accounts: AccountService | None = None,
    build_queries: BuildQueryService | None = None,
    schematics: SchematicService | None = None,
    votes: VoteService | None = None,
    published_build_media: PublishedBuildMedia | None = None,
) -> None:
    """Replace dependencies with only the services required by the tested routes.

    The cast permits a partial graph; it does not verify that a newly reached collaborator
    exists. The service subclasses retain method-signature checking.
    """
    fake_services = cast(
        ApiServices,
        PartialReadServices(
            tags=tags,
            versions=versions,
            accounts=accounts,
            build_queries=build_queries,
            schematics=schematics,
            votes=votes,
            published_build_media=published_build_media,
        ),
    )

    async def provide_services(request: Request) -> ApiServices:
        return fake_services

    provider = cast(Provide, app.dependencies["services"])
    provider.dependency = provide_services


def test_public_build_links_and_serves_retained_media(
    app_factory: tuple[Litestar, MockDatabaseManager], tmp_path: Path
) -> None:
    app, _database = app_factory
    data = b"processed image"
    media = PublishedMediaFake(tmp_path, data)
    _override(app, build_queries=ConfirmedBuildFake(), schematics=MockSchematics(), published_build_media=media)
    resource = f"/v1/builds/7/media/{media.artifact.upload_id}/content"

    with TestClient(app) as client:
        detail = client.get("/v1/builds/7")
        content = client.get(resource)
        unrelated = client.get(resource.replace("/builds/7/", "/builds/8/"))

    assert detail.status_code == 200
    assert detail.json()["preview"] == {
        "kind": "image",
        "url": f"https://example.com{resource}",
        "width": 640,
        "height": 320,
    }
    assert [item["url"] for item in detail.json()["media"] if item["kind"] == "image"] == [
        f"https://example.com{resource}"
    ]
    assert content.status_code == 200
    assert content.headers["content-type"] == "image/png"
    assert content.content == data
    assert unrelated.status_code == 404


def test_tag_and_version_collections_use_page_envelope(
    app_factory: tuple[Litestar, MockDatabaseManager],
) -> None:
    app, _database = app_factory
    definition = TagDefinition(
        id=4,
        stable_key="official_seamless",
        display_name="Seamless",
        authority=TagAuthority.OFFICIAL,
        semantic_kind=TagSemanticKind.RESTRICTION,
        value_type=TagValueType.NONE,
        moderation_status=TagModerationStatus.APPROVED,
        query_name="seamless",
        restriction_type="miscellaneous",
    )
    definition.slug = "seamless"
    _override(app, tags=PublicTagFake(definition), versions=VersionFake())

    with TestClient(app) as client:
        tags = client.get("/v1/tags")
        versions = client.get("/v1/versions")

    assert tags.status_code == 200
    assert tags.json()["items"] == [
        {
            "id": 4,
            "key": "official_seamless",
            "aliases": [],
            "build_count": 0,
            "record_count": 0,
            "slug": "seamless",
            "name": "Seamless",
            "query_name": "seamless",
            "authority": "official",
            "kind": "restriction",
            "value_type": "none",
            "restriction_type": "miscellaneous",
            "record_operator": None,
            "canonical_unit": None,
            "display_unit": None,
            "numeric_step": None,
        }
    ]
    assert tags.json()["total"] == 1
    assert tags.json()["next"] is None
    assert [item["display_name"] for item in versions.json()["items"]] == ["Java 1.21.5", "Bedrock 1.21.50"]


def test_creator_alias_never_exposes_linked_account(
    app_factory: tuple[Litestar, MockDatabaseManager],
) -> None:
    app, _database = app_factory
    _override(app, accounts=AccountFake())

    with TestClient(app) as client:
        response = client.get("/v1/creator-aliases/lookup", params={"name": "builder"})
        by_id = client.get("/v1/creator-aliases/7")

    assert response.status_code == 200
    assert by_id.json() == response.json()
    assert response.json() == {"id": 7, "name": "Builder"}
    assert "user_id" not in response.text


def test_creator_profile_groups_public_aliases(app_factory: tuple[Litestar, MockDatabaseManager]) -> None:
    app, _database = app_factory
    _override(app, accounts=AccountFake())

    with TestClient(app) as client:
        response = client.get("/v1/accounts/22222222-2222-2222-2222-222222222222")

    assert response.status_code == 200
    assert response.json() == {
        "id": "22222222-2222-2222-2222-222222222222",
        "canonical_id": None,
        "viewer": None,
        "hidden": False,
        "aliases": [
            {"name": "Builder", "build_count": 2},
            {"name": "OldBuilder", "build_count": 0},
        ],
        "display_name": "Builder",
        "bio": None,
        "pronouns": None,
        "links": [],
        "avatar_url": None,
        "joined_at": None,
        "identities": [],
    }


def test_an_unpreviewable_build_answers_409_with_the_reason(
    app_factory: tuple[Litestar, MockDatabaseManager],
) -> None:
    """A refusal is about the build's state, so it is a conflict rather than a 404 or a 500."""
    app, _database = app_factory
    _override(app, build_queries=ConfirmedBuildFake(), schematics=RefusingRenderFake())

    with TestClient(app) as client:
        response = client.get("/v1/builds/7/schematics/render")

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "SCHEMATIC_RENDER_REFUSED"
    assert body["context"] == {"reason": "over_volume_budget"}


def test_a_render_rejects_a_camera_outside_the_supported_range(
    app_factory: tuple[Litestar, MockDatabaseManager],
) -> None:
    app, _database = app_factory
    _override(app, build_queries=ConfirmedBuildFake(), schematics=RefusingRenderFake())

    with TestClient(app) as client:
        response = client.get("/v1/builds/7/schematics/render", params={"pitch": 120})

    assert response.status_code == 422


def test_schematic_render_content_is_immutable_png(app_factory: tuple[Litestar, MockDatabaseManager]) -> None:
    app, _database = app_factory
    _override(app, schematics=SchematicFake())

    with TestClient(app) as client:
        response = client.get(f"/v1/schematic-renders/{RENDER_RECIPE_HASH}/content")

    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n\x1a\npreview"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def _pack_service(config: SchematicConfig) -> SchematicService:
    # The API's real wiring, so a pack configured without rendering still reaches the routes.
    return create_schematic_service(config, Mock(), Mock(), uow_factory=Mock(), permissions=Mock(), render_capable=True)


def test_resource_pack_redirects_to_its_digest_and_serves_an_immutable_zip(
    app_factory: tuple[Litestar, MockDatabaseManager], tmp_path: Path
) -> None:
    app, _database = app_factory
    pack = tmp_path / "pack.zip"
    pack.write_bytes(b"PK\x03\x04textures")
    digest = sha256(pack.read_bytes()).hexdigest()
    _override(app, schematics=_pack_service(SchematicConfig(render_pack_path=pack, render_cache_dir=tmp_path)))

    with TestClient(app) as client:
        redirect = client.get("/v1/schematic-resource-pack", follow_redirects=False)
        content = client.get(redirect.headers["location"])
        replaced = client.get(f"/v1/schematic-resource-packs/{'0' * 64}")

    assert redirect.status_code == 307
    assert redirect.headers["location"] == f"/v1/schematic-resource-packs/{digest}"
    assert content.status_code == 200
    assert content.content == pack.read_bytes()
    assert content.headers["content-type"] == "application/zip"
    assert content.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert replaced.status_code == 404


def test_resource_pack_is_unavailable_without_a_configured_pack(
    app_factory: tuple[Litestar, MockDatabaseManager], tmp_path: Path
) -> None:
    app, _database = app_factory
    _override(app, schematics=_pack_service(SchematicConfig(render_cache_dir=tmp_path)))

    with TestClient(app) as client:
        response = client.get("/v1/schematic-resource-pack", follow_redirects=False)

    assert response.status_code == 503


def test_hidden_vote_session_omits_ballots_and_live_tallies(
    app_factory: tuple[Litestar, MockDatabaseManager],
) -> None:
    app, _database = app_factory
    session = poll_snapshot(
        id=9,
        author_account_id=123,
        question="Pick a color",
        visibility=VoteVisibility.ANONYMOUS_HIDDEN,
        guild_id=99,
        menu=VoteMenu(
            (
                VoteOption("red", VoteChoice.GENERIC, label="Red"),
                VoteOption("blue", VoteChoice.GENERIC, label="Blue", position=1),
            ),
            (VoteAlias("red", "1"), VoteAlias("blue", "2", position=1)),
        ),
        selections=(VoteSelection(Voter.account(111), 99, "red", "1", 2.5),),
    )
    _override(app, votes=VoteFake(session))

    with TestClient(app) as client:
        response = client.get("/v1/vote-sessions/9")

    assert response.status_code == 200
    payload = response.json()
    assert payload["tallies"] is None
    assert payload["options"] == [
        {"id": "red", "label": "Red", "choice": "generic", "multiplier": 1.0, "position": 0},
        {"id": "blue", "label": "Blue", "choice": "generic", "multiplier": 1.0, "position": 1},
    ]
    assert "111" not in response.text
    assert "author_id" not in response.text


@pytest.mark.parametrize("visibility", [VoteVisibility.ANONYMOUS_LIVE, VoteVisibility.ANONYMOUS_HIDDEN])
def test_anonymous_polls_do_not_name_their_voters(visibility: VoteVisibility) -> None:
    selections = (VoteSelection(Voter.account(111), 99, "red", "1", 2.5, "Alex"),)

    assert VoteSessionDetail.from_domain(poll_snapshot(visibility=visibility, selections=selections)).voters is None


def test_visible_polls_name_each_voter_and_their_choice() -> None:
    selections = (
        VoteSelection(Voter.account(111), 99, "red", "1", 2.5, "Alex"),
        VoteSelection(Voter.account(112), 99, "red", "1", 1.0),
    )
    detail = VoteSessionDetail.from_domain(poll_snapshot(visibility=VoteVisibility.VISIBLE_LIVE, selections=selections))

    assert [voter.model_dump() for voter in detail.voters or ()] == [
        {"display_name": "Alex", "option_id": "red"},
        {"display_name": None, "option_id": "red"},
    ]


def test_build_reviews_never_name_their_voters() -> None:
    detail = VoteSessionDetail.from_domain(
        build_snapshot(selections=(VoteSelection(Voter.account(111), 10, "approve", "✅", 1.0),))
    )

    assert detail.voters is None


@pytest.mark.parametrize(
    ("session", "expected_kind", "expected_build_id", "expected_visibility"),
    [
        (build_snapshot(), VoteKind.BUILD, 42, None),
        (build_snapshot(kind=VoteKind.DELETE_LOG), VoteKind.DELETE_LOG, None, None),
        *[(poll_snapshot(visibility=visibility), VoteKind.GENERIC, None, visibility) for visibility in VoteVisibility],
    ],
    ids=["build", "delete-log", *[f"generic-{visibility.value}" for visibility in VoteVisibility]],
)
def test_vote_read_kind_visibility_target_matrix(
    session: VoteSessionSnapshot,
    expected_kind: VoteKind,
    expected_build_id: int | None,
    expected_visibility: VoteVisibility | None,
) -> None:
    """The REST projection preserves each valid discriminator without leaking other targets."""
    detail = VoteSessionDetail.from_domain(session)

    assert detail.kind is expected_kind
    assert detail.build_id == expected_build_id
    assert (detail.poll.visibility if detail.poll is not None else None) is expected_visibility


class TestTypedNotFound:
    """Missing resources retain their specific error code, resource name and identifier.

    The default transport fakes return a miss for each lookup used here.
    """

    def test_a_missing_tag_names_its_identifier(self, client: TestClient) -> None:
        response = client.get("/v1/tags/404")

        assert response.status_code == 404
        problem = response.json()
        assert problem["code"] == "TAG_NOT_FOUND"
        assert problem["resource"] == "tag"
        assert problem["context"] == {"tag_id": 404}

    def test_a_missing_tag_slug_names_the_slug(self, client: TestClient) -> None:
        response = client.get("/v1/tags/lookup", params={"slug": "zero-tick"})

        assert response.status_code == 404
        assert response.json()["code"] == "TAG_NOT_FOUND"

    def test_a_missing_vote_session_names_its_identifier(self, client: TestClient) -> None:
        response = client.get("/v1/vote-sessions/1234")

        assert response.status_code == 404
        problem = response.json()
        assert problem["code"] == "VOTE_SESSION_NOT_FOUND"
        assert problem["resource"] == "vote_session"
        assert problem["context"] == {"vote_session_id": 1234}

    def test_a_missing_account_names_its_public_identifier(self, client: TestClient) -> None:
        response = client.get(f"/v1/accounts/{CREATOR_PUBLIC_ID}")

        assert response.status_code == 404
        problem = response.json()
        assert problem["code"] == "ACCOUNT_NOT_FOUND"
        assert problem["resource"] == "account"
        assert problem["context"] == {"account_id": str(CREATOR_PUBLIC_ID)}

    def test_a_missing_record_names_its_identifier(self, client: TestClient) -> None:
        record_id = "00000000-0000-0000-0000-000000000077"
        response = client.get(f"/v1/records/{record_id}")

        assert response.status_code == 404
        problem = response.json()
        assert problem["code"] == "RECORD_NOT_FOUND"
        assert problem["resource"] == "record"
        assert problem["context"] == {"record_id": record_id}


def test_tag_moderation_requires_authentication(client: TestClient) -> None:
    assert client.get("/v1/tags/proposals").status_code == 401
    assert client.post("/v1/tags/proposals", json={"name": "Compact"}).status_code == 401
    for action in ("approve", "reject", "archive"):
        assert client.post(f"/v1/tags/proposals/1/{action}", json={"revision": 1}).status_code == 401
    assert client.post("/v1/tags/1/aliases", json={"alias": "Hip", "revision": 1}).status_code == 401
