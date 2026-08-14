"""Disposable PostgreSQL roles, template reset, and deterministic API seed data."""

import hashlib
import hmac
import json
import re
import secrets
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import connection as PsycopgConnection
from sqlalchemy import URL

from tests.fuzz.api.environment import RunIdentity, SeededIds, SyntheticSecrets, UnsafeEnvironmentError

POSTGRES_USER = "postgres"
POSTGRES_DATABASE = "postgres"
SEED_BUILDER_VERSION = "api-seed-v1"
SEED_BUILDER_HASH = hashlib.sha256(SEED_BUILDER_VERSION.encode()).hexdigest()
DATABASE_STARTUP_SECONDS = 30
MIGRATION_SECONDS = 180
_SAFE_IDENTITY = re.compile(r"[0-9a-f]{32}")
_ROOT = Path(__file__).resolve().parents[3]

ALICE_ACCOUNT_ID = 1_001
BOB_ACCOUNT_ID = 1_002
CONSENT_PENDING_ACCOUNT_ID = 1_003
ADMINISTRATOR_ACCOUNT_ID = 1_004
JAVA_VERSION_ID = 1
ALICE_PUBLIC_ID = "00000000-0000-0000-0000-000000001001"
BOB_PUBLIC_ID = "00000000-0000-0000-0000-000000001002"


@dataclass(frozen=True, slots=True)
class DatabaseCredentials:
    """Synthetic role passwords kept out of representations and process inheritance."""

    administrator_password: str = field(repr=False)
    migrator_password: str = field(repr=False)
    application_password: str = field(repr=False)
    observer_password: str = field(repr=False)

    @classmethod
    def generate(cls) -> "DatabaseCredentials":
        """Generate coordinator-owned passwords independent of container-visible identity."""
        return cls(
            administrator_password=secrets.token_urlsafe(32),
            migrator_password=secrets.token_urlsafe(32),
            application_password=secrets.token_urlsafe(32),
            observer_password=secrets.token_urlsafe(32),
        )


@dataclass(frozen=True, slots=True)
class DatabaseLocation:
    """Coordinator and container-network locations for one PostgreSQL service."""

    coordinator_host: str
    coordinator_port: int
    container_host: str
    container_port: int = 5432


