"""pytest 公共夹具：临时数据库与测试客户端。"""
from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.db import connection
from app.migrations import migrate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@pytest.fixture()
def db_path(tmp_path) -> str:
    path = str(tmp_path / "test.db")
    with connection(path) as conn:
        migrate(conn)
    return path


@pytest.fixture()
def client(db_path) -> Iterator[TestClient]:
    with TestClient(create_app(db_path)) as c:
        yield c
