"""数据模型：笔记（meta.json + body.md）与相关 DTO。

笔记是核心实体。每条笔记 = 一个目录下两份文件：
- meta.json：元数据（id/title/url/标签/分类/关键词/指纹等）
- body.md ：正文（Markdown）
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

# 状态枚举（对应设计文档）
NOTE_STATUS = ("draft", "clean", "archived")


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_fingerprint(text: str) -> str:
    """正文去重指纹：对压缩后的文本取 sha1。"""
    norm = " ".join(text.split()).strip().lower()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def slugify(text: str, max_len: int = 40) -> str:
    """生成笔记 id（slug）：保留中文/字母/数字，其余折成 -。"""
    import re
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", text).strip("-")
    return (s or "note")[:max_len]


@dataclass
class NoteMeta:
    id: str
    title: str = ""
    source_url: Optional[str] = None
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    topic: str = ""
    tags: list = field(default_factory=list)
    keywords: list = field(default_factory=list)
    summary: str = ""
    status: str = "draft"
    fingerprint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NoteMeta":
        allowed = {k: d.get(k) for k in (cls.__dataclass_fields__.keys())}
        return cls(**{k: v for k, v in allowed.items() if v is not None})


@dataclass
class Note:
    meta: NoteMeta
    body: str = ""

    def to_dict(self) -> dict:
        d = self.meta.to_dict()
        d["body"] = self.body
        return d
