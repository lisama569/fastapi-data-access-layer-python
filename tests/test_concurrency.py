"""并发冲突测试：乐观锁拒绝静默覆盖，错误可与校验失败/不存在区分。"""
from __future__ import annotations

import logging
import threading
from collections import Counter

from app.errors import ConflictError, NotFoundError
from app.models import ItemCreate, ItemUpdate
from app.repositories import ItemRepository
from app.uow import UnitOfWork

logger = logging.getLogger("test.concurrency")


def _create_item(db_path: str, name: str, value: int):
    with UnitOfWork(db_path) as uow:
        item = ItemRepository(uow.conn).create(ItemCreate(name=name, value=value))
        uow.commit()
        return item


def test_concurrent_updates_exactly_one_wins(db_path):
    """N 个线程以相同 version 并发更新同一条记录：恰好 1 个成功，其余全部
    收到 ConflictError，最终 version 只推进一次，无静默覆盖。"""
    item = _create_item(db_path, "hot", 0)
    n_threads = 8
    barrier = threading.Barrier(n_threads)
    results: list[str] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        barrier.wait()  # 尽量同时出发，放大竞争
        try:
            with UnitOfWork(db_path) as uow:
                ItemRepository(uow.conn).update(
                    item.id, ItemUpdate(value=100 + i, version=item.version)
                )
                uow.commit()
            outcome = "committed"
        except ConflictError:
            outcome = "conflict"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    counts = Counter(results)
    with UnitOfWork(db_path) as uow:
        final = ItemRepository(uow.conn).get(item.id)
        uow.commit()
    logger.info(
        "输入: %d 个线程以 version=%d 并发更新条目 %d; 判定依据: 恰好 1 个 committed、"
        "%d 个 conflict、最终 version=2; 实际: %s, final.version=%d",
        n_threads,
        item.version,
        item.id,
        n_threads - 1,
        dict(counts),
        final.version,
    )
    assert counts["committed"] == 1
    assert counts["conflict"] == n_threads - 1
    assert final.version == item.version + 1


def test_error_kinds_are_distinguishable(client):
    """冲突(409/conflict)、不存在(404/not_found)、校验失败(422/validation_failed)
    三类错误的 HTTP 状态码与机器可读 code 均可区分。"""
    created = client.post("/items", json={"name": "x", "value": 1}).json()

    r_conflict = client.put(
        f"/items/{created['id']}", json={"value": 2, "version": created["version"] + 5}
    )
    r_not_found = client.get("/items/424242")
    r_validation = client.post("/items", json={"name": "y"})  # 缺 value 字段

    logger.info(
        "输入: 过期 version 更新 / 不存在 id / 缺字段创建; 判定依据: "
        "状态码与 error.code 分别为 409/conflict、404/not_found、422/validation_failed; "
        "实际: (%d,%s) (%d,%s) (%d,%s)",
        r_conflict.status_code, r_conflict.json()["error"]["code"],
        r_not_found.status_code, r_not_found.json()["error"]["code"],
        r_validation.status_code, r_validation.json()["error"]["code"],
    )
    assert r_conflict.status_code == 409
    assert r_conflict.json()["error"]["code"] == "conflict"
    assert r_not_found.status_code == 404
    assert r_not_found.json()["error"]["code"] == "not_found"
    assert r_validation.status_code == 422
    assert r_validation.json()["error"]["code"] == "validation_failed"


def test_stale_delete_rejected(db_path):
    """并发删除：携带过期 version 的删除被拒绝为 ConflictError，记录保留。"""
    item = _create_item(db_path, "del", 1)
    # 另一事务先更新，使 version 前进
    with UnitOfWork(db_path) as uow:
        ItemRepository(uow.conn).update(item.id, ItemUpdate(value=2, version=1))
        uow.commit()

    try:
        with UnitOfWork(db_path) as uow:
            ItemRepository(uow.conn).delete(item.id, version=1)  # 旧 version
            uow.commit()
        outcome = "deleted"
    except ConflictError as exc:
        outcome = f"conflict:{exc.code}"

    with UnitOfWork(db_path) as uow:
        still_there = ItemRepository(uow.conn).get(item.id)
        uow.commit()
    logger.info(
        "输入: 以 version=1 删除已被并发推进到 version=2 的条目; 判定依据: "
        "抛出 ConflictError 且记录仍存在; 实际: outcome=%s still_there.version=%d",
        outcome,
        still_there.version,
    )
    assert outcome == "conflict:conflict"
    assert still_there.version == 2


def test_delete_missing_raises_not_found(db_path):
    """删除不存在的记录是 NotFoundError，而非 ConflictError。"""
    try:
        with UnitOfWork(db_path) as uow:
            ItemRepository(uow.conn).delete(999, version=1)
            uow.commit()
        outcome = "deleted"
    except NotFoundError as exc:
        outcome = f"not_found:{exc.code}"
    logger.info("输入: 删除 id=999; 判定依据: NotFoundError; 实际: %s", outcome)
    assert outcome == "not_found:not_found"
