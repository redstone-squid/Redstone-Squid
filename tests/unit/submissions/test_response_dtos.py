"""Submission attachment DTOs expose only the owner-visible source metadata."""

from uuid import UUID

from httpx import AsyncClient
from litestar import Litestar, Response, get

from squid.api.v1.submission_intake import SuppliedFileDTO
from squid.api.v1.submission_schematics import DraftSchematicDTO
from squid.submissions.application.intake import SuppliedAttachment
from squid.submissions.domain.intake import IntakeStatus
from squid.submissions.domain.schematics import DraftSchematic, DraftSchematicState
from squid.submissions.domain.source_files import SubmissionSourceFile
from tests.support.litestar import asgi_transport

SOURCE_ID = UUID("6d9a5b5f-4b5b-4b31-b148-e56d478ca862")


async def test_supplied_file_dto_omits_private_provenance_and_internal_flags() -> None:
    source = SuppliedAttachment(
        SOURCE_ID,
        "door.schem",
        "schematic",
        IntakeStatus.FAILED,
        source=SubmissionSourceFile(SOURCE_ID, "door.schem", None, "https://private.invalid/token", 123),
        history_managed=True,
    )

    @get("/", return_dto=SuppliedFileDTO)
    async def endpoint() -> Response[list[SuppliedAttachment]]:
        return Response([source], headers={"ETag": '"draft-r2"'})

    app = Litestar(route_handlers=[endpoint])
    async with AsyncClient(transport=asgi_transport(app), base_url="http://test") as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert response.headers["ETag"] == '"draft-r2"'
    assert response.json() == [
        {"id": str(SOURCE_ID), "filename": "door.schem", "kind": "schematic", "status": "failed"}
    ]
    schema = app.openapi_schema.to_schema()["components"]["schemas"]["SuppliedFileResponse"]
    assert set(schema["properties"]) == {"id", "filename", "kind", "status"}


async def test_schematic_dto_preserves_source_metadata_without_storage_path() -> None:
    source = DraftSchematic(
        SOURCE_ID, "door.schem", "a" * 64, 123, DraftSchematicState.READY, primary=True, discarded=False
    )

    @get("/", return_dto=DraftSchematicDTO)
    async def endpoint() -> list[DraftSchematic]:
        return [source]

    app = Litestar(route_handlers=[endpoint])
    async with AsyncClient(transport=asgi_transport(app), base_url="http://test") as client:
        response = await client.get("/")
    expected = {
        "id": str(SOURCE_ID),
        "filename": "door.schem",
        "sha256": "a" * 64,
        "byte_size": 123,
        "state": "ready",
        "primary": True,
        "discarded": False,
    }
    assert response.status_code == 200
    assert response.json() == [expected]
    schema = app.openapi_schema.to_schema()["components"]["schemas"]["DraftSchematicResponse"]
    assert set(schema["properties"]) == set(expected)
