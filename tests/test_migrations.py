"""迁移测试：版本校验、幂等性与不兼容变更拒绝。"""
from __future__ import annotations

import logging

import pytest

import app.migrations as m
from app.db import connection
from app.errors import MigrationError

logger = logging.getLogger("test.migrations")


def test_fresh_db_migrates_to_current_version(tmp_path):
    path = str(tmp_path / "fresh.db")
    with connection(path) as conn:
        assert m.current_version(conn) == 0
        m.migrate(conn)
        version = m.current_version(conn)
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    logger.info(
        "输入: 全新数据库; 判定依据: 版本=%d 且 items/schema_migrations 表存在; "
        "实际: version=%d tables=%s",
        m.SCHEMA_VERSION,
        version,
        sorted(tables),
    )
    assert version == m.SCHEMA_VERSION
    assert {"items", "schema_migrations"} <= tables


def test_migrate_is_idempotent_and_preserves_data(tmp_path):
    path = str(tmp_path / "idem.db")
    with connection(path) as conn:
        m.migrate(conn)
        conn.execute("INSERT INTO items (name, value, version) VALUES ('keep', 7, 1)")
        m.migrate(conn)  # 重复执行
        m.migrate(conn)
        rows = conn.execute("SELECT name, value FROM items").fetchall()
        version = m.current_version(conn)
    logger.info(
        "输入: 迁移后插入数据再重复迁移两次; 判定依据: 数据保留且版本不变; "
        "实际: rows=%s version=%d",
        [tuple(r) for r in rows],
        version,
    )
    assert [tuple(r) for r in rows] == [("keep", 7)]
    assert version == m.SCHEMA_VERSION


def test_newer_db_version_rejected_with_distinguishable_reason(tmp_path):
    path = str(tmp_path / "newer.db")
    with connection(path) as conn:
        m.migrate(conn)
        conn.execute(
            "INSERT INTO schema_migrations (version) VALUES (?)", (m.SCHEMA_VERSION + 1,)
        )
        with pytest.raises(MigrationError) as exc_info:
            m.verify_compatible(conn)
    detail = exc_info.value.detail
    logger.info(
        "输入: 数据库版本 %d > 代码版本 %d; 判定依据: MigrationError 且 "
        "reason=db_newer_than_code; 实际: code=%s detail=%s",
        m.SCHEMA_VERSION + 1,
        m.SCHEMA_VERSION,
        exc_info.value.code,
        detail,
    )
    assert exc_info.value.code == "migration_error"
    assert detail["reason"] == "db_newer_than_code"
    assert detail["db_version"] == m.SCHEMA_VERSION + 1


def test_failed_migration_rolls_back_completely(tmp_path, monkeypatch):
    """迁移脚本中途失败：整个迁移回滚，不留半迁移状态，可修复后重试。"""
    path = str(tmp_path / "fail.db")
    broken = dict(m.MIGRATIONS)
    broken[m.SCHEMA_VERSION + 1] = "CREATE TABLE t_ok (id INTEGER); THIS IS INVALID SQL;"
    monkeypatch.setattr(m, "MIGRATIONS", broken)
    monkeypatch.setattr(m, "SCHEMA_VERSION", m.SCHEMA_VERSION + 1)

    with connection(path) as conn:
        with pytest.raises(Exception):
            m.migrate(conn)
        version = m.current_version(conn)
        leftover = conn.execute(
            "SELECT count(*) AS c FROM sqlite_master WHERE name='t_ok'"
        ).fetchone()["c"]
    logger.info(
        "输入: 包含非法 SQL 的迁移脚本; 判定依据: 版本仍为 0 且 t_ok 表不存在; "
        "实际: version=%d leftover=%d",
        version,
        leftover,
    )
    assert version == 0
    assert leftover == 0


def test_missing_migration_script_rejected(tmp_path, monkeypatch):
    """版本链中存在空洞（缺少某版本脚本）时给出可区分原因。"""
    path = str(tmp_path / "gap.db")
    gapped = dict(m.MIGRATIONS)
    del gapped[2]
    monkeypatch.setattr(m, "MIGRATIONS", gapped)

    with connection(path) as conn:
        with pytest.raises(MigrationError) as exc_info:
            m.migrate(conn)
    logger.info(
        "输入: 缺少版本 2 的迁移脚本; 判定依据: reason=missing_migration; "
        "实际: detail=%s",
        exc_info.value.detail,
    )
    assert exc_info.value.detail["reason"] == "missing_migration"
    assert exc_info.value.detail["version"] == 2
