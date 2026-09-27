"""事务边界（Unit of Work）。

约定：
- ``with uow.transaction() as tx:`` 进入时获取全局写锁并对当前状态做深拷贝，
  事务内的读写都作用于这份工作副本，因此事务内可以读到自己的未提交写入。
- 正常退出 with 块：工作副本原子替换正式状态并落盘 —— 多次写入全部生效。
- with 块内抛异常：工作副本被直接丢弃、正式状态不变 —— 全部回滚。
- 写操作在无活动事务时会被拒绝（TransactionInactiveError），避免半事务写入。
- 全局写锁（RLock）使事务严格串行化，从根上消除丢失更新；事务状态保存在
  线程本地存储中，多线程可共享同一个 UoW 而互不串扰，同一线程内仍禁止嵌套。
"""

from __future__ import annotations

import copy
import threading
from contextlib import contextmanager
from typing import Iterator

from app.errors import AppError
from app.repository import ItemRepository
from app.storage import Storage


class TransactionInactiveError(AppError):
    error_type = "transaction_inactive"
    http_status = 500


class _TLS(threading.local):
    def __init__(self) -> None:
        self.state: dict | None = None
        self.items: ItemRepository | None = None


class UnitOfWork:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._tls = _TLS()

    @property
    def active(self) -> bool:
        return self._tls.state is not None

    @property
    def state(self) -> dict:
        """当前事务的工作副本；无活动事务时抛错。"""
        if self._tls.state is None:
            raise TransactionInactiveError("当前没有活动事务，写操作必须在事务内执行")
        return self._tls.state

    @contextmanager
    def transaction(self, *, log=print) -> "Iterator[UnitOfWork]":
        lock = self._storage.lock
        lock.acquire()  # 事务严格串行化
        if self._tls.state is not None:
            lock.release()
            raise TransactionInactiveError("同一线程内不支持嵌套事务")
        # 进入事务：深拷贝隔离工作副本
        self._tls.state = copy.deepcopy(self._storage.live_state())
        self._tls.items = ItemRepository(self, self._storage)
        log("[uow] transaction begin")
        try:
            yield self
        except BaseException as exc:
            # 回滚：丢弃工作副本，正式状态不动
            self._tls.state = None
            self._tls.items = None
            log(f"[uow] rollback due to {type(exc).__name__}: {exc}")
            raise
        else:
            # 提交：原子替换 + 落盘
            committed = self._tls.state
            self._storage.commit(committed)
            self._tls.state = None
            self._tls.items = None
            log("[uow] commit ok")
        finally:
            lock.release()

    @property
    def items(self) -> ItemRepository:
        if self._tls.items is None:
            raise TransactionInactiveError("items 仓储仅在事务内可用")
        return self._tls.items

    def read_repository(self) -> ItemRepository:
        """事务外只读仓储：每次访问基于一致快照。"""
        return ItemRepository(self, self._storage, readonly=True)
