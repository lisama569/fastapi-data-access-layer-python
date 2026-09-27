"""分页测试：增删期间结果不重复不遗漏，非法游标被明确拒绝。"""
from __future__ import annotations

import logging
import time

from app.models import ItemCreate
from app.pagination import compute_filter_hash, issue_cursor
from app.repositories import ItemRepository
from app.uow import UnitOfWork

logger = logging.getLogger("test.pagination")


def _seed(client, n: int, prefix: str = "item") -> list[dict]:
    return [
        client.post("/items", json={"name": f"{prefix}-{i:03d}", "value": i}).json()
        for i in range(n)
    ]


def _walk_pages(client, limit: int, **params) -> list[dict]:
    """跟随 next_cursor 翻完所有页，返回累计条目。"""
    collected: list[dict] = []
    cursor = None
    while True:
        q = {"limit": limit, **params}
        if cursor:
            q["cursor"] = cursor
        page = client.get("/items", params=q).json()
        collected.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return collected


def test_insert_and_delete_during_pagination_no_dup_no_missing(client):
    """翻页过程中新增（id 落在已翻过的区间）与删除（尚未翻到的记录），
    结果集不重复、且不漏掉任何始终存在且应被看到的记录。"""
    seeded = _seed(client, 10)  # id 1..10
    limit = 4

    # 第 1 页
    page1 = client.get("/items", params={"limit": limit}).json()
    assert [i["id"] for i in page1["items"]] == [1, 2, 3, 4]

    # 翻页间隙：删除一条尚未翻到的记录(id=6)，新增一条记录(id=11)
    client.delete("/items/6", params={"version": 1})
    client.post("/items", json={"name": "item-999", "value": 999})

    # 继续翻完剩余页
    rest: list[dict] = []
    cursor = page1["next_cursor"]
    while cursor:
        page = client.get("/items", params={"limit": limit, "cursor": cursor}).json()
        rest.extend(page["items"])
        cursor = page["next_cursor"]

    all_items = page1["items"] + rest
    ids = [i["id"] for i in all_items]
    logger.info(
        "输入: 10 条种子数据, limit=%d, 翻页间隙删除 id=6、新增 id=11; 判定依据: "
        "id 无重复、id=6 不出现（已删）、id=11 出现（新增且 id 大于游标位置）、"
        "其余 1..10 均恰好出现一次; 实际: ids=%s",
        limit,
        ids,
    )
    assert len(ids) == len(set(ids)), "结果出现重复"
    assert 6 not in ids, "已删除的记录不应出现"
    assert 11 in ids, "翻页期间新增且 id 更大的记录应被看到"
    assert sorted(ids) == [1, 2, 3, 4, 5, 7, 8, 9, 10, 11]


def test_offset_style_deletion_would_skip_but_keyset_does_not(client):
    """keyset 语义验证：删除已翻过区间内的记录不影响后续页的连续性。"""
    _seed(client, 7)
    page1 = client.get("/items", params={"limit": 3}).json()
    client.delete("/items/2", params={"version": 1})  # 删除已翻过区间内的记录
    page2 = client.get(
        "/items", params={"limit": 3, "cursor": page1["next_cursor"]}
    ).json()
    ids2 = [i["id"] for i in page2["items"]]
    logger.info(
        "输入: 7 条记录, 第 1 页后删除 id=2; 判定依据: 第 2 页从 id=4 开始连续返回 "
        "[4,5,6]（offset 分页在此场景会跳过 id=4）; 实际: %s",
        ids2,
    )
    assert ids2 == [4, 5, 6]


def test_invalid_cursor_rejected(client):
    """无法解析的游标被拒绝为 400/invalid_cursor。"""
    r = client.get("/items", params={"cursor": "not-a-cursor"})
    logger.info(
        "输入: cursor='not-a-cursor'; 判定依据: 400 + invalid_cursor; 实际: %d %s",
        r.status_code,
        r.json()["error"]["code"],
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_cursor"


def test_tampered_cursor_rejected(client):
    """篡改过的游标（签名不匹配）被拒绝。"""
    _seed(client, 3)
    good = client.get("/items", params={"limit": 2}).json()["next_cursor"]
    tampered = good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")
    r = client.get("/items", params={"cursor": tampered})
    logger.info(
        "输入: 篡改尾部的游标; 判定依据: 400 + invalid_cursor; 实际: %d %s",
        r.status_code,
        r.json()["error"]["code"],
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_cursor"


def test_cursor_filter_mismatch_rejected(client):
    """游标对应的查询条件与当前请求不一致时被明确拒绝。"""
    _seed(client, 5, prefix="apple")
    page1 = client.get(
        "/items", params={"limit": 2, "name_prefix": "apple"}
    ).json()
    r = client.get(
        "/items",
        params={"limit": 2, "name_prefix": "banana", "cursor": page1["next_cursor"]},
    )
    logger.info(
        "输入: 用 name_prefix=apple 的游标查询 name_prefix=banana; 判定依据: "
        "400 + invalid_cursor; 实际: %d %s",
        r.status_code,
        r.json()["error"]["code"],
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_cursor"


def test_expired_cursor_rejected(client):
    """超过 TTL 的游标被拒绝，而不是从任意位置继续。"""
    import app.api as api_module

    original_ttl = api_module.CURSOR_TTL_SECONDS
    api_module.CURSOR_TTL_SECONDS = 0.05
    try:
        _seed(client, 3)
        cursor = client.get("/items", params={"limit": 2}).json()["next_cursor"]
        time.sleep(0.1)
        r = client.get("/items", params={"limit": 2, "cursor": cursor})
    finally:
        api_module.CURSOR_TTL_SECONDS = original_ttl
    logger.info(
        "输入: TTL=0.05s, 等待 0.1s 后使用游标; 判定依据: 400 + invalid_cursor; "
        "实际: %d %s",
        r.status_code,
        r.json()["error"]["code"],
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_cursor"


def test_filtered_pagination_complete(client):
    """带过滤条件的分页能完整遍历匹配子集。"""
    _seed(client, 3, prefix="apple")
    _seed(client, 4, prefix="banana")
    collected = _walk_pages(client, limit=2, name_prefix="banana")
    names = [i["name"] for i in collected]
    logger.info(
        "输入: 3 条 apple + 4 条 banana, limit=2 翻页; 判定依据: 恰好 4 条 banana "
        "且无重复; 实际: %s",
        names,
    )
    assert names == [f"banana-{i:03d}" for i in range(4)]


def test_cursor_roundtrip_via_repository(db_path):
    """仓储层 keyset 查询与游标签发的端到端一致性（不经 HTTP）。"""
    with UnitOfWork(db_path) as uow:
        repo = ItemRepository(uow.conn)
        for i in range(5):
            repo.create(ItemCreate(name=f"r-{i}", value=i))
        first = repo.list_after(last_id=0, limit=2, name_prefix=None)
        fh = compute_filter_hash({"name_prefix": None})
        token = issue_cursor(first[-1].id, fh)
        from app.pagination import decode_cursor

        decoded = decode_cursor(token, filter_hash=fh, ttl_seconds=60)
        second = repo.list_after(last_id=decoded.last_id, limit=2, name_prefix=None)
        uow.commit()
    logger.info(
        "输入: 5 条记录, limit=2; 判定依据: 第二页从 id=%d 之后继续; 实际: %s",
        first[-1].id,
        [i.id for i in second],
    )
    assert [i.id for i in second] == [first[-1].id + 1, first[-1].id + 2]
