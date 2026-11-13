import pytest
from litestar import Litestar
from litestar.testing import TestClient

from tests.unit.api.fakes import MockDatabaseManager, build_app


@pytest.fixture
def app_factory() -> tuple[Litestar, MockDatabaseManager]:
    return build_app()


@pytest.fixture
def client(app_factory: tuple[Litestar, MockDatabaseManager]):
    app, database = app_factory
    # Enter the lifespan so cleanup runs and the fake database closure can be asserted.
    with TestClient(app) as c:
        yield c
    assert database.closed
