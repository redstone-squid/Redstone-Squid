"""Account deletion gives every foreign key to accounts a disposition.

The policy is in docs/plans/account-deletion.md. Deletion keeps a tombstone
account row, so these dispositions are carried out by code, not `ondelete` rules.
"""

from squid.core.persistence.account_merge import AccountReference
from squid.core.persistence.base import Base
from squid.core.persistence.model_registry import account_models


def test_every_account_foreign_key_has_an_explicit_deletion_disposition() -> None:
    erased = (
        AccountReference("account_identities", "account_id"),
        AccountReference("account_merge_tickets", "account_id"),
        AccountReference("account_profiles", "account_id"),
        AccountReference("api_keys", "owner_account_id"),
        AccountReference("catalogue_preferences", "account_id"),
        AccountReference("cli_device_enrollments", "approved_by_account_id"),
        AccountReference("cli_devices", "account_id"),
        AccountReference("creator_alias_claims", "account_id"),
        AccountReference("minecraft_paper_installations", "owner_account_id"),
        AccountReference("minecraft_player_challenges", "approved_by_account_id"),
        AccountReference("minecraft_player_grants", "account_id"),
        AccountReference("notification_deliveries", "account_id"),
        AccountReference("notification_profiles", "account_id"),
        AccountReference("notification_subscriptions", "account_id"),
        AccountReference("notifications", "account_id"),
        AccountReference("permission_grants", "subject_account_id"),
        AccountReference("permission_role_assignments", "subject_account_id"),
        AccountReference("submission_draft_editors", "account_id"),
        AccountReference("submission_inference_runs", "owner_account_id"),
        AccountReference("submission_schematic_sources", "owner_account_id"),
        AccountReference("submission_schematic_sources", "uploaded_by_account_id"),
        AccountReference("voter_guild_members", "account_id"),
        AccountReference("web_sessions", "account_id"),
    )
    withdrawn_while_pending = (
        AccountReference("build_submissions", "submitter_account_id"),
        AccountReference("builds", "submitter_account_id"),
        AccountReference("submission_drafts", "owner_account_id"),
        AccountReference("submission_revision_proposals", "owner_account_id"),
        AccountReference("submission_revision_proposals", "requested_by_account_id"),
    )
    kept_on_tombstone = (
        AccountReference("api_keys", "created_by_account_id"),
        AccountReference("build_schematics", "rights_attested_by_account_id"),
        AccountReference("build_schematics", "uploaded_by_account_id"),
        AccountReference("build_tag_assignments", "created_by_account_id"),
        AccountReference("creator_alias_claims", "resolved_by_account_id"),
        AccountReference("permission_audit_log", "actor_account_id"),
        AccountReference("permission_grants", "granted_by_account_id"),
        AccountReference("permission_role_assignments", "granted_by_account_id"),
        AccountReference("permission_role_includes", "added_by_account_id"),
        AccountReference("permission_role_patterns", "added_by_account_id"),
        AccountReference("permission_roles", "created_by_account_id"),
        AccountReference("public_account_redirects", "target_account_id"),
        AccountReference("runtime_setting_changes", "actor_account_id"),
        AccountReference("submission_draft_changes", "actor_account_id"),
        AccountReference("submission_drafts", "submission_actor_account_id"),
        AccountReference("submission_finalization_jobs", "requested_by_account_id"),
        AccountReference("submission_revision_proposals", "approved_by_account_id"),
        AccountReference("tag_definitions", "created_by_account_id"),
        AccountReference("vote_ballot_events", "account_id"),
        AccountReference("vote_sessions", "author_account_id"),
        AccountReference("votes", "account_id"),
    )
    # Unlinked either way; the creator chooses whether the credited name stays.
    creator_choice = (
        AccountReference("build_credits", "account_id"),
        AccountReference("build_credits", "proposed_account_id"),
        AccountReference("creator_aliases", "account_id"),
    )
    described = (*erased, *withdrawn_while_pending, *kept_on_tombstone, *creator_choice)
    actual = {
        AccountReference(table.name, foreign_key.parent.name)
        for table in Base.metadata.tables.values()
        for foreign_key in table.foreign_keys
        if foreign_key.column.table is account_models.Account.__table__
    }

    assert len(described) == len(set(described)), "each reference needs exactly one deletion disposition"
    assert set(described) == actual, "schema references and deletion dispositions must evolve together"
