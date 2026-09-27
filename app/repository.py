"""仓储层。

- 事务内读写作用于 UoW 的工作副本；事务外只允许快照读。
- 更新采用乐观并发控制：调用方必须携带 expected_version，
  与当前记录版本不一致时抛 ConflictError（区别于 NotFoundError 与
  ValidationError），绝不静默覆盖。
- 分页为按 id 升序的 keyset 分页，位置来自已校验游标。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

from app.errors import ConflictError, NotFoundError, ValidationError
from app.pagination import decode_cursor, encode_cursor

if TYPE_CHECKING:
    from app.storage import Storage
    from app.unitofwork import UnitOfWork


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class ItemRepository:
    def __init__(
        self,
        uow: "UnitOfWork",
        storage: "Storage",
        *,
        readonly: bool = False,
        clock: Callable[[], str] = utcnow_iso,
    ) -> None:
        self._uow = uow
        self._storage = storage
        self._readonly = readonly
        self._clock = clock

    # ---- 内部：取工作状态或只读快照 ----

    def _rw_state(self) -> dict:
        if self._readonly:
            raise ValidationError("只读仓储不能执行写操作")
        return self._uow.state

    def _read_state(self) -> dict:
        if self._uow.active and not self._readonly:
            return self._uow.state  # 事务内：读到自己的未提交写入
        return self._storage.snapshot()  # 事务外：一致快照

    # ---- 写操作（仅事务内）----

    def add(self, name: str, *, log=print) -> dict:
        name = self._validate_name(name)
        state = self._rw_state()
        item_id = state["next_id"]
        now = self._clock()
        row = {
            "id": item_id,
            "name": name,
            "version": 1,
            "created_at": now,
            "updated_at": now,
        }
        state["items"][item_id] = row
        state["next_id"] = item_id + 1
        log(f"[repo] add id={item_id} name={name!r} version=1")
        return dict(row)

    def update(
        self, item_id: int, name: str, expected_version: int, *, log=print
    ) -> dict:
        name = self._validate_name(name)
        if not isinstance(expected_version, int) or expected_version < 1:
            raise ValidationError("expected_version 必须为 >=1 的整数")
        state = self._rw_state()
        row = state["items"].get(item_id)
        if row is None:
            log(f"[repo] update miss id={item_id} (not found)")
            raise NotFoundError("记录不存在", details={"id": item_id})
        current = row["version"]
        log(
            f"[repo] update id={item_id} expected_version={expected_version} "
            f"current_version={current}"
        )
        if expected_version != current:
            raise ConflictError(
                "记录已被其他请求修改，版本冲突，本次更新被拒绝",
                details={
                    "id": item_id,
                    "expected_version": expected_version,
                    "current_version": current,
                },
            )
        row["name"] = name
        row["version"] = current + 1
        row["updated_at"] = self._clock()
        log(f"[repo] update ok id={item_id} new_version={row['version']}")
        return dict(row)

    def delete(self, item_id: int, *, log=print) -> None:
        state = self._rw_state()
        if item_id not in state["items"]:
            log(f"[repo] delete miss id={item_id} (not found)")
            raise NotFoundError("记录不存在", details={"id": item_id})
        del state["items"][item_id]
        log(f"[repo] delete ok id={item_id}")

    # ---- 读操作 ----

    def get(self, item_id: int) -> dict:
        state = self._read_state()
        row = state["items"].get(item_id)
        if row is None:
            raise NotFoundError("记录不存在", details={"id": item_id})
        return dict(row)

    def list_page(
        self,
        limit: int,
        cursor: str | None,
        name_filter: str | None,
        *,
        log=print,
    ) -> tuple[list[dict], str | None, bool]:
        if not isinstance(limit, int) or limit < 1 or limit > 100:
            raise ValidationError("limit 必须为 1..100 的整数")
        if name_filter is not None:
            name_filter = name_filter.strip() or None

        after_id = 0
        if cursor is not None:
            after_id = decode_cursor(cursor, name_filter, log=log)["id"]

        state = self._read_state()
        candidates = [
            row
            for row in state["items"].values()
            if row["id"] > after_id
            and (name_filter is None or name_filter in row["name"])
        ]
        candidates.sort(key=lambda r: r["id"])
        page = candidates[:limit]
        has_more = len(candidates) > limit
        next_cursor = (
            encode_cursor(page[-1]["id"], name_filter) if has_more else None
        )
        log(
            f"[repo] list after_id={after_id} limit={limit} "
            f"filter={name_filter!r} matched={len(candidates)} "
            f"returned={len(page)} has_more={has_more}"
        )
        return [dict(r) for r in page], next_cursor, has_more

    @staticmethod
    def _validate_name(name: str) -> str:
        if not isinstance(name, str):
            raise ValidationError("name 必须为字符串")
        name = name.strip()
        if not name or len(name) > 200:
            raise ValidationError("name 不能为空且长度不能超过 200")
        return name
