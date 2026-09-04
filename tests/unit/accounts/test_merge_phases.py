"""Static guarantees for the typed account-merge phase inventory."""

from collections.abc import Sequence
from typing import cast

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ClauseElement, Executable

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
    compiled = str(cast(ClauseElement, session.statements[0]).compile(dialect=postgresql.dialect()))
    assert compiled.startswith("UPDATE builds SET submitter_account_id=")
    assert "WHERE builds.submitter_account_id =" in compiled


def test_only_owner_precedence_retains_raw_postgres_with_a_specific_reason() -> None:
    assert repository._RETAINED_MERGE_SQL_REASONS == {
        repository._COLLAPSE_DRAFT_ACCESS_SQL: (
            "The winner depends on the draft's pre-merge owner while deleting one of two conflicting access rows; "
            "the joined CASE delete is clearer and safer than duplicating that precedence across correlated subqueries."
        )
    }
    statement = repository._COLLAPSE_DRAFT_ACCESS_SQL
    assert statement.startswith("\n")
    assert "\n" in statement.strip()
    assert ":survivor" in statement
    assert ":absorbed" in statement


@pytest.mark.parametrize(
    "builder",
    [
        repository._collapse_alias_claims_statement,
        repository._collapse_votes_statement,
        repository._merge_notification_profile_statement,
        repository._collapse_notification_subscriptions_statement,
        repository._merge_permission_effects_statement,
        repository._collapse_permission_grants_statement,
        repository._collapse_permission_role_assignments_statement,
    ],
)
def test_conflict_merge_statements_are_orm_backed_core(builder) -> None:
    statement = builder(repository._AccountMergeContext(survivor=7, absorbed=9))

    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert compiled
    assert "survivor" in compiled or "notification_profiles" in compiled or "permission_grants" in compiled
