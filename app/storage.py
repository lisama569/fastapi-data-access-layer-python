"""线程安全的内存存储。

并发模型：单把可重入锁（RLock）。
- 事务（读-改-写）在 UoW 中持有整把锁，工作副本来自状态的深拷贝，
  提交时原子替换并落盘，回滚直接丢弃副本 —— 天然避免丢失更新。
- 非事务读取在锁内做一次深拷贝快照后立即释放锁，保证读到一致状态。

持久化：JSON 文件 + 同目录临时文件 os.replace 原子替换，
重复启动不会读到半截文件。
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading


def initial_state() -> dict:
    from app.migrations import SCHEMA_VERSION

    return {
        "items": {},
        "next_id": 1,
        "meta": {"schema_version": SCHEMA_VERSION},
    }


class Storage:
    def __init__(self, path: str | None = None) -> None:
        self._path = path
        self._lock = threading.RLock()
        loaded = self._load_from_disk() if path else None
        self._state: dict = loaded if loaded is not None else initial_state()

    @property
    def lock(self) -> threading.RLock:
        """事务期间由 UnitOfWork 持有的写锁（可重入）。"""
        return self._lock

    def snapshot(self) -> dict:
        """返回当前状态的深拷贝快照（短持锁）。"""
        with self._lock:
            return copy.deepcopy(self._state)

    def live_state(self) -> dict:
        """锁内可见的实时状态引用，调用方必须已持有 lock。"""
        return self._state

    def commit(self, new_state: dict) -> None:
        """以新状态原子替换内存状态并落盘，调用方必须已持有 lock。"""
        with self._lock:
            if self._path is not None:
                self._persist_locked(new_state)
            self._state = new_state

    # ---- 持久化 ----

    def _load_from_disk(self) -> dict | None:
        assert self._path is not None
        if not os.path.exists(self._path):
            return None
        with open(self._path, "r", encoding="utf-8") as f:
            state = json.load(f)
        # JSON 对象键必为字符串，转回 int
        state["items"] = {int(k): v for k, v in state.get("items", {}).items()}
        state.setdefault("next_id", 1)
        state.setdefault("meta", {"schema_version": 0})
        return state

    def _persist_locked(self, state: dict) -> None:
        assert self._path is not None
        directory = os.path.dirname(os.path.abspath(self._path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".data-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
