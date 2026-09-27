"""数据结构版本管理与本地持久化复现测试。"""

from __future__ import annotations

import json
import os

import pytest

from app.errors import MigrationError
from app.migrations import SCHEMA_VERSION, run_migrations
from app.storage import Storage
from tests.conftest import log_case


def test_fresh_storage_is_at_latest_version():
    state = Storage(None).snapshot()
    assert state["meta"]["schema_version"] == SCHEMA_VERSION
    # 重复执行迁移为 no-op，不破坏空数据
    again = run_migrations(state, log=lambda *a: None)
    assert again["items"] == {} and again["next_id"] == 1
    log_case("fresh-latest", version=SCHEMA_VERSION)


def test_migrate_v0_to_latest_and_idempotent():
    legacy = {"items": {"1": {"id": 1, "name": "old", "version": 1,
                              "created_at": "t", "updated_at": "t"}},
              "next_id": 2}  # 无 meta → 视为 v0
    migrated = run_migrations(legacy, log=lambda *a: None)
    assert migrated["meta"]["schema_version"] == SCHEMA_VERSION
    assert 1 in migrated["items"]

    # 再次执行不得重复改写或破坏数据
    second = run_migrations(migrated, log=lambda *a: None)
    assert second["meta"]["schema_version"] == SCHEMA_VERSION
    assert second["items"][1]["name"] == "old"
    assert second["next_id"] == 2
    log_case("migrate-then-rerun", version=second["meta"]["schema_version"])


def test_downgrade_is_rejected_with_distinct_reason():
    future = {"items": {}, "next_id": 1,
              "meta": {"schema_version": SCHEMA_VERSION + 10}}
    with pytest.raises(MigrationError) as exc:
        run_migrations(future, log=lambda *a: None)
    assert exc.value.error_type == "migration_error"
    assert exc.value.details == {"installed": SCHEMA_VERSION + 10,
                                 "code": SCHEMA_VERSION}
    log_case("downgrade-rejected", details=exc.value.details)


def test_incompatible_data_is_rejected():
    bad = {"items": ["not", "a", "dict"], "next_id": 1}
    with pytest.raises(MigrationError) as exc:
        run_migrations(bad, log=lambda *a: None)
    assert "不兼容" in exc.value.message
    log_case("incompatible-rejected", msg=exc.value.message)


def test_data_file_roundtrip_and_repeat_start(tmp_path):
    """写入数据 -> 落盘 -> 用同一路径重新构造 Storage，数据完整、迁移幂等。"""
    path = str(tmp_path / "data.json")
    s1 = Storage(path)
    with __import__("app.unitofwork", fromlist=["UnitOfWork"]).UnitOfWork(s1).transaction(
        log=lambda *a: None
    ) as tx:
        tx.items.add("persisted", log=lambda *a: None)

    assert os.path.exists(path)
    on_disk = json.load(open(path, encoding="utf-8"))
    assert on_disk["meta"]["schema_version"] == SCHEMA_VERSION

    s2 = Storage(path)  # 模拟重启：从磁盘加载并跑启动迁移
    migrated = run_migrations(s2.snapshot(), log=lambda *a: None)
    assert migrated["items"][1]["name"] == "persisted"
    log_case("restart-roundtrip", item=migrated["items"][1])


def test_startup_runs_migrations_via_lifespan(tmp_path):
    path = str(tmp_path / "data.json")
    # 预置一个 v0 旧文件
    json.dump({"items": {}, "next_id": 1}, open(path, "w", encoding="utf-8"))
    from fastapi.testclient import TestClient

    from app.api import create_app

    app = create_app(storage=Storage(path))
    with TestClient(app):  # lifespan 启动迁移不报错即通过
        pass
    assert json.load(open(path, encoding="utf-8"))["meta"]["schema_version"] == SCHEMA_VERSION
    log_case("lifespan-migrated", version=SCHEMA_VERSION)
