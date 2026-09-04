"""Static guarantees for the typed account-merge phase inventory."""

from collections.abc import Sequence
from typing import cast

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Executable

from squid.accounts.infrastructure import repository


class CapturingSession:
    def __init__(self) -> None:
        self.statements: list[Executable] = []

    async def execute(self, statement: Executable, parameters: object = None) -> None:
        del parameters
        self.statements.append(statement)


def test_each_routine_account_reference_has_one_phase_owner() -> None:
    phases: Sequence[Sequence[repository._AccountReference]] = (
        repository._IDENTITY_PROFILE_REFERENCES,
        repository._SUBMISSION_REFERENCES,
        repository._CREATOR_CREDIT_REFERENCES,
        repository._VOTING_REFERENCES,
        repository._NOTIFICATION_REFERENCES,
        repository._PERMISSION_PROVENANCE_REFERENCES,
    )
    references = tuple((reference.table_name, reference.column_name) for phase in phases for reference in phase)

    assert len(references) == len(set(references))
    assert ("submission_drafts", "owner_account_id") in references
    assert ("account_identities", "account_id") in references
    assert ("permission_grants", "granted_by_account_id") in references


@pytest.mark.asyncio
async def test_routine_reference_moves_compile_as_core_updates() -> None:
    session = CapturingSession()
    await repository._move_account_reference(
        cast(AsyncSession, session),
        repository._AccountReference("builds", "submitter_account_id"),
        repository._AccountMergeContext(survivor=7, absorbed=9),
    )

    assert len(session.statements) == 1
    compiled = str(session.statements[0].compile(dialect=postgresql.dialect()))
    assert compiled.startswith("UPDATE builds SET submitter_account_id=")
    assert "WHERE builds.submitter_account_id =" in compiled


def test_irreducible_postgres_merge_statements_are_named_multiline_contracts() -> None:
    statements = (
        repository._COLLAPSE_DRAFT_ACCESS_SQL,
        repository._COLLAPSE_ALIAS_CLAIMS_SQL,
        repository._COLLAPSE_VOTES_SQL,
        repository._MERGE_NOTIFICATION_PROFILE_SQL,
        repository._COLLAPSE_NOTIFICATION_SUBSCRIPTIONS_SQL,
        repository._MERGE_PERMISSION_EFFECTS_SQL,
        repository._COLLAPSE_PERMISSION_GRANTS_SQL,
        repository._COLLAPSE_PERMISSION_ROLE_ASSIGNMENTS_SQL,
    )

    assert all(statement.startswith("\n") and "\n" in statement.strip() for statement in statements)
    assert all(":survivor" in statement and ":absorbed" in statement for statement in statements)
