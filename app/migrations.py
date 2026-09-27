"""数据结构版本管理：启动校验、幂等迁移、不兼容变更拒绝。

规则：
- 当前代码期望的 schema 版本为 SCHEMA_VERSION；
- 数据库版本 > SCHEMA_VERSION：库由更新版本代码创建，本代码无法安全读取
  → 拒绝启动（reason=db_newer_than_code）；
- 数据库版本 < SCHEMA_VERSION：按序执行缺失的迁移脚本，脚本可重复执行
  （幂等），重复运行不破坏已有数据；
- 迁移在单个事务内执行，失败整体回滚，不会留下半迁移状态。
"""
from __future__ import annotations

import sqlite3

from app.errors import MigrationError

SCHEMA_VERSION: int = 2

# 版本号 -> 迁移 SQL（幂等：使用 IF NOT EXISTS / 条件列添加）
MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        value INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    );
    """,
    2: """
    CREATE INDEX IF NOT EXISTS idx_items_name ON items (name, id);
    """,
}

_DDL_META = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def current_version(conn: sqlite3.Connection) -> int:
    """读取数据库当前版本；无版本表视为 0（全新库）。"""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if exists is None:
        return 0
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    return int(row["v"]) if row["v"] is not None else 0


def migrate(conn: sqlite3.Connection) -> None:
    """把数据库迁移到 SCHEMA_VERSION；幂等，可重复执行。"""
    verify_compatible(conn)
    version = current_version(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(_DDL_META)
        for v in range(version + 1, SCHEMA_VERSION + 1):
            script = MIGRATIONS.get(v)
            if script is None:
                raise MigrationError(
                    f"缺少版本 {v} 的迁移脚本",
                    detail={"reason": "missing_migration", "version": v},
                )
            # 逐条执行而非 executescript：后者会隐式提交，破坏事务回滚语义
            for stmt in script.split(";"):
                if stmt.strip():
                    conn.execute(stmt)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (v,)
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def verify_compatible(conn: sqlite3.Connection) -> None:
    """启动时校验：数据库版本不得高于代码支持的版本。"""
    version = current_version(conn)
    if version > SCHEMA_VERSION:
        raise MigrationError(
            f"数据库版本 {version} 高于代码支持的版本 {SCHEMA_VERSION}，拒绝启动",
            detail={
                "reason": "db_newer_than_code",
                "db_version": version,
                "code_version": SCHEMA_VERSION,
            },
        )
