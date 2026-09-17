"""Operator commands for the migration ledger that `alembic upgrade` verifies against.

`squid/persistence/migration_history.py` writes and checks the ledger; this is the two things
a person occasionally has to tell it, both of which are assertions only a human can make.

`adopt` claims that the revision scripts on disk are the ones an existing database already ran,
which is what gives a database older than the ledger any protection at all. `repair` claims that
an edit to an already-applied revision changed no DDL that database ran, and is how a deploy
blocked by the guard gets unblocked. Neither touches the schema; both change what the ledger
believes about it, so run them only on a database you have actually looked at.
"""

import argparse
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, make_url

from squid.config import load_database_config
from squid.persistence.migration_history import adopt_script_digests, repair_script_digests

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def adopt(connection: Connection, script_directory: ScriptDirectory) -> None:
    """Record the current digests for applied revisions the ledger has nothing on."""
    adopted = adopt_script_digests(connection, script_directory)
    if not adopted:
        print("Every applied revision already has a recorded digest; nothing to adopt.")
        return
    print(f"Adopted {len(adopted)} revision(s), from {adopted[0]} through {adopted[-1]}.")


def repair(connection: Connection, script_directory: ScriptDirectory) -> None:
    """Accept the current scripts for applied revisions whose files have been edited."""
    repaired = repair_script_digests(connection, script_directory)
    if not repaired:
        print("No applied revision has been edited; nothing to repair.")
        return
    for revision, (recorded, current) in sorted(repaired.items()):
        print(f"{revision}: {recorded[:12]} -> {current[:12]}")
    print(f"Accepted {len(repaired)} edited revision(s).")


def main(argv: list[str] | None = None) -> None:
    """Run the requested command against the configured database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("adopt", "repair"))
    command = {"adopt": adopt, "repair": repair}[parser.parse_args(argv).command]

    config = Config(str(PROJECT_ROOT / "alembic.ini"), toml_file=str(PROJECT_ROOT / "pyproject.toml"))
    script_directory = ScriptDirectory.from_config(config)
    url = make_url(load_database_config().url.get_secret_value()).set(drivername="postgresql+psycopg2")

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            command(connection, script_directory)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
