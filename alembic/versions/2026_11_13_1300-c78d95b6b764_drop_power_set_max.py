"""Drop the unused power_set_max function.

It enumerated tag subsets for the record cache that the baseline replaced. Nothing calls it now.
"""

from alembic_utils.pg_function import PGFunction

from alembic import op

revision: str = "c78d95b6b764"
down_revision: str | None = "b67c84a5a653"
branch_labels: str | None = None
depends_on: str | None = None

_POWER_SET_MAX = "CREATE FUNCTION public.power_set_max(txt text[], max_k integer DEFAULT 8) RETURNS SETOF text[]\n    LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE\n    AS $$\nDECLARE\n    n     int := array_length(txt, 1);\n    mask  int;\nBEGIN\n    IF n IS NULL OR n = 0 THEN\n        RETURN NEXT ARRAY[]::text[];\n        RETURN;\n    END IF;\n\n    FOR mask IN 0 .. (1 << n) - 1 LOOP\n        -- skip masks with more than max_k bits set\n        IF (\n             SELECT COUNT(*)\n             FROM   generate_series(0, n - 1) g\n             WHERE  ((mask >> g) & 1) = 1\n           ) > max_k THEN\n            CONTINUE;\n        END IF;\n\n        RETURN NEXT coalesce(                      -- fall back to empty array\n            (SELECT array_agg(txt[i] ORDER BY i)\n             FROM generate_subscripts(txt, 1) AS i\n             WHERE (mask >> (i - 1)) & 1 = 1),\n            ARRAY[]::text[]\n        );\n    END LOOP;\nEND;\n$$;"


def upgrade() -> None:
    op.drop_entity(PGFunction.from_sql(_POWER_SET_MAX))


def downgrade() -> None:
    op.create_entity(PGFunction.from_sql(_POWER_SET_MAX))
