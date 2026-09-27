"""工作单元：界定事务边界，同一事务内多次写入要么全部生效、要么全部回滚。"""
from __future__ import annotations

import sqlite3
from types import TracebackType

from app.db import connect


class UnitOfWork:
    """用法::

        with UnitOfWork(db_path) as uow:
            repo = ItemRepository(uow.conn)
            repo.create(...)
            repo.create(...)
            uow.commit()   # 全部生效；未 commit 或抛异常则全部回滚
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._done = False  # 已 commit 或 rollback

    def __enter__(self) -> "UnitOfWork":
        self._conn = connect(self._db_path)
        self._conn.execute("BEGIN IMMEDIATE")  # 立即获取写锁，写事务串行化
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        try:
            if exc_type is None and self._done:
                pass  # 已显式 commit
            else:
                self.rollback()  # 异常或未显式提交：全部回滚
        finally:
            assert self._conn is not None
            self._conn.close()
            self._conn = None
        return False  # 不吞掉异常

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("UnitOfWork 尚未进入上下文或已退出")
        return self._conn

    def commit(self) -> None:
        self.conn.commit()
        self._done = True

    def rollback(self) -> None:
        if self._conn is not None and not self._done:
            self._conn.rollback()
            self._done = True
