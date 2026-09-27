# fastapi-data-access-layer-python

基于 FastAPI + SQLite 的数据访问层示例，提供：

- **仓储式数据访问 + 工作单元事务边界**：同一事务内多次写入原子生效或全部回滚；
- **乐观并发控制**：`version` 条件更新，并发冲突返回 409，绝不静默覆盖；
- **稳定的 keyset 游标分页**：翻页期间增删不重不漏；非法/过期/条件不匹配的游标返回 400；
- **本地可复现的 schema 版本管理**：启动校验、幂等迁移、不兼容变更拒绝启动；
- **可区分的错误模型**：`validation_failed` / `not_found` / `conflict` / `invalid_cursor` / `migration_error`。

## 快速开始

```bash
uv sync
uv run pytest -s      # 运行全部测试（含并发、回滚、分页边界）
uv run python main.py # 启动服务，默认 ./app.db，可用 APP_DB_PATH 覆盖
```

## 目录结构

```
app/
  errors.py        # 可区分的错误类型与统一错误载荷
  db.py            # 连接管理（WAL、busy_timeout、外键）
  uow.py           # UnitOfWork 事务边界
  repositories.py  # ItemRepository：CRUD + 乐观锁 + keyset 查询
  pagination.py    # 游标编解码、HMAC 签名、TTL 与条件指纹校验
  migrations.py    # schema 版本表、幂等迁移、兼容性校验
  api.py           # FastAPI 路由与统一错误映射
tests/             # 事务 / 并发 / 分页 / 迁移 四组测试
docs/data-access.md  # 事务与并发约定、分页语义、版本兼容规则与验证方法
```

详细语义约定见 [docs/data-access.md](docs/data-access.md)。
