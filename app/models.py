"""领域模型（持久化结构）。

记录以 dict 形式存储，Item 仅负责结构与序列化约定。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Item:
    id: int
    name: str
    version: int
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, row: dict) -> "Item":
        return cls(
            id=row["id"],
            name=row["name"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
