"""Record migration history and detect changes to applied revision files.

Alembic's version table records the current schema revision. This history table
also records how the database got there: each upgrade, downgrade, and stamp, with
its time and application release. A stamp records an asserted revision without
executing its migration script.

Each entry stores a digest of the revision file. A later migration run checks
digests for the current revision's ancestors and refuses files that have changed.
Tests on a fresh database cannot detect that mismatch: they run the edited file,
while an existing database still reflects the version it previously ran.

The Alembic callback writes rows inside the migration transaction. Failed
transactions roll back their history too; failures are recorded in logs.
"""

import hashlib
import socket
import time
from pathlib import Path
from typing import Any

from alembic.runtime.migration import MigrationContext, MigrationInfo
from alembic.script import Script, ScriptDirectory
from alembic.util import to_tuple
from alembic.util.exc import CommandError
from sqlalchemy import Connection, String, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY

from squid.config import load_build_config

HISTORY_TABLE = "alembic_migration_history"
"""Migration bookkeeping, maintained here rather than through model metadata.

`alembic/env.py` excludes this table and `alembic_version` from schema comparison.
"""

UNKNOWN_RELEASE = "unknown"
"""Recorded when the image did not supply a commit hash, e.g. a developer's local run."""


def release_version() -> str:
    """Return the build this process came from.

    `Dockerfile` sets `SQUID_BUILD_COMMIT_HASH` from the build's commit, so the migrate
    container attributes a migration to the same release the services report themselves as.
    A developer's local run supplies no commit and records `UNKNOWN_RELEASE`.
    """
    return load_build_config().commit_hash or UNKNOWN_RELEASE


_CREATE_TABLE = f"""
CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    revision varchar(64) NOT NULL,
    down_revisions varchar(64)[] NOT NULL,
    direction text NOT NULL CHECK (direction IN ('upgrade', 'downgrade', 'stamp')),
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    duration_ms integer NOT NULL,
    script_sha256 char(64),
    release_version text NOT NULL,
    applied_from text NOT NULL
)
"""

# The rest of the schema documents itself from model docstrings (`squid/persistence/base.py`).
# This table has no model to read them from, so its comments are written out by hand rather
# than left as the one undocumented table in the database.
_TABLE_COMMENTS = (
    f"COMMENT ON TABLE {HISTORY_TABLE} IS "
    "'Append-only history of upgrades, downgrades, and stamps. Entries commit with the "
    "migration transaction; stamps record revisions without executing their scripts.'",
    f"COMMENT ON COLUMN {HISTORY_TABLE}.revision IS 'The revision this step moved onto or off of.'",
    f"COMMENT ON COLUMN {HISTORY_TABLE}.down_revisions IS "
    "'Parents of `revision`; more than one only at a branch merge.'",
    f"COMMENT ON COLUMN {HISTORY_TABLE}.direction IS "
    "'upgrade and downgrade ran the script; stamp asserted the revision without running it.'",
    f"COMMENT ON COLUMN {HISTORY_TABLE}.applied_at IS "
    "'clock_timestamp(), not now(): a whole `upgrade head` shares one transaction, so "
    "transaction time would stamp every step of a release identically.'",
    f"COMMENT ON COLUMN {HISTORY_TABLE}.duration_ms IS "
    "'Wall time from the previous step of this run. The first row of a run also carries that "
    "run''s setup.'",
    f"COMMENT ON COLUMN {HISTORY_TABLE}.script_sha256 IS "
    "'Digest of the revision file for this step, checked while the revision is in the current schema ancestry. "
    "NULL when the revision resolved to no file on disk.'",
    f"COMMENT ON COLUMN {HISTORY_TABLE}.release_version IS 'SQUID_BUILD_COMMIT_HASH of the image that ran it.'",
    f"COMMENT ON COLUMN {HISTORY_TABLE}.applied_from IS "
    "'Hostname of the process that ran it, which is the container id under Compose.'",
)

_INSERT_STEP = text(
    f"INSERT INTO {HISTORY_TABLE} "
    "(revision, down_revisions, direction, duration_ms, script_sha256, release_version, applied_from) "
    "VALUES (:revision, :down_revisions, :direction, :duration_ms, :script_sha256, :release_version, :applied_from)"
).bindparams(bindparam("down_revisions", type_=ARRAY(String)))

_REPAIR_STEP = text(
    f"INSERT INTO {HISTORY_TABLE} "
    "(revision, down_revisions, direction, duration_ms, script_sha256, release_version, applied_from) "
    "SELECT revision, down_revisions, 'stamp', 0, :script_sha256, :release_version, :applied_from "
    f"FROM {HISTORY_TABLE} WHERE revision = :revision ORDER BY id DESC LIMIT 1"
)
"""Carries the parents forward from the step being repaired rather than re-deriving them.

A revision can only be repaired when it already has a row, so the SELECT always finds one.
"""

_LATEST_DIGESTS = text(
    f"SELECT DISTINCT ON (revision) revision, script_sha256 FROM {HISTORY_TABLE} "
    "WHERE script_sha256 IS NOT NULL ORDER BY revision, id DESC"
)
"""The most recent digest recorded for each revision, which is the one still in force."""


def create_history_table(connection: Connection) -> None:
    """Create the history table and refresh its database comments.

    Run before applying revisions so the first migration of a new database can
    be recorded too.
    """
    connection.execute(text(_CREATE_TABLE))
    for comment in _TABLE_COMMENTS:
        connection.execute(text(comment))


def script_digest(script: Script) -> str:
    """Return the SHA-256 digest of the complete revision file.

    Comments and formatting count as changes. After reviewing an edit, use
    `repair_script_digests` to accept its new digest.
    """
    return hashlib.sha256(Path(script.path).read_bytes()).hexdigest()


