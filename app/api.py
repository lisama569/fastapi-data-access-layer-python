"""FastAPI 应用装配。

- 启动时（lifespan）校验/执行数据结构迁移并落盘，版本不一致直接拒绝启动。
- 所有业务错误统一映射为 JSON：{"error": {"type", "message", "details"}}，
  通过 type 与 HTTP 状态码区分：validation_error(400) / invalid_cursor(400)
  / not_found(404) / conflict(409) / migration_error(500)。
- 数据文件路径由环境变量 DATA_FILE 指定；未设置时使用纯内存存储（不落盘）。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.errors import AppError
from app.migrations import run_migrations
from app.schemas import ItemCreate, ItemOut, ItemUpdate, PageOut
from app.storage import Storage
from app.unitofwork import UnitOfWork

router = APIRouter(prefix="/items", tags=["items"])


def _error_response(exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "error": {
                "type": exc.error_type,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


def create_app(*, storage: Storage | None = None) -> FastAPI:
    data_path = os.environ.get("DATA_FILE") or None
    store = storage if storage is not None else Storage(data_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 启动时校验数据结构版本并执行迁移；不兼容则抛 MigrationError 拒绝启动
        with store.lock:
            migrated = run_migrations(store.live_state())
            store.commit(migrated)  # 迁移结果落盘（幂等，无变更时内容不变）
        yield

    app = FastAPI(title="Data Access Layer", version="1.0.0", lifespan=lifespan)
    app.state.storage = store
    app.state.uow = UnitOfWork(store)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return _error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "type": "validation_error",
                    "message": "请求参数校验失败",
                    "details": {"errors": exc.errors()},
                }
            },
        )

    @router.post("", status_code=201, response_model=ItemOut)
    def create_item(body: ItemCreate):
        with app.state.uow.transaction() as tx:
            return tx.items.add(body.name)

    @router.get("/{item_id}", response_model=ItemOut)
    def get_item(item_id: int):
        return app.state.uow.read_repository().get(item_id)

    @router.patch("/{item_id}", response_model=ItemOut)
    def update_item(item_id: int, body: ItemUpdate):
        with app.state.uow.transaction() as tx:
            return tx.items.update(item_id, body.name, body.expected_version)

    @router.delete("/{item_id}", status_code=204)
    def delete_item(item_id: int):
        with app.state.uow.transaction() as tx:
            tx.items.delete(item_id)

    @router.get("", response_model=PageOut)
    def list_items(
        limit: int = 20,
        cursor: str | None = None,
        name_filter: str | None = None,
    ):
        items, next_cursor, has_more = app.state.uow.read_repository().list_page(
            limit, cursor, name_filter
        )
        return {"items": items, "next_cursor": next_cursor, "has_more": has_more}

    app.include_router(router)
    return app
