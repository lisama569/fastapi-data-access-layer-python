"""游标分页稳定性与游标校验测试。"""

from __future__ import annotations

import pytest

from app.errors import InvalidCursorError
from app.pagination import decode_cursor, encode_cursor
from app.storage import Storage
from app.unitofwork import UnitOfWork
from tests.conftest import log_case


def _seed(uow: UnitOfWork, names: list[str]) -> None:
    with uow.transaction() as tx:
        for n in names:
            tx.items.add(n)


def test_pagination_no_duplicates_or_gaps_across_pages():
    """完整翻页：每页不重不漏，顺序按 id 升序。"""
    uow = UnitOfWork(Storage(None))
    _seed(uow, [f"item-{i}" for i in range(5)])

    seen: list[int] = []
    cursor = None
    pages = 0
    while True:
        rows, cursor, has_more = uow.read_repository().list_page(2, cursor, None)
        seen.extend(r["id"] for r in rows)
        pages += 1
        log_case("page", page=pages, ids=[r["id"] for r in rows], has_more=has_more)
        if not has_more:
            assert cursor is None
            break
    assert seen == [1, 2, 3, 4, 5]
    assert len(seen) == len(set(seen))


def test_insert_and_delete_during_pagination_do_not_duplicate_or_skip():
    """翻页过程中：新增 id 更大的记录不影响已有页；删除未来页记录不产生空洞错位。"""
    uow = UnitOfWork(Storage(None))
    _seed(uow, ["a1", "a2", "a3"])

    # 第一页：id 1
    page1, cursor, has_more = uow.read_repository().list_page(1, None, None)
    assert [r["id"] for r in page1] == [1] and has_more

    # 翻页期间：新增一条（id=4，在窗口之后）并删除 id=2（未来页中的记录）
    with uow.transaction() as tx:
        tx.items.add("a4")
        tx.items.delete(2)

    # 用旧游标继续：不得重复 id=1，也不得遗漏仍然存在的 id=3
    remaining = []
    cur = cursor
    while True:
        rows, cur, has_more = uow.read_repository().list_page(1, cur, None)
        remaining.extend(r["id"] for r in rows)
        if not has_more:
            break
    log_case("during-mutation-pages", remaining=remaining, deleted=2, inserted=4)
    assert 1 not in remaining          # 不重复
    assert 2 not in remaining          # 已删除
    assert remaining == [3, 4]         # 不跳过仍存在的记录，新记录正常出现


def test_filter_pagination_only_matches_and_binds_cursor():
    uow = UnitOfWork(Storage(None))
    _seed(uow, ["apple", "banana", "apricot", "cherry"])

    rows, cursor, has_more = uow.read_repository().list_page(1, None, "ap")
    assert [r["name"] for r in rows] == ["apple"] and has_more
    rows2, _, has_more2 = uow.read_repository().list_page(10, cursor, "ap")
    assert [r["name"] for r in rows2] == ["apricot"] and not has_more2
    log_case("filter-pages", cursor=cursor)


@pytest.mark.parametrize(
    "token, name_filter, expected_reason",
    [
        ("not-a-cursor", None, "malformed"),
        ("abc.def", None, "bad_signature"),
        (encode_cursor(1, "ap")[:-2] + "aa", "ap", "bad_signature"),
        (encode_cursor(1, "ap"), None, "filter_mismatch"),
        (encode_cursor(1, "ap"), "xx", "filter_mismatch"),
    ],
)
def test_invalid_cursors_are_rejected(token, name_filter, expected_reason):
    with pytest.raises(InvalidCursorError) as exc:
        decode_cursor(token, name_filter, log=lambda *a: None)
    assert exc.value.details["reason"] == expected_reason
    log_case(
        "invalid-cursor",
        token_prefix=token[:16],
        name_filter=name_filter,
        reason=exc.value.details["reason"],
    )


def test_invalid_cursor_http_distinguishable(client):
    r = client.get("/items", params={"cursor": "garbage"})
    assert r.status_code == 400
    body = r.json()["error"]
    assert body["type"] == "invalid_cursor"
    assert body["details"]["reason"] == "malformed"
    log_case("invalid-cursor-http", status=400, body=body)
