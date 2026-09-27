# 数据访问层（事务一致性 · 并发安全 · 稳定分页 · 版本管理）

基于 FastAPI 的本地可复现数据访问能力。默认使用带原子落盘的 JSON 文件存储
（也支持纯内存模式），无需外部数据库即可演示与验证全部语义。

## 运行

```bash
uv sync
uv run pytest -s                      # 运行全部测试（-s 查看关键判定日志）
DATA_FILE=./data.json uv run uvicorn main:app --reload   # 带持久化启动
# 不设置 DATA_FILE 时为纯内存模式
```

## 一、事务与并发约定

- **事务边界**：所有写操作必须在 `with uow.transaction() as tx:` 内执行；
  事务外只允许快照读（`uow.read_repository()`）。
- **原子性**：进入事务时对当前状态做一次深拷贝作为工作副本，事务内的多次写入都作用于
  工作副本；正常退出时工作副本原子替换正式状态并落盘（全部生效），
  抛异常时工作副本直接丢弃（全部回滚），自增序列等内部状态也一并回滚。
- **并发安全（不静默覆盖）**：事务由全局可重入锁串行化，杜绝丢失更新；
  业务层更新采用**乐观并发控制**，请求必须携带所基于的 `expected_version`：
  - 与当前版本一致才允许更新，写入后 `version += 1`；
  - 不一致则返回 `409 conflict`，响应 details 中给出 `expected_version` 与
    `current_version`，冲突不会改变任何数据。
- **错误可区分**（统一响应体 `{"error": {"type", "message", "details"}}`）：

  | 场景 | HTTP | error.type |
  | --- | --- | --- |
  | 请求参数/业务校验失败 | 400 | `validation_error` |
  | 游标非法、篡改、与查询条件不匹配 | 400 | `invalid_cursor` |
  | 资源不存在 | 404 | `not_found` |
  | 并发版本冲突 | 409 | `conflict` |
  | 数据结构版本不兼容/拒绝降级 | 500 | `migration_error` |

## 二、分页语义

- 采用 **keyset（游标）分页**，按不可变的自增主键 `id` 升序，游标只记录
  “上一页最后一条记录的 id”这一位置。
- **翻页期间新增/删除不会重复或遗漏**：
  - 已返回页的 id 不可能再次返回（无重复）；
  - 不做 OFFSET 整体位移，不会因插入/删除而跳过中间仍存在的记录；
  - 删除游标位置之后的记录只让结果少一行；新增更大 id 的记录只出现在后续页。
  - 游标是位置而非行内容，游标指向的行被删除后仍可正常继续翻页。
- **游标校验**（失败返回 400 `invalid_cursor`，details.reason 明确区分）：
  - `malformed`：格式不是 `<body>.<signature>`；
  - `bad_signature`：HMAC-SHA256 签名不匹配（伪造/篡改/来自其他实例）；
  - `bad_payload` / `bad_version` / `id_missing`：内容、版本或位置非法；
  - `filter_mismatch`：游标生成时绑定的 `name_filter` 与当前请求不一致——
    明确拒绝，而不是从任意位置继续返回数据。
- 接口：`GET /items?limit=1..100&cursor=...&name_filter=...`，
  返回 `{items, next_cursor, has_more}`；`has_more=false` 时 `next_cursor=null`。

## 三、版本兼容规则

- 数据文件中以 `meta.schema_version` 记录结构版本；应用启动（lifespan）时
  调用 `run_migrations` 校验并顺序迁移，迁移结果立即落盘。
- 迁移按版本号顺序执行纯函数 step（0→1→…→当前），任意旧版本可升级到最新；
  缺少 meta 的文件按版本 0 处理。
- **幂等**：版本已是最新时迁移为 no-op，重复启动不会改写或破坏已有数据。
- **拒绝降级**：已安装版本高于当前代码支持的版本时抛 `migration_error`
  （details 含 `installed` / `code`），拒绝启动。
- **不兼容变更**：结构校验失败（如 items 不是对象、记录缺少 id）时抛
  `migration_error` 拒绝启动，错误信息与 details 可与其他错误区分。
- 新增不兼容升级时：在 `app/migrations.py` 提高 `SCHEMA_VERSION` 并在 `_STEPS`
  中补充对应 step 即可，启动日志会打印 `installed_version`、每个 step 与最终版本。

## 四、验证方法

```bash
uv run pytest -s                      # 全部 19 个用例，含关键输入与判定日志
uv run pytest tests/test_concurrency.py -s   # 并发冲突/事务回滚
uv run pytest tests/test_pagination.py -s    # 分页边界与游标拒绝
uv run pytest tests/test_migrations.py -s    # 版本校验/幂等/重启复现
```

重点覆盖：

- 两个线程基于同一版本并发更新同一条记录：恰有一个提交成功，另一个收到
  409 `conflict`，最终 `version` 只增加 1、数据保持一致；
- 事务内多次写入后因删除不存在记录而失败：全部写入回滚，序列不前进；
- 翻页中途新增/删除记录：已返回页不重复、仍存在的记录不遗漏；
- 非法/篡改/条件不匹配游标被拒绝，原因可区分；
- v0 数据升级、重复迁移无操作、拒绝降级、写入后重启数据完整复现。

测试日志通过 `log_case`（WARNING 级）与模块内 `[uow] [repo] [cursor]
[migration]` 前缀打印关键输入（id、expected/current version、filter、游标片段）
与判定依据（拒绝 reason、matched/returned 条数、最终状态）。

## API 一览

| 方法/路径 | 说明 |
| --- | --- |
| `POST /items` `{name}` → 201 | 创建记录，返回含 `version=1` 的记录 |
| `GET /items/{id}` | 单条查询，不存在返回 404 |
| `PATCH /items/{id}` `{name, expected_version}` | 乐观锁更新，冲突返回 409 |
| `DELETE /items/{id}` → 204 | 删除，不存在返回 404 |
| `GET /items?limit&cursor&name_filter` | 游标分页 |
