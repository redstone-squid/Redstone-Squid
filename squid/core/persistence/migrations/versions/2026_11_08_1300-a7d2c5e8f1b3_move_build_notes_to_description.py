"""Move submitter notes from extra_info.user into the description column.

Builds used to store the submitter's note twice: in `description` and in `extra_info.user`. Most
readers preferred `description`, but the Discord review card read only `extra_info.user`. After
this migration `description` is the only copy.

When both copies exist and disagree, `description` wins, because every reader except the review
card already preferred it. An empty string counts as missing on both sides.

The search projection and Discord-sync triggers would otherwise queue work for every migrated
build. Search text does not change, because search already preferred `description`. The review
card changes only where it used to show a note that differs from the merged description. So the
first pass, for builds whose card is unchanged, runs with both triggers disabled. The second
pass, for the rest, leaves them enabled so those cards are re-rendered.

Retained inference candidates in `submission_inference_runs` get the same rewrite, so reopening
one after the deploy still offers its note.
"""

from alembic import op

revision: str = "a7d2c5e8f1b3"
down_revision: str | None = "f3c6b9e4a1d7"
branch_labels: str | None = None
depends_on: str | None = None

_QUEUE_TRIGGERS = ("builds_enqueue_search_on_update", "builds_enqueue_discord_sync_on_update")

_MERGED = "COALESCE(NULLIF(description, ''), NULLIF(extra_info->>'user', ''))"


def _set_queue_triggers(enabled: bool) -> None:
    action = "ENABLE" if enabled else "DISABLE"
    for trigger in _QUEUE_TRIGGERS:
        op.execute(f"ALTER TABLE public.builds {action} TRIGGER {trigger}")


def upgrade() -> None:
    _set_queue_triggers(enabled=False)
    op.execute(
        f"""
        UPDATE public.builds
        SET description = {_MERGED}, extra_info = extra_info - 'user'
        WHERE extra_info ? 'user'
          AND {_MERGED} IS NOT DISTINCT FROM NULLIF(extra_info->>'user', '')
        """
    )
    _set_queue_triggers(enabled=True)
    op.execute(
        f"""
        UPDATE public.builds
        SET description = {_MERGED}, extra_info = extra_info - 'user'
        WHERE extra_info ? 'user'
        """
    )

    op.execute(
        """
        UPDATE public.submission_inference_runs AS run
        SET candidates = (
            SELECT jsonb_agg(
                CASE
                    WHEN item.candidate->'extra_info' ? 'user' THEN
                        jsonb_set(item.candidate, '{extra_info}', (item.candidate->'extra_info') - 'user')
                        || jsonb_build_object(
                            'description',
                            COALESCE(
                                NULLIF(NULLIF(item.candidate->'description', 'null'::jsonb), '""'::jsonb),
                                item.candidate->'extra_info'->'user'
                            )
                        )
                    ELSE item.candidate
                END
                ORDER BY item.position
            )
            FROM jsonb_array_elements(run.candidates) WITH ORDINALITY AS item(candidate, position)
        )
        WHERE jsonb_path_exists(run.candidates, '$[*].extra_info.user')
        """
    )


def downgrade() -> None:
    # The previous code wrote the note to both places, so restoring the copy changes no rendered output.
    _set_queue_triggers(enabled=False)
    op.execute(
        """
        UPDATE public.builds
        SET extra_info = extra_info || jsonb_build_object('user', description)
        WHERE description IS NOT NULL
        """
    )
    _set_queue_triggers(enabled=True)

    op.execute(
        """
        UPDATE public.submission_inference_runs AS run
        SET candidates = (
            SELECT jsonb_agg(
                CASE
                    WHEN jsonb_typeof(item.candidate->'description') = 'string' THEN
                        jsonb_set(item.candidate, '{extra_info,user}', item.candidate->'description')
                    ELSE item.candidate
                END
                ORDER BY item.position
            )
            FROM jsonb_array_elements(run.candidates) WITH ORDINALITY AS item(candidate, position)
        )
        WHERE jsonb_path_exists(run.candidates, '$[*].description ? (@.type() == "string")')
        """
    )
