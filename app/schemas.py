"""API 请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ItemUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=1)


class ItemOut(BaseModel):
    id: int
    name: str
    version: int
    created_at: str
    updated_at: str


class PageOut(BaseModel):
    items: list[ItemOut]
    next_cursor: str | None = None
    has_more: bool
