# 数据访问层约定说明

本文档说明本服务在事务、并发、分页与数据结构版本管理上的语义约定，
以及对应的本地验证方法。

## 1. 事务与并发约定

### 1.1 事务边界（Unit of Work）

- 所有读写均通过 `app.uow.UnitOfWork` 包裹：

  ```python
  with UnitOfWork(db_path) as uow:
      repo = ItemRepository(uow.conn)
      repo.create(...)
      repo.update(...)
      uow.commit()        # 全部生效
  # 未 commit、或 with 块内抛异常 → 已执行的写入全部回滚
  ```

- 进入事务即执行 `BEGIN IMMEDIATE`，立即获取写锁；同一时刻只有一个
  写事务提交，写事务之间天然串行化，避免“最后写入者获胜”。
- HTTP 请求是事务的最外层：一个请求内的多次仓储写入要么全部生效，
  要么全部回滚（包括写入后因冲突异常导致的回滚）。
- 每个 `UnitOfWork` 使用独立的 SQLite 连接（`check_same_thread=False`
  但连接不跨线程共享），适配 FastAPI 线程池下的并发请求。
- SQLite 以 WAL 模式运行：读写互不长期阻塞，`busy_timeout=5000` 毫秒
  避免锁冲突立即报错。

### 1.2 乐观并发控制（不静默覆盖）

`items` 表携带单调递增的 `version` 列。更新与删除**必须**携带读取时
获得的版本号，SQL 层做条件更新：

```sql
UPDATE items SET ..., version = version + 1 WHERE id = ? AND version = ?;
```

- 影响行数为 1：更新成功，version +1；
- 影响行数为 0：记录已被并发修改 → 抛出 `ConflictError`，调用方必须
  重新读取、合并后重试，旧值不会静默覆盖新值。

### 1.3 可区分的错误类型

所有错误统一返回 `{"error": {"code", "message", "detail"}}`：

| 场景 | HTTP | `code` | 异常 |
|---|---|---|---|
| 请求体/参数校验失败 | 422 | `validation_failed` | `ValidationFailedError` |
| 资源不存在 | 404 | `not_found` | `NotFoundError` |
| 并发修改冲突（version 过期） | 409 | `conflict` | `ConflictError` |
| 游标非法/失效/条件不匹配 | 400 | `invalid_cursor` | `CursorError` |
| 数据结构版本不兼容/迁移失败 | 500 | `migration_error` | `MigrationError` |

判定顺序保证：先检查记录是否存在（404），再检查版本（409），因此
“删除不存在的记录”是 404，“记录存在但版本过期”是 409。

## 2. 游标分页语义

### 2.1 为什么是 keyset 而不是 offset

分页采用 **keyset pagination**，游标记录上一页最后一条记录的主键
（`last_id`），查询条件为 `WHERE id > :last_id ORDER BY id LIMIT n`：

- **翻页期间新增**：新记录的 id 大于已翻位置时在后续页正常出现；
  插入到已翻区间不可能发生（id 单调递增）→ 不会重复、不会遗漏。
- **翻页期间删除**：已删除记录不再返回；不会像 offset 分页那样因
  前序记录被删而整体错位、跳过一条数据（有专门测试证明）。
- 当一页恰好返回 `limit` 条时签发 `next_cursor`，否则为 `null`。

### 2.2 游标的安全校验

游标为不透明字符串，内含 `last_id`、查询条件指纹、签发时间，并有
HMAC-SHA256 签名。服务端对每个游标做四项校验，任一不过即返回
`400 invalid_cursor`，**绝不**从任意位置继续返回数据：

1. 格式可解析；
2. 签名匹配（防伪造、防篡改）；
3. 游标中的查询条件指纹（`name_prefix` 等参数的 SHA256）与本次
   请求一致——即不能用 A 条件的游标翻 B 条件的页；
4. 未超过 TTL（默认 300 秒），过期即失效，调用方需重新发起查询。

### 2.3 使用方式

```
GET /items?limit=20
GET /items?limit=20&cursor=<上一页的 next_cursor>
GET /items?name_prefix=apple&limit=20
```

`limit` 取值范围 1–100。变更任何过滤条件后必须重新取第一页，
复用旧游标会得到 `invalid_cursor`。

## 3. 数据结构版本兼容规则

- 数据库通过 `schema_migrations` 表记录已应用的版本号；代码侧的
  目标版本为 `app.migrations.SCHEMA_VERSION`，迁移脚本是
  `MIGRATIONS` 中版本号 → 幂等 SQL 的有序映射。
- **启动时校验**：应用启动调用 `migrate()`，先做兼容性检查再按需
  按序补齐缺失版本：
  - 库版本 == 代码版本：不做任何变更；
  - 库版本 < 代码版本：按序执行缺失脚本，全部脚本使用
    `IF NOT EXISTS` 等幂等写法，重复执行不报错、不破坏数据；
  - 库版本 > 代码版本：库由更新版本代码创建，本版本无法安全读写
    → 抛出 `migration_error`（`detail.reason = "db_newer_than_code"`）
    并拒绝启动；
  - 迁移链存在版本空洞 → `reason = "missing_migration"`。
- 整批迁移在**单个事务**内逐条执行，中途任何语句失败都会整体回滚，
  数据库停留在迁移前版本，修复后可安全重试。

### 如何新增一个本地可复现的版本

1. 将 `SCHEMA_VERSION` +1；
2. 在 `MIGRATIONS` 中新增对应版本号的**幂等** SQL（建表/建索引用
   `IF NOT EXISTS`；加列需先检查 `pragma table_info` 或保证全新库
   路径存在）；
3. 补充对应测试；
4. 已发布版本的迁移脚本只许追加、不许修改或删除。

## 4. 验证方法

### 4.1 运行测试

```bash
uv sync
uv run pytest -s
```

`-s` 可看到单测日志，每个关键用例都会打印“关键输入 + 判定依据 +
实际结果”。测试矩阵：

| 文件 | 覆盖点 |
|---|---|
| `tests/test_transactions.py` | 多写入提交、异常回滚、未提交回滚、事务内冲突连带回滚 |
| `tests/test_concurrency.py` | 8 线程并发更新恰好 1 胜其余冲突、409/404/422 可区分、过期 version 删除拒绝 |
| `tests/test_pagination.py` | 翻页中增删不重不漏、对比 offset 错位场景、非法/篡改/条件不匹配/过期游标拒绝、过滤分页完整性 |
| `tests/test_migrations.py` | 全新库迁移、重复迁移幂等且保数据、高版本库拒绝、失败迁移整体回滚、版本空洞拒绝 |

### 4.2 手动验证（HTTP）

```bash
uv run python main.py            # 默认使用 ./app.db，启动即迁移

curl -s -X POST localhost:8000/items -H 'content-type: application/json' \
  -d '{"name":"a","value":1}'
# 拿到 version 后并发用同一 version 发起两个 PUT，观察一个 200、一个 409：
curl -s -X PUT localhost:8000/items/1 -H 'content-type: application/json' \
  -d '{"value":2,"version":1}'
```

数据库文件可直接删除后重启，验证从零幂等建库；也可用
`sqlite3 app.db 'select * from schema_migrations;'` 查看当前版本。
