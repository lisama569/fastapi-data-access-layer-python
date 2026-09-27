"""统一错误类型。

错误类别通过异常类型即可区分：
- ValidationError: 请求数据校验失败 (400)
- NotFoundError: 资源不存在 (404)
- ConflictError: 并发修改冲突 / 唯一约束冲突 (409)
- InvalidCursorError: 游标非法、失效或与查询条件不匹配 (400)
- MigrationError: 数据结构版本不一致或存在不兼容变更 (500)
"""

from __future__ import annotations


class AppError(Exception):
    """所有业务错误的基类。"""

    error_type: str = "app_error"
    http_status: int = 400

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationError(AppError):
    error_type = "validation_error"
    http_status = 400


class NotFoundError(AppError):
    error_type = "not_found"
    http_status = 404


class ConflictError(AppError):
    """乐观锁版本冲突（expected_version 与当前版本不一致）。"""

    error_type = "conflict"
    http_status = 409


class InvalidCursorError(AppError):
    error_type = "invalid_cursor"
    http_status = 400


class MigrationError(AppError):
    error_type = "migration_error"
    http_status = 500
