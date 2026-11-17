"""Wake listeners accept only PostgreSQL connection URLs."""

import pytest
from pydantic import SecretStr
from sqlalchemy import make_url

from squid.persistence.wake_listener import asyncpg_dsn


@pytest.mark.parametrize("driver", ["postgresql", "postgres", "postgresql+asyncpg", "postgresql+psycopg"])
def test_dsn_preserves_connection_details(driver: str) -> None:
    result = make_url(asyncpg_dsn(SecretStr(f"{driver}://user:p%40ss@localhost:5433/squid?sslmode=require")))
    assert result.drivername == "postgresql"
    assert result.username == "user"
    assert result.password == "p@ss"
    assert result.port == 5433
    assert result.database == "squid"
    assert result.query == {"sslmode": "require"}


@pytest.mark.parametrize("url", ["sqlite:///squid.db", "mysql://localhost/squid"])
def test_rejects_other_backends(url: str) -> None:
    with pytest.raises(ValueError, match="PostgreSQL"):
        asyncpg_dsn(SecretStr(url))
