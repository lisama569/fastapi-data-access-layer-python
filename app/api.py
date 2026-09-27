"""FastAPI 路由：统一错误映射、事务边界与分页参数解析。"""
from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.db import connection
from app.errors import AppError, ValidationFailedError
from app.migrations import migrate
from app.models import ItemCreate, ItemOut, ItemUpdate, PageOut
from app.pagination import compute_filter_hash, decode_cursor, issue_cursor
from app.repositories import ItemRepository
from app.uow import UnitOfWork

logger = logging.getLogger("app.api")

CURSOR_TTL_SECONDS = 300.0
MAX_PAGE_SIZE = 100


def create_app(db_path: str) -> FastAPI:
    app = FastAPI(title="data-access-layer")
    app.state.db_path = db_path

    @app.on_event("startup")
    def _startup() -> None:
        # 启动时校验并迁移数据结构版本；不兼容则拒绝启动
        with connection(db_path) as conn:
            migrate(conn)
        logger.info("startup: schema migrated, db_path=%s", db_path)

    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        err = ValidationFailedError("请求参数校验失败", detail={"errors": exc.errors()})
        return JSONResponse(status_code=err.http_status, content=err.to_payload())

    @app.post("/items", status_code=201, response_model=ItemOut)
    def create_item(data: ItemCreate) -> ItemOut:
        with UnitOfWork(db_path) as uow:
            item = ItemRepository(uow.conn).create(data)
            uow.commit()
            return item

    @app.get("/items/{item_id}", response_model=ItemOut)
    def get_item(item_id: int) -> ItemOut:
        with UnitOfWork(db_path) as uow:
            item = ItemRepository(uow.conn).get(item_id)
            uow.commit()
            return item

    @app.put("/items/{item_id}", response_model=ItemOut)
    def update_item(item_id: int, data: ItemUpdate) -> ItemOut:
        with UnitOfWork(db_path) as uow:
            item = ItemRepository(uow.conn).update(item_id, data)
            uow.commit()
            return item

    @app.delete("/items/{item_id}", status_code=204)
    def delete_item(item_id: int, version: int = Query(...)) -> None:
        with UnitOfWork(db_path) as uow:
            ItemRepository(uow.conn).delete(item_id, version=version)
            uow.commit()

    @app.get("/items", response_model=PageOut)
    def list_items(
        limit: int = Query(20, ge=1, le=MAX_PAGE_SIZE),
        cursor: str | None = Query(None),
        name_prefix: str | None = Query(None),
    ) -> PageOut:
        filters = {"name_prefix": name_prefix}
        filter_hash = compute_filter_hash(filters)
        last_id = 0
        if cursor is not None:
            decoded = decode_cursor(cursor, filter_hash=filter_hash, ttl_seconds=CURSOR_TTL_SECONDS)
            last_id = decoded.last_id
        with UnitOfWork(db_path) as uow:
            items = ItemRepository(uow.conn).list_after(
                last_id=last_id, limit=limit, name_prefix=name_prefix
            )
            uow.commit()
        next_cursor = issue_cursor(items[-1].id, filter_hash) if len(items) == limit else None
        return PageOut(items=items, next_cursor=next_cursor)

    return app