def recorded_digests(connection: Connection) -> dict[str, str]:
    """Read the digest currently in force for every revision this database has recorded."""
    return {row.revision: row.script_sha256 for row in connection.execute(_LATEST_DIGESTS)}


def _applied_scripts(connection: Connection, script_directory: ScriptDirectory) -> dict[str, Script]:
    """Return the scripts for revisions the database's current heads descend from.

    A revision that was applied and later downgraded no longer describes the current
    schema. This check therefore excludes its file when looking for edits that would
    make the recorded schema disagree with the scripts on disk.
    """
    heads = MigrationContext.configure(connection).get_current_heads()
    if not heads:
        return {}
    return {
        script.revision: script
        for script in script_directory.iterate_revisions(heads, "base")
        if isinstance(script, Script)
    }


def divergent_revisions(connection: Connection, script_directory: ScriptDirectory) -> dict[str, tuple[str, str]]:
    """Map each applied revision whose script changed to its (recorded, current) digests.

    A revision with no recorded digest is absent from the result rather than reported. This
    database may predate the ledger, or have been stamped past the revision, and neither is
    evidence of an edit -- absence means unknown, not divergence.
    """
    recorded = recorded_digests(connection)
    divergent: dict[str, tuple[str, str]] = {}
    for revision, script in _applied_scripts(connection, script_directory).items():
        expected = recorded.get(revision)
        if expected is None:
            continue
        current = script_digest(script)
        if current != expected:
            divergent[revision] = (expected, current)
    return divergent


def verify_script_digests(connection: Connection, script_directory: ScriptDirectory) -> None:
    """Refuse to migrate when an applied revision's script no longer matches what ran here.

    Raises `CommandError` so the Alembic CLI reports it as a failed command rather than a
    traceback, since the reader is whoever is running a deploy.
    """
    divergent = divergent_revisions(connection, script_directory)
    if not divergent:
        return
    detail = ", ".join(
        f"{revision} (recorded {expected[:12]}, now {current[:12]})"
        for revision, (expected, current) in sorted(divergent.items())
    )
    msg = (
        f"{len(divergent)} already-applied revision(s) have been edited since this database ran them: "
        f"{detail}. This database no longer matches the scripts that claim to describe it. Review the "
        f"edits and apply any missing change as a new revision. Use repair_script_digests() "
        f"to accept reviewed changes to the recorded digests."
    )
    raise CommandError(msg)


def repair_script_digests(connection: Connection, script_directory: ScriptDirectory) -> dict[str, tuple[str, str]]:
    """Accept changed scripts for applied revisions and return their old and new digests.

    Append a stamp row for each change, preserving the previous history. This does
    not apply SQL changes from the edited scripts; review those changes before
    accepting their digests.
    """
    divergent = divergent_revisions(connection, script_directory)
    release, applied_from = release_version(), socket.gethostname()
    for revision, (_recorded, current) in divergent.items():
        connection.execute(
            _REPAIR_STEP,
            {
                "revision": revision,
                "script_sha256": current,
                "release_version": release,
                "applied_from": applied_from,
            },
        )
    return divergent


def adopt_script_digests(connection: Connection, script_directory: ScriptDirectory) -> list[str]:
    """Record current digests for applied revisions that have none, and return their IDs.

    Use this for databases whose migrations predate the history table. The caller
    must first confirm that the scripts match the schema already deployed. This
    function records that claim; it neither verifies nor changes the schema.

    Existing digests are left unchanged. Each adopted revision receives a stamp
    row with zero duration.
    """
    recorded = recorded_digests(connection)
    release, applied_from = release_version(), socket.gethostname()
    adopted: list[str] = []
    for revision, script in _applied_scripts(connection, script_directory).items():
        if revision in recorded:
            continue
        connection.execute(
            _INSERT_STEP,
            {
                "revision": revision,
                "down_revisions": list(to_tuple(script.down_revision, default=())),
                "direction": "stamp",
                "duration_ms": 0,
                "script_sha256": script_digest(script),
                "release_version": release,
                "applied_from": applied_from,
            },
        )
        adopted.append(revision)
    return adopted


class MigrationStepRecorder:
    """Alembic `on_version_apply` callback that appends one ledger row per step.

    Holds a monotonic mark rather than timing each step from the inside, because the hook
    only fires after a step has finished. One instance covers one migration run; reusing it
    across runs would charge the gap between them to the next run's first step.
    """

    def __init__(self) -> None:
        self.release_version = release_version()
        self.applied_from = socket.gethostname()
        self._mark = time.monotonic()

    def __call__(
        self,
        *,
        ctx: MigrationContext,
        step: MigrationInfo,
        **_rest: Any,
    ) -> None:
        """Record a step after Alembic updates its version table, before commit.

        Accept unused callback keywords such as `heads` and `run_args`. Skip offline
        SQL generation, which has no database in which to record history.
        """
        now = time.monotonic()
        duration_ms = round((now - self._mark) * 1000)
        self._mark = now

        connection = ctx.connection
        if ctx.as_sql or connection is None:
            return  # Offline `--sql` mode has no database to read the ledger's prior state from.

        if step.is_stamp:
            direction = "stamp"
        else:
            direction = "upgrade" if step.is_upgrade else "downgrade"

        for revision_id in step.up_revision_ids:
            script = step.revision_map.get_revision(revision_id)
            connection.execute(
                _INSERT_STEP,
                {
                    "revision": revision_id,
                    "down_revisions": list(step.down_revision_ids),
                    "direction": direction,
                    "duration_ms": duration_ms,
                    "script_sha256": script_digest(script) if isinstance(script, Script) else None,
                    "release_version": self.release_version,
                    "applied_from": self.applied_from,
                },
            )
