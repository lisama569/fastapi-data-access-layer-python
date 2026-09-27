"""并发更新冲突与事务原子性测试。"""

from __future__ import annotations

import threading

from app.errors import ConflictError, NotFoundError
from app.storage import Storage
from app.unitofwork import UnitOfWork
from tests.conftest import log_case


def test_concurrent_updates_only_one_succeeds():
    """两个线程基于同一版本并发更新同一记录：只有一个成功，另一个收到可区分的冲突错误。"""
    uow = UnitOfWork(Storage(None))
    with uow.transaction() as tx:
        item = tx.items.add("original")
    item_id, base_version = item["id"], item["version"]
    log_case("concurrent-update-setup", id=item_id, version=base_version)

    # 两个线程各自开启事务并基于同一 base_version 更新。事务串行提交
    # （单写锁），因此先提交者成功，后提交者检测到版本前进而收到冲突 ——
    # 不会出现两边都成功、后写静默覆盖先写的情况。
    results: dict[str, str] = {}

    def worker(name: str, new_name: str) -> None:
        try:
            with uow.transaction() as tx:
                tx.items.update(item_id, new_name, base_version)
            results[name] = "committed"
        except ConflictError as exc:
            results[name] = f"conflict:v{exc.details['current_version']}"

    t1 = threading.Thread(target=worker, args=("t1", "from-t1"))
    t2 = threading.Thread(target=worker, args=("t2", "from-t2"))
    t1.start(); t2.start(); t1.join(); t2.join()

    log_case("concurrent-update-result", results=results)
    statuses = set(results.values())
    assert "committed" in statuses, f"应恰有一个提交成功: {results}"
    assert any(v.startswith("conflict:") for v in results.values()), results

    # 最终数据必须一致：版本恰好 +1，且只保留胜出者写入
    final = uow.read_repository().get(item_id)
    log_case("concurrent-update-final", final=final)
    assert final["version"] == base_version + 1
    assert final["name"] in {"from-t1", "from-t2"}


def test_conflict_is_distinguishable_from_notfound_and_validation(client):
    """HTTP 层：409 conflict / 404 not_found / 400 validation_error 三者可区分。"""
    created = client.post("/items", json={"name": "a"}).json()
    item_id = created["id"]

    # 先用错误的 expected_version 制造冲突
    r_conflict = client.patch(
        f"/items/{item_id}", json={"name": "b", "expected_version": 999}
    )
    assert r_conflict.status_code == 409
    assert r_conflict.json()["error"]["type"] == "conflict"
    assert r_conflict.json()["error"]["details"]["current_version"] == 1
    log_case("error-distinction-conflict", body=r_conflict.json())

    # 不存在 → 404
    r_missing = client.patch(
        "/items/99999", json={"name": "b", "expected_version": 1}
    )
    assert r_missing.status_code == 404
    assert r_missing.json()["error"]["type"] == "not_found"

    # 参数非法 → 400 validation_error
    r_invalid = client.patch(
        f"/items/{item_id}", json={"name": "", "expected_version": 1}
    )
    assert r_invalid.status_code == 400
    assert r_invalid.json()["error"]["type"] == "validation_error"

    # 冲突被拒绝后，原数据必须保持不变
    still = client.get(f"/items/{item_id}").json()
    assert still["name"] == "a" and still["version"] == 1
    log_case("error-distinction-final", item=still)


def test_transaction_rolls_back_all_writes_on_failure():
    """事务内先成功 add，随后因删除不存在记录抛 NotFoundError：add 必须一并回滚。"""
    uow = UnitOfWork(Storage(None))

    before = uow.read_repository()._read_state()["next_id"]
    try:
        with uow.transaction() as tx:
            tx.items.add("will-rollback")
            tx.items.add("will-rollback-2")
            tx.items.delete(99999)  # 触发回滚
    except NotFoundError:
        pass
    else:
        raise AssertionError("应当抛出 NotFoundError")

    state = uow.read_repository()._read_state()
    log_case(
        "rollback-state",
        next_id_before=before,
        next_id_after=state["next_id"],
        items=list(state["items"].keys()),
    )
    # 所有写入都未生效，自增序列也停留在原值
    assert state["items"] == {}
    assert state["next_id"] == before


def test_multi_write_transaction_commits_atomically():
    """正常事务中的多次写入全部生效。"""
    uow = UnitOfWork(Storage(None))
    with uow.transaction() as tx:
        tx.items.add("one")
        tx.items.add("two")
        tx.items.add("three")
    rows = uow.read_repository()._read_state()["items"]
    log_case("atomic-commit", ids=sorted(rows))
    assert sorted(rows) == [1, 2, 3]
