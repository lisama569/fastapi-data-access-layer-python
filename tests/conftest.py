"""公共 pytest fixtures。"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.storage import Storage


@pytest.fixture
def client():
    app = create_app(storage=Storage(None))  # 纯内存，测试间互不干扰
    with TestClient(app) as c:
        yield c


def log_case(title: str, **kwargs) -> None:
    """打印关键输入与判定依据，便于在单测日志中追溯。"""
    parts = " ".join(f"{k}={v!r}" for k, v in kwargs.items())
    logging.getLogger("tests").warning("CASE %s | %s", title, parts)
