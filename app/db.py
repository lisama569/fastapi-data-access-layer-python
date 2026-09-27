"""SQLite 连接管理：每个工作单元持有独立连接，保证线程安全。"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


def connect(db_path: str) -> sqlite3.Connection:
    """创建一条连接。

    - 每连接独立，供单个 UnitOfWork 独占使用，避免跨线程共享；
    - 关闭自动提交（isolation_level=""），事务边界完全由 UnitOfWork 控制；
    - 开启 WAL 与外键约束，提升并发读写下的可预期性。
    """
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def connection(db_path: str) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
