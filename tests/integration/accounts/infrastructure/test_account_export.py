"""Account ownership and catalogue export contracts against the migrated schema."""

import csv
import json
from io import BytesIO, StringIO
from unittest.mock import Mock
from zipfile import ZipFile

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.accounts.infrastructure.export import AccountExports
from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, UserActor
from squid.core.builds.application.queries import BuildListSort
from squid.core.builds.domain import BuildListFilters, BuildStatus
from squid.core.builds.models import Build, OtherBuild
from squid.core.catalogue_exports import CatalogueExports
from squid.core.exports import ExportFormat
from squid.core.records.application.models import ALL_RECORDS, RecordListSort
from squid.core.search.domain import SearchRequest, SearchScope
from squid.core.submissions.models.reviews import BuildSubmission
from tests.support.build_edits import build_edit_session
from tests.support.creator_credits import publish_credits


async def test_download_membership_and_file_formats(migrated_session_factory: async_sessionmaker[AsyncSession]) -> None:
    sessions = migrated_session_factory
    async with sessions.begin() as session:
        creator = Account()
        session.add(creator)
        await session.flush()
        creator_id = creator.id
    first, second = await publish_credits(sessions, ["Red", "Squid"], owner=creator_id)
    async with sessions.begin() as session:
        owner = await session.scalar(select(Build.submitter_account_id).where(Build.id == first.build_id))
        assert owner is not None
        submission = await session.scalar(select(BuildSubmission).where(BuildSubmission.build_id == first.build_id))
        assert submission is not None
        submission.content = {"description": "Submitted value", "private_installation": "sensitive"}

    accounts = AccountExports(sessions)
    owner_json = json.loads((await accounts.download(owner, ExportFormat.JSON)).content)["data"]
    assert owner_json["submissions"][0]["content"] == {"description": "Submitted value"}
    assert owner_json["credits"] == []
    creator_json = json.loads((await accounts.download(creator_id, ExportFormat.JSON)).content)
    archive = await accounts.download(creator_id, ExportFormat.CSV)
    with ZipFile(BytesIO(archive.content)) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["tables"] == creator_json["manifest"]["tables"]
        for name in ("credits", "submissions", "submitted_builds"):
            rows = list(csv.DictReader(StringIO(bundle.read(name + ".csv").decode()), quoting=csv.QUOTE_NOTNULL))
            assert rows == creator_json["data"][name]
    assert [credit["name"] for credit in creator_json["data"]["credits"]] == [first.name, second.name]
    assert creator_json["data"]["submissions"] == creator_json["data"]["submitted_builds"] == []

    catalogue = CatalogueExports(sessions, Mock(), Mock())
    actor = UserActor(account_id=owner)
    context = ActingContext(operator=actor, effective=actor)
    async with build_edit_session(sessions) as session:
        more = [
            OtherBuild(submission_status=BuildStatus.APPROVED, submitter_account_id=owner, ai_generated=False)
            for _ in range(100)
        ]
        session.add_all(more)
        await session.flush()
        expected_ids = [str(build.id) for build in reversed(more)] + [str(first.build_id)]
    build_file = await catalogue.builds(
        ExportFormat.CSV,
        statuses=frozenset({BuildStatus.APPROVED}),
        submitter_account_id=owner,
        filters=BuildListFilters(),
        sort=BuildListSort.ID,
        context=context,
    )
    assert build_file.extension == "csv"
    assert [row["id"] for row in csv.DictReader(StringIO(build_file.content.decode()))] == expected_ids
    records = await catalogue.records(ExportFormat.JSON, filters=ALL_RECORDS, sort=RecordListSort.ID, context=context)
    assert "records" in json.loads(records.content)["data"]
    search = await catalogue.search(
        ExportFormat.JSON, SearchRequest(query="absent", scope=SearchScope.ALL), context=context
    )
    assert json.loads(search.content)["data"]["search"] == []
