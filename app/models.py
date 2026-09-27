"""Pydantic 模型：条目读写与分页响应。"""
from __future__ import annotations

from pydantic import BaseModel


class ItemCreate(BaseModel):
    name: str
    value: int


class ItemUpdate(BaseModel):
    name: str | None = None
    value: int | None = None
    version: int


class ItemOut(BaseModel):
    id: int
    name: str
    value: int
    version: int


class PageOut(BaseModel):
    items: list[ItemOut]
    next_cursor: str | None
