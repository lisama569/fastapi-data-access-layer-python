"""仓储式数据访问：条目的增删改查与乐观锁并发控制。

并发约定：更新/删除必须携带读取时获得的 version；SQL 层面用
``WHERE id=? AND version=?`` 保证并发修改同一条记录时只有一方生效，
另一方 rowcount=0，被明确拒绝为 ConflictError，绝不静默覆盖。
"""
from __future__ import annotations

import sqlite3

from app.errors import ConflictError, NotFoundError
from app.models import ItemCreate, ItemOut, ItemUpdate


class ItemRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @staticmethod
    def _to_out(row: sqlite3.Row) -> ItemOut:
        return ItemOut(id=row["id"], name=row["name"], value=row["value"], version=row["version"])

    def create(self, data: ItemCreate) -> ItemOut:
        cur = self._conn.execute(
            "INSERT INTO items (name, value, version) VALUES (?, ?, 1)",
            (data.name, data.value),
        )
        return self.get(cur.lastrowid)  # type: ignore[arg-type]

    def get(self, item_id: int) -> ItemOut:
        row = self._conn.execute(
            "SELECT id, name, value, version FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"条目 {item_id} 不存在", detail={"item_id": item_id})
        return self._to_out(row)

    def update(self, item_id: int, data: ItemUpdate) -> ItemOut:
        current = self.get(item_id)  # 不存在 → NotFoundError
        new_name = current.name if data.name is None else data.name
        new_value = current.value if data.value is None else data.value
        cur = self._conn.execute(
            "UPDATE items SET name = ?, value = ?, version = version + 1 "
            "WHERE id = ? AND version = ?",
            (new_name, new_value, item_id, data.version),
        )
        if cur.rowcount == 0:
            raise ConflictError(
                f"条目 {item_id} 已被并发修改，请重新读取后重试",
                detail={"item_id": item_id, "expected_version": data.version},
            )
        return self.get(item_id)

    def delete(self, item_id: int, *, version: int) -> None:
        self.get(item_id)  # 不存在 → NotFoundError
        cur = self._conn.execute(
            "DELETE FROM items WHERE id = ? AND version = ?", (item_id, version)
        )
        if cur.rowcount == 0:
            raise ConflictError(
                f"条目 {item_id} 已被并发修改，删除被拒绝",
                detail={"item_id": item_id, "expected_version": version},
            )

    def list_after(
        self, *, last_id: int, limit: int, name_prefix: str | None
    ) -> list[ItemOut]:
        """keyset 分页：只取 id > last_id 的记录，与增删无关、不重复不遗漏。"""
        if name_prefix is None:
            rows = self._conn.execute(
                "SELECT id, name, value, version FROM items WHERE id > ? "
                "ORDER BY id ASC LIMIT ?",
                (last_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, name, value, version FROM items "
                "WHERE id > ? AND name LIKE ? ORDER BY id ASC LIMIT ?",
                (last_id, name_prefix + "%", limit),
            ).fetchall()
        return [self._to_out(r) for r in rows]
