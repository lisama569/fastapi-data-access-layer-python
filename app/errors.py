"""统一的错误类型：冲突、不存在、校验失败、游标非法、迁移不兼容必须可区分。"""
from __future__ import annotations


class AppError(Exception):
    """所有业务错误的基类，携带稳定的机器可读 code。"""

    code: str = "internal_error"
    http_status: int = 500

    def __init__(self, message: str = "", *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message or self.code
        self.detail = detail or {}

    def to_payload(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "detail": self.detail}}


class ValidationFailedError(AppError):
    code = "validation_failed"
    http_status = 422


class NotFoundError(AppError):
    code = "not_found"
    http_status = 404


class ConflictError(AppError):
    """并发修改同一条记录导致的乐观锁冲突。"""

    code = "conflict"
    http_status = 409


class CursorError(AppError):
    """游标非法、已失效或与查询条件不匹配。"""

    code = "invalid_cursor"
    http_status = 400


class MigrationError(AppError):
    """数据结构版本不一致或不兼容。"""

    code = "migration_error"
    http_status = 500
