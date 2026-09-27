"""游标编解码与校验。

分页语义（稳定 keyset/seek 分页）：
- 按不可变的主键 id 升序翻页，游标只携带"上一页最后一条记录的 id"这个位置，
  因此翻页过程中：
  * 已返回过的页不可能再次返回（id 只增），不会重复；
  * 不会因 LIMIT/OFFSET 那样的整体位移而跳过中间记录，不会遗漏；
  * 期间删除记录只会让后续结果少一行；新增 id 更大的记录只会出现在后续页；
    id 更小的新记录属于更早的页，不会在游标之后冒出 —— 窗口稳定。
- 游标是位置而不是行内容，因此游标指向的行被删除仍可正常继续翻页。

游标安全：
- payload 为 JSON，base64url 编码，并附 HMAC-SHA256 签名，防伪造与篡改；
- 游标内绑定生成时的查询条件 name_filter；解码时若与当前请求条件不一致，
  以 reason="filter_mismatch" 明确拒绝（而不是从任意位置继续返回）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from app.errors import InvalidCursorError

CURSOR_VERSION = "v1"
# 本地复现用途的固定密钥；多实例部署应改为环境注入。
CURSOR_SECRET = b"local-dev-cursor-secret-v1"


def _sign(payload_b64: str) -> str:
    mac = hmac.new(CURSOR_SECRET, payload_b64.encode("ascii"), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()).rstrip(b"=").decode("ascii")


def encode_cursor(last_id: int, name_filter: str | None) -> str:
    payload = {"v": CURSOR_VERSION, "id": last_id, "f": name_filter}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"{body}.{_sign(body)}"


def decode_cursor(token: str, name_filter: str | None, *, log=print) -> dict:
    """解码并校验游标。

    返回 {"id": int, "filter": str|None}；
    任何不合法情况抛 InvalidCursorError，details.reason 区分原因：
    malformed / bad_signature / bad_payload / bad_version / id_missing /
    filter_mismatch。
    """
    def reject(reason: str, detail: str) -> None:
        log(
            f"[cursor] reject reason={reason} detail={detail} "
            f"name_filter={name_filter!r} token={token[:24]!r}"
        )
        raise InvalidCursorError(
            f"非法或已失效的游标（{reason}）",
            details={"reason": reason, "detail": detail},
        )

    if not isinstance(token, str) or "." not in token:
        reject("malformed", "游标必须为 '<body>.<signature>' 形式")
    body, _, sig = token.rpartition(".")
    if not body or not sig:
        reject("malformed", "游标体或签名为空")

    expected_sig = _sign(body)
    if not hmac.compare_digest(sig, expected_sig):
        reject("bad_signature", "签名校验失败，游标可能被篡改或来自不同实例")

    pad = "=" * (-len(body) % 4)
    try:
        raw = base64.urlsafe_b64decode(body + pad)
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        reject("bad_payload", f"游标内容无法解码: {exc}")

    if not isinstance(payload, dict):
        reject("bad_payload", "游标内容必须为 JSON 对象")
    if payload.get("v") != CURSOR_VERSION:
        reject("bad_version", f"不支持的游标版本: {payload.get('v')!r}")
    last_id = payload.get("id")
    if not isinstance(last_id, int) or isinstance(last_id, bool) or last_id < 1:
        reject("id_missing", "游标缺少合法的起始位置 id")

    cursor_filter = payload.get("f")
    if cursor_filter != name_filter:
        reject(
            "filter_mismatch",
            f"游标绑定的查询条件 {cursor_filter!r} 与当前 {name_filter!r} 不一致",
        )

    log(f"[cursor] accept last_id={last_id} name_filter={name_filter!r}")
    return {"id": last_id, "filter": cursor_filter}