class DatabaseController:
    """Own the narrow privileged database operations for one attested fuzz run."""

    def __init__(
        self,
        identity: RunIdentity,
        location: DatabaseLocation,
        credentials: DatabaseCredentials,
        secrets_: SyntheticSecrets,
    ) -> None:
        _validate_identity(identity)
        self.identity = identity
        self.location = location
        self.credentials = credentials
        self.secrets = secrets_

    @property
    def administrator_url(self) -> str:
        """Return the coordinator URL for PostgreSQL bootstrap operations."""
        return self._url(
            POSTGRES_USER,
            self.credentials.administrator_password,
            POSTGRES_DATABASE,
            host=self.location.coordinator_host,
            port=self.location.coordinator_port,
        )

    @property
    def application_container_url(self) -> str:
        """Return the application-role URL reachable only on the Docker network."""
        return self._url(
            self.identity.application_role,
            self.credentials.application_password,
            self.identity.database_name,
            host=self.location.container_host,
            port=self.location.container_port,
        )

    @property
    def observer_url(self) -> str:
        """Return the coordinator URL for narrow invariant queries."""
        return self._url(
            self.identity.observer_role,
            self.credentials.observer_password,
            self.identity.database_name,
            host=self.location.coordinator_host,
            port=self.location.coordinator_port,
        )

    def bootstrap(self) -> tuple[SeededIds, str]:
        """Create roles, migrate a sealed template, clone it, and seed the active database."""
        with (
            closing(self._connect_admin(POSTGRES_DATABASE, autocommit=True)) as connection,
            connection.cursor() as cursor,
        ):
            self._create_roles(cursor)
            cursor.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(self.identity.template_database_name),
                    sql.Identifier(self.identity.migrator_role),
                )
            )
        with (
            closing(self._connect_admin(self.identity.template_database_name, autocommit=True)) as connection,
            connection.cursor() as cursor,
        ):
            for extension in ("vector", "unaccent", "pg_trgm"):
                cursor.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(extension)))
        self._migrate_template()
        self._install_control_sentinel()
        with (
            closing(self._connect_admin(POSTGRES_DATABASE, autocommit=True)) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                sql.SQL("ALTER DATABASE {} IS_TEMPLATE true").format(
                    sql.Identifier(self.identity.template_database_name)
                )
            )
            cursor.execute(
                sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS false").format(
                    sql.Identifier(self.identity.template_database_name)
                )
            )
            self._clone_active_database(cursor)
        self._configure_active_database()
        seeded_ids = self.seed()
        return seeded_ids, self.checksum()

    def wait_until_ready(self) -> None:
        """Wait for the final PostgreSQL server, not its temporary initialization server."""
        deadline = time.monotonic() + DATABASE_STARTUP_SECONDS
        while time.monotonic() < deadline:
            try:
                with (
                    closing(self._connect_admin(POSTGRES_DATABASE, autocommit=True)) as connection,
                    connection.cursor() as cursor,
                ):
                    cursor.execute("SELECT 1")
                    if cursor.fetchone() == (1,):
                        return
            except psycopg2.OperationalError:
                time.sleep(0.1)
        msg = "Disposable PostgreSQL server did not become ready."
        raise TimeoutError(msg)

    def reset_database(self) -> None:
        """Replace only this run's attested active database from its sealed template."""
        self.verify()
        with (
            closing(self._connect_admin(POSTGRES_DATABASE, autocommit=True)) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "SELECT datistemplate, datallowconn, pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s",
                (self.identity.template_database_name,),
            )
            template = cursor.fetchone()
            if template != (True, False, self.identity.migrator_role):
                msg = "Disposable PostgreSQL template attestation failed."
                raise UnsafeEnvironmentError(msg)
            cursor.execute(
                "SELECT pid, usename, application_name FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (self.identity.database_name,),
            )
            sessions = cursor.fetchall()
            unsafe_sessions = [
                row for row in sessions if row[1:] != (self.identity.application_role, self.identity.application_name)
            ]
            if unsafe_sessions:
                msg = "Disposable PostgreSQL database has an unexpected live session."
                raise UnsafeEnvironmentError(msg)
            for pid, _role, _application_name in sessions:
                cursor.execute("SELECT pg_terminate_backend(%s)", (pid,))
                if cursor.fetchone() != (True,):
                    msg = "Disposable PostgreSQL application session could not be terminated."
                    raise UnsafeEnvironmentError(msg)
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (self.identity.database_name,),
            )
            if cursor.fetchone() != (0,):
                msg = "Disposable PostgreSQL database did not quiesce before reset."
                raise UnsafeEnvironmentError(msg)
            cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(self.identity.database_name)))
            self._clone_active_database(cursor)
        self._configure_active_database()

    def seed(self) -> SeededIds:
        """Insert the same bounded personas and identifiers into a fresh active database."""
        ids = SeededIds(
            alice_account_id=ALICE_ACCOUNT_ID,
            bob_account_id=BOB_ACCOUNT_ID,
            consent_pending_account_id=CONSENT_PENDING_ACCOUNT_ID,
            administrator_account_id=ADMINISTRATOR_ACCOUNT_ID,
            java_version_id=JAVA_VERSION_ID,
            alice_public_id=ALICE_PUBLIC_ID,
            bob_public_id=BOB_PUBLIC_ID,
            alice_web_session=self.secrets.alice_web_session,
            bob_web_session=self.secrets.bob_web_session,
            consent_pending_web_session=self.secrets.consent_pending_web_session,
            administrator_web_session=self.secrets.administrator_web_session,
            service_api_token=self.secrets.service_api_token,
        )
        web_sessions = (
            ("00000000-0000-0000-0000-000000002001", ids.alice_web_session, ALICE_ACCOUNT_ID, 1001),
            ("00000000-0000-0000-0000-000000002002", ids.bob_web_session, BOB_ACCOUNT_ID, 1002),
            (
                "00000000-0000-0000-0000-000000002003",
                ids.consent_pending_web_session,
                CONSENT_PENDING_ACCOUNT_ID,
                1003,
            ),
            (
                "00000000-0000-0000-0000-000000002004",
                ids.administrator_web_session,
                ADMINISTRATOR_ACCOUNT_ID,
                1004,
            ),
        )
        with self._connect_role(
            self.identity.migrator_role,
            self.credentials.migrator_password,
            self.identity.database_name,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO accounts (id, public_creator_id, created_at, consent_version, consented_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        (
                            ALICE_ACCOUNT_ID,
                            ALICE_PUBLIC_ID,
                            "2026-01-01T00:00:00Z",
                            "2026-08-04",
                            "2026-08-04T00:00:00Z",
                        ),
                        (BOB_ACCOUNT_ID, BOB_PUBLIC_ID, "2026-01-01T00:00:00Z", "2026-08-04", "2026-08-04T00:00:00Z"),
                        (
                            CONSENT_PENDING_ACCOUNT_ID,
                            "00000000-0000-0000-0000-000000001003",
                            "2026-08-05T00:00:00Z",
                            None,
                            None,
                        ),
                        (
                            ADMINISTRATOR_ACCOUNT_ID,
                            "00000000-0000-0000-0000-000000001004",
                            "2026-01-01T00:00:00Z",
                            "2026-08-04",
                            "2026-08-04T00:00:00Z",
                        ),
                    ),
                )
                cursor.executemany(
                    "INSERT INTO account_identities "
                    "(id, account_id, provider, subject, display_name, verified_at, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, '2026-08-04T00:00:00Z', '2026-08-04T00:00:00Z')",
                    (
                        (2_001, ALICE_ACCOUNT_ID, "discord", "1001", "FuzzAlice"),
                        (2_002, ALICE_ACCOUNT_ID, "java", "00000000-0000-0000-0000-000000000101", "FuzzAlice"),
                        (2_003, BOB_ACCOUNT_ID, "discord", "1002", "FuzzBob"),
                        (2_004, BOB_ACCOUNT_ID, "java", "00000000-0000-0000-0000-000000000102", "FuzzBob"),
                        (2_005, CONSENT_PENDING_ACCOUNT_ID, "discord", "1003", "FuzzPending"),
                        (2_006, ADMINISTRATOR_ACCOUNT_ID, "discord", "1004", "FuzzAdmin"),
                    ),
                )
                cursor.execute(
                    "INSERT INTO versions (id, edition, major_version, minor_version, patch_number, data_version) "
                    "VALUES (%s, 'Java', 1, 21, 0, 3953)",
                    (JAVA_VERSION_ID,),
                )
                cursor.execute(
                    "INSERT INTO global_administrators (account_id, granted_by_account_id, granted_at) "
                    "VALUES (%s, %s, '2026-08-04T00:00:00Z')",
                    (ADMINISTRATOR_ACCOUNT_ID, ADMINISTRATOR_ACCOUNT_ID),
                )
                cursor.executemany(
                    "INSERT INTO web_sessions "
                    "(id, token_hash, account_id, discord_id, created_at, expires_at, last_seen_at, user_agent) "
                    "VALUES (%s, %s, %s, %s, '2026-08-04T00:00:00Z', '2100-01-01T00:00:00Z', "
                    "'2026-08-04T00:00:00Z', 'api-fuzzer')",
                    (
                        (
                            session_id,
                            hmac.digest(self.secrets.session_pepper.encode(), token.encode(), hashlib.sha256),
                            account_id,
                            discord_id,
                        )
                        for session_id, token, account_id, discord_id in web_sessions
                    ),
                )
                cursor.execute(
                    "INSERT INTO api_keys "
                    "(id, key_id, secret_hash, label, scopes, owner_account_id, created_by_account_id, created_at) "
                    "VALUES (2001, %s, %s, 'API fuzz service', %s, %s, %s, '2026-08-04T00:00:00Z')",
                    (
                        self.secrets.service_api_key_id,
                        hmac.digest(
                            self.secrets.api_key_pepper.encode(),
                            self.secrets.service_api_key_secret.encode(),
                            hashlib.sha256,
                        ),
                        [
                            "build.submission.read",
                            "build.submission.create",
                            "account.self.read",
                            "account.verify.relay",
                            "vote.poll.cast",
                        ],
                        ADMINISTRATOR_ACCOUNT_ID,
                        ADMINISTRATOR_ACCOUNT_ID,
                    ),
                )
                for table, value in (
                    ("accounts", ADMINISTRATOR_ACCOUNT_ID),
                    ("account_identities", 2_006),
                    ("api_keys", 2_001),
                    ("versions", JAVA_VERSION_ID),
                ):
                    cursor.execute(
                        "SELECT setval(pg_get_serial_sequence(%s, 'id'), %s, true)",
                        (table, value),
                    )
            connection.commit()
        return ids

    def checksum(self) -> str:
        """Hash a narrow ordered baseline without creating a second database read model."""
        statements = (
            "SELECT id, public_creator_id::text, consent_version FROM accounts ORDER BY id",
            "SELECT id, account_id, provider, subject FROM account_identities ORDER BY id",
            "SELECT id, edition, major_version, minor_version, patch_number, data_version FROM versions ORDER BY id",
            "SELECT account_id, granted_by_account_id FROM global_administrators ORDER BY account_id",
            "SELECT id::text, account_id, discord_id FROM web_sessions ORDER BY id",
            "SELECT key_id, scopes, owner_account_id FROM api_keys ORDER BY key_id",
            "SELECT (SELECT count(*) FROM verification_codes), "
            "(SELECT count(*) FROM submission_drafts), (SELECT count(*) FROM idempotency_requests), "
            "(SELECT count(*) FROM oauth_states)",
            "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'public' "
            "AND sequencename IN ('accounts_id_seq', 'account_identities_id_seq', 'api_keys_id_seq', "
            "'versions_id_seq') ORDER BY sequencename",
            "SELECT version_num FROM alembic_version ORDER BY version_num",
        )
        query_results: list[list[object]] = []
        with (
            self._connect_role(
                self.identity.observer_role,
                self.credentials.observer_password,
                self.identity.database_name,
            ) as connection,
            connection.cursor() as cursor,
        ):
            for statement in statements:
                cursor.execute(statement)
                query_results.append([list(row) for row in cursor.fetchall()])
        document = {"seed_builder_hash": SEED_BUILDER_HASH, "query_results": query_results}
        encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(encoded).hexdigest()

    def verify(self) -> tuple[str, str]:
        """Read back the control sentinel and application role setting."""
        with (
            self._connect_role(
                self.identity.observer_role,
                self.credentials.observer_password,
                self.identity.database_name,
            ) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "SELECT sentinel, database_name, template_database_name, application_name "
                "FROM fuzz_control.sentinel WHERE run_id = %s",
                (self.identity.run_id,),
            )
            row = cursor.fetchone()
        expected = (
            self.identity.sentinel,
            self.identity.database_name,
            self.identity.template_database_name,
            self.identity.application_name,
        )
        if row is None or row[1:] != expected[1:] or not hmac.compare_digest(row[0], expected[0]):
            msg = "Disposable PostgreSQL sentinel attestation failed."
            raise UnsafeEnvironmentError(msg)
        with (
            self._connect_role(
                self.identity.application_role,
                self.credentials.application_password,
                self.identity.database_name,
            ) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT current_database(), current_user, current_setting('application_name')")
            application = cursor.fetchone()
        if application != (
            self.identity.database_name,
            self.identity.application_role,
            self.identity.application_name,
        ):
            msg = "Disposable PostgreSQL application-role attestation failed."
            raise UnsafeEnvironmentError(msg)
        return row[0], self.identity.application_name

    def verify_live_application_sessions(self, expected_client_address: str) -> None:
        """Require live API sessions to use the exact role, database, name, and network address."""
        with (
            closing(self._connect_admin(POSTGRES_DATABASE, autocommit=True)) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "SELECT usename, application_name, client_addr::text FROM pg_stat_activity "
                "WHERE datname = %s AND backend_type = 'client backend' AND pid <> pg_backend_pid()",
                (self.identity.database_name,),
            )
            sessions = cursor.fetchall()
        expected = (self.identity.application_role, self.identity.application_name, expected_client_address)
        if not sessions or any(session != expected for session in sessions):
            msg = "Disposable PostgreSQL live application-session attestation failed."
            raise UnsafeEnvironmentError(msg)

    def observer_cannot_write(self) -> bool:
        """Return whether the observer role is denied application-table mutation."""
        with self._connect_role(
            self.identity.observer_role,
            self.credentials.observer_password,
            self.identity.database_name,
        ) as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("INSERT INTO accounts DEFAULT VALUES")
            except psycopg2.errors.InsufficientPrivilege:
                connection.rollback()
                return True
            connection.rollback()
            return False

    def verification_code_count(self) -> int:
        """Return the one mutable count used by the lifecycle integration check."""
        with (
            self._connect_role(
                self.identity.observer_role,
                self.credentials.observer_password,
                self.identity.database_name,
            ) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT count(*) FROM verification_codes")
            row = cursor.fetchone()
        if row is None or not isinstance(row[0], int):
            msg = "Disposable PostgreSQL verification-code invariant could not be read."
            raise UnsafeEnvironmentError(msg)
        return row[0]

    def _create_roles(self, cursor: psycopg2.extensions.cursor) -> None:
        roles = (
            (self.identity.migrator_role, self.credentials.migrator_password, "CREATEDB"),
            (self.identity.application_role, self.credentials.application_password, "NOCREATEDB"),
            (self.identity.observer_role, self.credentials.observer_password, "NOCREATEDB"),
        )
        for role, password, database_privilege in roles:
            cursor.execute(
                sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER {} NOCREATEROLE NOREPLICATION PASSWORD {}").format(
                    sql.Identifier(role),
                    sql.SQL(database_privilege),
                    sql.Literal(password),
                )
            )
        cursor.execute(sql.SQL("GRANT pg_signal_backend TO {}").format(sql.Identifier(self.identity.migrator_role)))

    def _migrate_template(self) -> None:
        migration_url = self._url(
            self.identity.migrator_role,
            self.credentials.migrator_password,
            self.identity.template_database_name,
            host=self.location.coordinator_host,
            port=self.location.coordinator_port,
        )
        environment = {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(_ROOT),
            "PYTHONUTF8": "1",
            "SQUID_DATABASE_URL": migration_url,
        }
        with TemporaryDirectory(prefix="squid-api-fuzz-migration-") as working_directory:
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", str(_ROOT / "alembic.ini"), "upgrade", "head"],
                cwd=working_directory,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=MIGRATION_SECONDS,
                check=False,
            )
        if result.returncode != 0:
            msg = "Disposable PostgreSQL template migration failed."
            raise RuntimeError(msg)

    def _install_control_sentinel(self) -> None:
        with self._connect_role(
            self.identity.migrator_role,
            self.credentials.migrator_password,
            self.identity.template_database_name,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("CREATE SCHEMA fuzz_control")
                cursor.execute(
                    "CREATE TABLE fuzz_control.sentinel ("
                    "run_id text PRIMARY KEY, sentinel text NOT NULL, database_name text NOT NULL, "
                    "template_database_name text NOT NULL, application_name text NOT NULL)"
                )
                cursor.execute(
                    "INSERT INTO fuzz_control.sentinel VALUES (%s, %s, %s, %s, %s)",
                    (
                        self.identity.run_id,
                        self.identity.sentinel,
                        self.identity.database_name,
                        self.identity.template_database_name,
                        self.identity.application_name,
                    ),
                )
                cursor.execute("REVOKE ALL ON SCHEMA fuzz_control FROM PUBLIC")
                cursor.execute("REVOKE ALL ON ALL TABLES IN SCHEMA fuzz_control FROM PUBLIC")
                cursor.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA fuzz_control TO {}").format(
                        sql.Identifier(self.identity.observer_role)
                    )
                )
                cursor.execute(
                    sql.SQL("GRANT SELECT ON fuzz_control.sentinel TO {}").format(
                        sql.Identifier(self.identity.observer_role)
                    )
                )
            connection.commit()

    def _clone_active_database(self, cursor: psycopg2.extensions.cursor) -> None:
        cursor.execute(
            sql.SQL("CREATE DATABASE {} WITH TEMPLATE {} OWNER {}").format(
                sql.Identifier(self.identity.database_name),
                sql.Identifier(self.identity.template_database_name),
                sql.Identifier(self.identity.migrator_role),
            )
        )

    def _configure_active_database(self) -> None:
        with (
            closing(self._connect_admin(POSTGRES_DATABASE, autocommit=True)) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(self.identity.database_name))
            )
            for role in (
                self.identity.migrator_role,
                self.identity.application_role,
                self.identity.observer_role,
            ):
                cursor.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(self.identity.database_name), sql.Identifier(role)
                    )
                )
            cursor.execute(
                sql.SQL("ALTER ROLE {} IN DATABASE {} SET application_name TO {}").format(
                    sql.Identifier(self.identity.application_role),
                    sql.Identifier(self.identity.database_name),
                    sql.Literal(self.identity.application_name),
                )
            )
        with self._connect_admin(self.identity.database_name, autocommit=False) as connection:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
                cursor.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC")
                cursor.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC")
                cursor.execute("REVOKE ALL ON ALL ROUTINES IN SCHEMA public FROM PUBLIC")
                cursor.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA public TO {}, {}").format(
                        sql.Identifier(self.identity.application_role),
                        sql.Identifier(self.identity.observer_role),
                    )
                )
                cursor.execute(
                    sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(
                        sql.Identifier(self.identity.application_role)
                    )
                )
                cursor.execute(
                    sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                        sql.Identifier(self.identity.application_role)
                    )
                )
                cursor.execute(
                    sql.SQL("GRANT EXECUTE ON ALL ROUTINES IN SCHEMA public TO {}").format(
                        sql.Identifier(self.identity.application_role)
                    )
                )
                cursor.execute(
                    sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(
                        sql.Identifier(self.identity.observer_role)
                    )
                )
                cursor.execute(
                    sql.SQL("GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                        sql.Identifier(self.identity.observer_role)
                    )
                )
            connection.commit()

    def _connect_admin(self, database: str, *, autocommit: bool) -> PsycopgConnection:
        connection = psycopg2.connect(
            self._url(
                POSTGRES_USER,
                self.credentials.administrator_password,
                database,
                host=self.location.coordinator_host,
                port=self.location.coordinator_port,
            ),
            connect_timeout=2,
        )
        connection.autocommit = autocommit
        return connection

    def _connect_role(self, role: str, password: str, database: str) -> PsycopgConnection:
        return psycopg2.connect(
            self._url(
                role,
                password,
                database,
                host=self.location.coordinator_host,
                port=self.location.coordinator_port,
            ),
            connect_timeout=2,
        )

    @staticmethod
    def _url(user: str, password: str, database: str, *, host: str, port: int) -> str:
        return URL.create(
            "postgresql",
            username=user,
            password=password,
            host=host,
            port=port,
            database=database,
        ).render_as_string(hide_password=False)


def _validate_identity(identity: RunIdentity) -> None:
    if _SAFE_IDENTITY.fullmatch(identity.run_id) is None:
        msg = "Disposable PostgreSQL run IDs must be 128-bit lowercase hexadecimal values."
        raise UnsafeEnvironmentError(msg)
    if identity.database_name != f"squid_fuzz_{identity.run_id}":
        msg = "Disposable PostgreSQL active database name does not match its run ID."
        raise UnsafeEnvironmentError(msg)
    if identity.template_database_name != f"squid_fuzz_template_{identity.run_id}":
        msg = "Disposable PostgreSQL template database name does not match its run ID."
        raise UnsafeEnvironmentError(msg)
