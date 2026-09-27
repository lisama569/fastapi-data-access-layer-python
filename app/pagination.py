"""稳定的游标（keyset）分页：游标编码、解码与校验。

设计要点：
- 游标记录上一页最后一条记录的 keyset 位置（last_id）与查询条件指纹
  （filter_hash），翻页期间发生新增/删除不会导致结果重复或遗漏；
- 游标带 HMAC 签名，客户端无法伪造；
- 游标带签发时间，超过 TTL 视为已失效；
- 游标中的 filter_hash 与当前查询条件不一致时明确拒绝。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from app.errors import CursorError

# 本地可复现的签名密钥（生产环境应来自配置/密钥管理服务）
_SECRET = b"fastapi-data-access-layer-cursor-secret"


@dataclass(frozen=True)
class Cursor:
    last_id: int
    filter_hash: str
    created_at: float


def compute_filter_hash(filters: dict) -> str:
    """对查询条件计算稳定指纹，用于检测游标与查询条件是否匹配。"""
    normalized = json.dumps(filters, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def encode_cursor(cursor: Cursor) -> str:
    payload = {
        "last_id": cursor.last_id,
        "filter_hash": cursor.filter_hash,
        "created_at": cursor.created_at,
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    sig = hmac.new(_SECRET, body, hashlib.sha256).hexdigest()[:16]
    token = {"p": payload, "s": sig}
    return base64.urlsafe_b64encode(json.dumps(token).encode()).decode()


def decode_cursor(token: str, *, filter_hash: str, ttl_seconds: float) -> Cursor:
    """解码并校验游标；任何不合法都抛出 CursorError 而不是静默继续。"""
    try:
        raw = base64.urlsafe_b64decode(token.encode())
        decoded = json.loads(raw)
        payload, sig = decoded["p"], decoded["s"]
        body = json.dumps(payload, separators=(",", ":")).encode()
        expected = hmac.new(_SECRET, body, hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            raise CursorError("游标签名不匹配，可能已被篡改")
        cursor = Cursor(
            last_id=int(payload["last_id"]),
            filter_hash=str(payload["filter_hash"]),
            created_at=float(payload["created_at"]),
        )
    except CursorError:
        raise
    except Exception as exc:
        raise CursorError("游标格式非法，无法解析", detail={"reason": str(exc)}) from exc

    if cursor.filter_hash != filter_hash:
        raise CursorError(
            "游标与当前查询条件不匹配",
            detail={"cursor_filter": cursor.filter_hash, "query_filter": filter_hash},
        )
    age = time.time() - cursor.created_at
    if age > ttl_seconds:
        raise CursorError("游标已失效，请重新发起查询", detail={"age_seconds": round(age, 3)})
    return cursor


def issue_cursor(last_id: int, filter_hash: str) -> str:
    return encode_cursor(Cursor(last_id=last_id, filter_hash=filter_hash, created_at=time.time()))
