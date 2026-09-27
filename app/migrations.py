"""本地可复现的数据结构版本管理。

规则：
- 每个版本对应一个纯函数迁移 step: 0->1, 1->2 ...，按版本号顺序执行，
  迁移成功后记录新版本号。顺序执行保证任意旧版本都能升级到最新。
- 重复执行（版本已最新）是无操作（no-op），不会破坏或重复改写数据。
- 已安装版本高于代码已知版本（疑似旧代码打开新数据/降级）→ 拒绝启动。
- 缺少 meta 信息的文件视为版本 0，同样走顺序迁移。
"""

from __future__ import annotations

from app.errors import MigrationError

# 当前代码支持的数据结构版本
SCHEMA_VERSION: int = 1


def _migrate_0_to_1(state: dict) -> dict:
    """v1：确立 items 字典 + 自增主键 next_id + meta 的规范结构。"""
    items = state.get("items")
    if not isinstance(items, dict):
        raise MigrationError(
            "数据结构不兼容：items 必须为对象（id -> 记录）",
            details={"actual_type": type(items).__name__},
        )
    normalized: dict[int, dict] = {}
    for key, row in items.items():
        if not isinstance(row, dict) or "id" not in row:
            raise MigrationError(
                "数据结构不兼容：item 记录必须为含 id 字段的对象",
                details={"bad_key": str(key)},
            )
        normalized[int(row["id"])] = row
    state["items"] = normalized
    try:
        state["next_id"] = int(state.get("next_id", 1))
    except (TypeError, ValueError):
        raise MigrationError("数据结构不兼容：next_id 必须为整数")
    return state


# 版本号 -> 迁移到下一版本的函数
_STEPS = {
    0: _migrate_0_to_1,
}


def get_installed_version(state: dict) -> int:
    meta = state.get("meta")
    if not isinstance(meta, dict) or "schema_version" not in meta:
        return 0
    try:
        return int(meta["schema_version"])
    except (TypeError, ValueError):
        raise MigrationError(
            "数据结构不兼容：meta.schema_version 必须为整数",
            details={"actual": meta.get("schema_version")},
        )


def run_migrations(state: dict, *, log=print) -> dict:
    """启动时校验版本并按顺序执行迁移，原地更新并返回 state。"""
    installed = get_installed_version(state)
    log(f"[migration] installed_version={installed} code_version={SCHEMA_VERSION}")

    if installed > SCHEMA_VERSION:
        raise MigrationError(
            "数据结构版本高于当前代码支持的版本，疑似使用旧代码打开新数据（拒绝降级）",
            details={"installed": installed, "code": SCHEMA_VERSION},
        )

    version = installed
    while version < SCHEMA_VERSION:
        step = _STEPS.get(version)
        if step is None:
            raise MigrationError(
                "缺少迁移步骤，无法升级到下一版本",
                details={"from_version": version},
            )
        log(f"[migration] apply step {version} -> {version + 1}")
        state = step(state)
        version += 1
        state.setdefault("meta", {})["schema_version"] = version

    if version == installed:
        log("[migration] already at latest version; no-op (idempotent)")
    else:
        log(f"[migration] migrated {installed} -> {version}")
    return state
