"""事务边界测试：多次写入原子生效或全部回滚。"""
from __future__ import annotations

import logging

import pytest

from app.errors import ConflictError
from app.models import ItemCreate, ItemUpdate
from app.repositories import ItemRepository
from app.uow import UnitOfWork

logger = logging.getLogger("test.transactions")


def test_multi_write_commit_all_visible(db_path):
    """同一事务内两次写入，commit 后全部可见。"""
    with UnitOfWork(db_path) as uow:
        repo = ItemRepository(uow.conn)
        a = repo.create(ItemCreate(name="tx-a", value=1))
        b = repo.create(ItemCreate(name="tx-b", value=2))
        uow.commit()
    logger.info("输入: 两次 create; 判定依据: commit 后两条记录均可读取")
    with UnitOfWork(db_path) as uow:
        repo = ItemRepository(uow.conn)
        assert repo.get(a.id).name == "tx-a"
        assert repo.get(b.id).name == "tx-b"
        uow.commit()


def test_exception_rolls_back_all_writes(db_path):
    """事务中途抛异常，之前的写入全部回滚，不留半截数据。"""
    with UnitOfWork(db_path) as uow:
        repo = ItemRepository(uow.conn)
        seed = repo.create(ItemCreate(name="seed", value=0))
        uow.commit()

    with pytest.raises(RuntimeError, match="boom"):
        with UnitOfWork(db_path) as uow:
            repo = ItemRepository(uow.conn)
            repo.create(ItemCreate(name="should-vanish", value=1))
            repo.update(seed.id, ItemUpdate(value=99, version=seed.version))
            raise RuntimeError("boom")

    with UnitOfWork(db_path) as uow:
        repo = ItemRepository(uow.conn)
        names = [i.name for i in repo.list_after(last_id=0, limit=100, name_prefix=None)]
        seed_after = repo.get(seed.id)
        uow.commit()
    logger.info(
        "输入: create + update 后抛异常; 判定依据: 新记录不存在且 seed.value 仍为 0; "
        "实际: names=%s seed.value=%s",
        names,
        seed_after.value,
    )
    assert "should-vanish" not in names
    assert seed_after.value == 0
    assert seed_after.version == seed.version


def test_missing_commit_rolls_back(db_path):
    """未显式 commit 即退出上下文，写入同样回滚。"""
    with UnitOfWork(db_path) as uow:
        ItemRepository(uow.conn).create(ItemCreate(name="no-commit", value=1))
        # 故意不调用 uow.commit()

    with UnitOfWork(db_path) as uow:
        names = [
            i.name
            for i in ItemRepository(uow.conn).list_after(
                last_id=0, limit=100, name_prefix=None
            )
        ]
        uow.commit()
    logger.info("输入: create 后不 commit; 判定依据: 记录不可见; 实际: names=%s", names)
    assert "no-commit" not in names


def test_conflict_inside_transaction_rolls_back_other_writes(db_path):
    """事务内后一次写入因并发冲突失败，前一次写入也一并回滚。"""
    with UnitOfWork(db_path) as uow:
        repo = ItemRepository(uow.conn)
        item = repo.create(ItemCreate(name="victim", value=1))
        uow.commit()

    # 另一事务先把 version 推进到 2
    with UnitOfWork(db_path) as uow:
        repo = ItemRepository(uow.conn)
        repo.update(item.id, ItemUpdate(value=2, version=1))
        uow.commit()

    with pytest.raises(ConflictError):
        with UnitOfWork(db_path) as uow:
            repo = ItemRepository(uow.conn)
            repo.create(ItemCreate(name="companion", value=3))  # 先写一条
            repo.update(item.id, ItemUpdate(value=3, version=1))  # 旧 version → 冲突

    with UnitOfWork(db_path) as uow:
        repo = ItemRepository(uow.conn)
        names = [i.name for i in repo.list_after(last_id=0, limit=100, name_prefix=None)]
        victim = repo.get(item.id)
        uow.commit()
    logger.info(
        "输入: 事务内 create 后以旧 version update; 判定依据: 冲突异常且 companion 不存在、"
        "victim.value 仍为 2; 实际: names=%s victim.value=%s",
        names,
        victim.value,
    )
    assert "companion" not in names
    assert victim.value == 2
