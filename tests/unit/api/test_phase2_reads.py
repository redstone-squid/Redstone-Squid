"""Focused contracts for the remaining public read resources."""

from collections.abc import Sequence
from types import SimpleNamespace
from typing import cast, override
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from squid.accounts.application import AccountService
from squid.accounts.domain import CreatorAlias, CreditedAlias, PublicCreatorProfile
from squid.api.dependencies import get_services
from squid.builds.application import BuildQueryService
from squid.builds.domain import Build, DoorBuild, Status
from squid.runtime import ApiServices
from squid.schematics.application import RenderedSchematic, RenderRequest, RenderSkipReason, SchematicService
from squid.schematics.errors import SchematicNotFoundError, SchematicRenderRefusedError
from squid.tags.application import TagService
from squid.tags.domain import (
    TagAuthority,
    TagDefinition,
    TagModerationStatus,
    TagSemanticKind,
    TagValueType,
)
from squid.versions.application.services import VersionService
from squid.versions.domain import MinecraftVersion
from squid.voting.application import VoteService
from squid.voting.domain import VoteChoice, VoteOption, VoteSelection, VoteSessionSnapshot, VoteVisibility
from tests.support.voting import poll_snapshot
from tests.unit.api.fakes import MockDatabaseManager

CREATOR_PUBLIC_ID = UUID("22222222-2222-2222-2222-222222222222")
RENDER_RECIPE_HASH = "b" * 64

# Each fake subclasses the service it replaces and marks its methods `@override`, so a
# renamed method or a changed return type fails the type check here instead of leaving
# these route tests green against a contract the routes no longer have. None of them run
# the real `__init__`: the fakes replace every method a route reaches, so a service's
# repositories are exactly what they must not have.


class PublicTagFake(TagService):
    def __init__(self, definition: TagDefinition) -> None:
        self.definition = definition

    @override
    async def public_definitions(self) -> Sequence[TagDefinition]:
        return (self.definition,)

    @override
    async def public_definition(self, tag_id: int) -> TagDefinition | None:
        return self.definition if tag_id == self.definition.id else None


class VersionFake(VersionService):
    def __init__(self) -> None:
        pass

    @override
    async def list_all(self) -> list[MinecraftVersion]:
        return [MinecraftVersion("Java", 1, 21, 5), MinecraftVersion("Bedrock", 1, 21, 50)]


class AccountFake(AccountService):
    def __init__(self) -> None:
        pass

    @override
    async def get_creator_alias(self, name: str) -> CreatorAlias | None:
        if name.casefold() != "builder":
            return None
        return CreatorAlias(7, "Builder", account_id=42, public_creator_id=CREATOR_PUBLIC_ID)

    @override
    async def get_public_profile(self, public_id: UUID) -> PublicCreatorProfile | None:
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
    async def render_content(self, recipe_hash: str, *, max_bytes: int = 8 * 1024 * 1024) -> bytes:
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
    async def render_now(self, build_id: int, *, request: RenderRequest | None = None) -> RenderedSchematic:
        reason = RenderSkipReason.OVER_VOLUME_BUDGET
        raise SchematicRenderRefusedError(reason.value, reason.description)


class ConfirmedBuildFake(BuildQueryService):
    def __init__(self) -> None:
        pass

    @override
    async def get_public(self, build_id: int) -> Build:
        return DoorBuild(
            id=build_id,
            submitter_account_id=1,
            submission_status=Status.CONFIRMED,
            versions=["1.21"],
            door_width=2,
            door_height=2,
            patterns=["Regular"],
            orientation="Door",
        )


class VoteFake(VoteService):
    def __init__(self, session: VoteSessionSnapshot) -> None:
        self.session = session

    @override
    async def get_session_by_id(self, vote_session_id: int) -> VoteSessionSnapshot | None:
        return self.session if vote_session_id == self.session.id else None


def _override(app: FastAPI, **services: object) -> None:
    """Install partial services for the routes under test.

    `ApiServices` has two dozen required capabilities and a route reaches one of them, so
    the namespace stays partial; the fakes above are where the shape is actually checked.
    """
    fake_services = cast(ApiServices, SimpleNamespace(**services))
    app.dependency_overrides[get_services] = lambda: fake_services


