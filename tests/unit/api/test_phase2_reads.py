"""Focused contracts for the remaining public read resources."""

from types import SimpleNamespace
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from whenever import Instant

from squid.api.dependencies import get_services
from squid.runtime import ApplicationServices
from squid.tags.domain import (
    TagAuthority,
    TagDefinition,
    TagModerationStatus,
    TagSemanticKind,
    TagValueType,
)
from squid.users.domain import CreatorAlias
from squid.versions.domain import MinecraftVersion
from squid.voting.domain import GenericPoll, VoteChoice, VoteOption, VoteSelection, VoteSessionSnapshot, VoteTarget
from tests.unit.api.fakes import MockDatabaseManager


class PublicTagFake:
    def __init__(self, definition: TagDefinition) -> None:
        self.definition = definition

    async def public_definitions(self):
        return (self.definition,)

    async def public_definition(self, tag_id: int):
        return self.definition if tag_id == self.definition.id else None


class VersionFake:
    async def list_all(self):
        return [MinecraftVersion("Java", 1, 21, 5), MinecraftVersion("Bedrock", 1, 21, 50)]


class UserFake:
    async def get_creator_alias(self, name: str):
        return CreatorAlias(7, "Builder", user_id=42) if name.casefold() == "builder" else None


class SchematicFake:
    async def content(self, sha256: str):
        return b"schematic-data" if sha256 == "a" * 64 else None


class VoteFake:
    def __init__(self, session: VoteSessionSnapshot) -> None:
        self.session = session

    async def get_session_by_id(self, vote_session_id: int):
        return self.session if vote_session_id == self.session.id else None


def _override(app: FastAPI, **services: object) -> None:
    fake_services = cast(ApplicationServices, SimpleNamespace(**services))
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
            "numeric_quantum": None,
        }
    ]
    assert tags.json()["has_more"] is False
    assert [item["display_name"] for item in versions.json()["items"]] == ["Java 1.21.5", "Bedrock 1.21.50"]


def test_creator_alias_never_exposes_linked_account(
    app_factory: tuple[FastAPI, MockDatabaseManager],
) -> None:
    app, _database = app_factory
    _override(app, users=UserFake())

    with TestClient(app) as client:
        response = client.get("/v1/creator-aliases/builder")

    assert response.status_code == 200
    assert response.json() == {"name": "Builder", "claimed": True}
    assert "user_id" not in response.text


def test_schematic_content_is_an_attachment(app_factory: tuple[FastAPI, MockDatabaseManager]) -> None:
    app, _database = app_factory
    _override(app, schematics=SchematicFake())

    with TestClient(app) as client:
        response = client.get(f"/v1/schematics/{'a' * 64}/content")

    assert response.status_code == 200
    assert response.content == b"schematic-data"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == f'attachment; filename="{"a" * 64}.schematic"'


def test_hidden_vote_session_omits_ballots_and_live_tallies(
    app_factory: tuple[FastAPI, MockDatabaseManager],
) -> None:
    app, _database = app_factory
    session = VoteSessionSnapshot(
        id=9,
        author_id=123,
        kind="generic",
        status="open",
        result="pending",
        pass_threshold=1,
        fail_threshold=1,
        votes={111: 2.5},
        messages=(),
        options=(VoteOption("1", VoteChoice.GENERIC, identifier="red", label="Red"),),
        target=VoteTarget(),
        selections=(VoteSelection(111, 99, "red", "1", 2.5),),
        poll=GenericPoll("Pick a color", "anonymous_hidden", 99, Instant.now().add(hours=1)),
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
