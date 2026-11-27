"""Version catalogue corrections over HTTP."""

from typing import Any, override

from litestar.testing import TestClient

from squid.core.actors import ActingContext
from squid.core.errors import ErrorCode
from squid.core.versions.application.services import VersionService
from squid.core.versions.domain import MinecraftVersion, VersionCorrection, VersionEntry
from squid.core.versions.errors import VersionInUseError
from tests.unit.api.fakes import TEST_CONFIG, TEST_SYNERGY_SECRET, build_app

EDITOR_CONFIG = TEST_CONFIG.model_copy(
    update={"api": TEST_CONFIG.api.model_copy(update={"secret_nodes": ["version.entry.edit", "version.entry.delete"]})}
)
AUTHORIZED = {"Authorization": TEST_SYNERGY_SECRET}


class RecordingVersions(VersionService):
    def __init__(self) -> None:
        self.corrections: list[VersionCorrection] = []

    @override
    async def correct(self, version_id: int, correction: VersionCorrection, *, context: ActingContext) -> VersionEntry:
        self.corrections.append(correction)
        return correction.apply(VersionEntry(version_id, MinecraftVersion("Java", 1, 21, 4), data_version=4189))

    @override
    async def delete(self, version_id: int, *, context: ActingContext) -> None:
        raise VersionInUseError(version_id)


def client(versions: VersionService) -> Any:
    app, _database = build_app(versions=versions, config=EDITOR_CONFIG)
    return TestClient(app, raise_server_exceptions=False)


def test_a_correction_clears_data_version_only_when_null_is_sent() -> None:
    versions = RecordingVersions()

    with client(versions) as http:
        kept = http.patch("/v1/versions/3", json={"edition": "Bedrock"}, headers=AUTHORIZED)
        cleared = http.patch("/v1/versions/3", json={"data_version": None}, headers=AUTHORIZED)

    assert kept.status_code == 200
    assert kept.json() | {"display_name": None} == {
        "id": 3,
        "edition": "Bedrock",
        "major": 1,
        "minor": 21,
        "patch": 4,
        "display_name": None,
        "data_version": 4189,
    }
    assert cleared.json()["data_version"] is None


def test_deleting_a_version_in_use_is_a_conflict() -> None:
    with client(RecordingVersions()) as http:
        response = http.delete("/v1/versions/3", headers=AUTHORIZED)

    assert response.status_code == 409
    assert response.json()["code"] == ErrorCode.VERSION_IN_USE


def test_corrections_need_the_edit_node() -> None:
    with client(RecordingVersions()) as http:
        response = http.patch("/v1/versions/3", json={"edition": "Bedrock"})

    assert response.status_code == 401
