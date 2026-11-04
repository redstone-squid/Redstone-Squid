import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.unit.api.fakes import MockDatabaseManager, build_app


@pytest.fixture
def app_factory() -> tuple[FastAPI, MockDatabaseManager]:
    return build_app()


@pytest.fixture
def client(app_factory: tuple[FastAPI, MockDatabaseManager]):
    app, database = app_factory
    # Enter the lifespan so cleanup runs and the fake database closure can be asserted.
    with TestClient(app) as c:
        yield c
    assert database.closed