def test_tag_and_version_collections_use_page_envelope(
    app_factory: tuple[FastAPI, MockDatabaseManager],
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
    _override(app, tags=PublicTagFake(definition), versions=VersionFake())

    with TestClient(app) as client:
        tags = client.get("/v1/tags")
        versions = client.get("/v1/versions")

    assert tags.status_code == 200
    assert tags.json()["items"] == [
        {
            "id": 4,
            "key": "official_seamless",
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
    app_factory: tuple[FastAPI, MockDatabaseManager],
) -> None:
    app, _database = app_factory
    _override(app, accounts=AccountFake())

    with TestClient(app) as client:
        response = client.get("/v1/creator-aliases/builder")

    assert response.status_code == 200
    assert response.json() == {
        "name": "Builder",
        "claimed": True,
        "creator_id": "22222222-2222-2222-2222-222222222222",
    }
    assert "user_id" not in response.text


def test_creator_profile_groups_public_aliases(app_factory: tuple[FastAPI, MockDatabaseManager]) -> None:
    app, _database = app_factory
    _override(app, accounts=AccountFake())

    with TestClient(app) as client:
        response = client.get("/v1/creators/22222222-2222-2222-2222-222222222222")

    assert response.status_code == 200
    assert response.json() == {
        "id": "22222222-2222-2222-2222-222222222222",
        "canonical_id": None,
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
    app_factory: tuple[FastAPI, MockDatabaseManager],
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
    app_factory: tuple[FastAPI, MockDatabaseManager],
) -> None:
    app, _database = app_factory
    _override(app, build_queries=ConfirmedBuildFake(), schematics=RefusingRenderFake())

    with TestClient(app) as client:
        response = client.get("/v1/builds/7/schematics/render", params={"pitch": 120})

    assert response.status_code == 422


def test_schematic_render_content_is_immutable_png(app_factory: tuple[FastAPI, MockDatabaseManager]) -> None:
    app, _database = app_factory
    _override(app, schematics=SchematicFake())

    with TestClient(app) as client:
        response = client.get(f"/v1/schematic-renders/{RENDER_RECIPE_HASH}/content")

    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n\x1a\npreview"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_hidden_vote_session_omits_ballots_and_live_tallies(
    app_factory: tuple[FastAPI, MockDatabaseManager],
) -> None:
    app, _database = app_factory
    session = poll_snapshot(
        id=9,
        author_account_id=123,
        question="Pick a color",
        visibility=VoteVisibility.ANONYMOUS_HIDDEN,
        guild_id=99,
        options=(VoteOption("1", VoteChoice.GENERIC, identifier="red", label="Red"),),
        selections=(VoteSelection(111, 99, "red", "1", 2.5),),
    )
    _override(app, votes=VoteFake(session))

    with TestClient(app) as client:
        response = client.get("/v1/vote-sessions/9")

    assert response.status_code == 200
    payload = response.json()
    assert payload["tallies"] is None
    assert payload["options"] == [{"id": "red", "label": "Red", "choice": "generic", "position": 0}]
    assert "111" not in response.text
    assert "author_id" not in response.text


class TestTypedNotFound:
    """Each missing resource names itself, rather than sharing one bare
    `NotFoundError` carrying a `resource=` string.

    The default app's fakes miss on every lookup, which is exactly the case
    under test.
    """

    def test_a_missing_tag_names_its_identifier(self, client: TestClient) -> None:
        response = client.get("/v1/tags/404")

        assert response.status_code == 404
        problem = response.json()
        assert problem["code"] == "TAG_NOT_FOUND"
        assert problem["resource"] == "tag"
        assert problem["context"] == {"tag_id": 404}

    def test_a_missing_vote_session_names_its_identifier(self, client: TestClient) -> None:
        response = client.get("/v1/vote-sessions/1234")

        assert response.status_code == 404
        problem = response.json()
        assert problem["code"] == "VOTE_SESSION_NOT_FOUND"
        assert problem["resource"] == "vote_session"
        assert problem["context"] == {"vote_session_id": 1234}

    def test_a_missing_creator_profile_names_its_identifier(self, client: TestClient) -> None:
        response = client.get(f"/v1/creators/{CREATOR_PUBLIC_ID}")

        assert response.status_code == 404
        problem = response.json()
        assert problem["code"] == "CREATOR_NOT_FOUND"
        assert problem["resource"] == "creator"
        assert problem["context"] == {"creator_id": str(CREATOR_PUBLIC_ID)}

    def test_a_missing_record_names_its_identifier(self, client: TestClient) -> None:
        response = client.get("/v1/records/77")

        assert response.status_code == 404
        problem = response.json()
        assert problem["code"] == "RECORD_NOT_FOUND"
        assert problem["resource"] == "record"
        assert problem["context"] == {"record_id": 77}
