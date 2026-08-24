"""文件数据层：笔记以 meta.json + body.md 双文件存储，无重型数据库。

目录结构（借鉴 Obsidian wiki 的 source/entity/concept 分层）：
  data/
  ├─ notes/{source|entity|concept}/{slug}/meta.json + body.md
  ├─ templates/article.md + meta.schema.json
  ├─ reports/YYYY-MM-DD.md/.json
  ├─ tags.json / topics.json / index.json
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import List, Optional

from . import config
from .models import NOTE_TYPES, Note, NoteMeta


def ensure_initialized() -> bool:
    return config.NOTES_DIR.exists()


# ---------- 初始化向导 ----------
def init_knowledge_base(root: Optional[str] = None,
                        topics: Optional[List[str]] = None,
                        templates: Optional[bool] = True) -> dict:
    """运行初始化向导：建目录结构与模板。幂等，不覆盖已有笔记。"""
    if root:
        # 允许切换数据根目录（向导第 1 步）
        config.DATA_ROOT = Path(root)
        _rebind_paths()

    topics = topics or ["默认主题"]
    config.NOTES_DIR.mkdir(parents=True, exist_ok=True)
    for t in NOTE_TYPES:  # source / entity / concept 三层
        (config.NOTES_DIR / t).mkdir(parents=True, exist_ok=True)
    config.TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # 主题表（保留已存在的，合并新主题）
    topics_data = load_json(config.TOPICS_FILE, default={"topics": []})
    existing = set(topics_data["topics"])
    existing.update(t for t in topics if t and t not in existing)
    topics_data["topics"] = sorted(existing)
    save_json(config.TOPICS_FILE, topics_data)

    # 标签表
    tags_data = load_json(config.TAGS_FILE, default={"tags": []})
    save_json(config.TAGS_FILE, tags_data)

    # 模板
    if templates:
        _ensure_template("article.md", _ARTICLE_TEMPLATE)
        _ensure_template("meta.schema.json", _META_SCHEMA)

    return {
        "root": str(config.DATA_ROOT),
        "topics": topics_data["topics"],
        "templates": [p.name for p in config.TEMPLATES_DIR.glob("*")],
    }


def _rebind_paths() -> None:
    config.NOTES_DIR = config.DATA_ROOT / "notes"
    config.TEMPLATES_DIR = config.DATA_ROOT / "templates"
    config.REPORTS_DIR = config.DATA_ROOT / "reports"
    config.INDEX_FILE = config.DATA_ROOT / "index.json"
    config.TAGS_FILE = config.DATA_ROOT / "tags.json"
    config.TOPICS_FILE = config.DATA_ROOT / "topics.json"


# ---------- 笔记读写 ----------
def note_path(note_id: str, note_type: str = "source") -> Path:
    # 目录层级：notes/{type}/{slug}/  —— 借鉴 Obsidian wiki 的 source/entity/concept 分层
    if note_type not in NOTE_TYPES:
        note_type = "source"
    return config.NOTES_DIR / note_type / _safe(note_id)


def save_note(note: Note, dedup: bool = True) -> dict:
    """写入笔记（upsert）。返回 {note_id, dedup:{hit}} 等。"""
    d = note.to_dict()
    if not note.meta.fingerprint:
        note.meta.fingerprint = make_body_fingerprint(note.body)

    # 去重：指纹相同的既有笔记视为重复
    hit = False
    existing_id = None
    if dedup and note.meta.fingerprint:
        hit_info = find_by_fingerprint(note.meta.fingerprint, exclude=note.meta.id)
        if hit_info:
            hit, existing_id = True, hit_info

    if not hit:
        _write_note(note)
    else:
        # 去重命中：更新已有笔记的 updated_at 并返回
        if existing_id:
            _touch(existing_id)

    return {"note_id": note.meta.id, "dedup": {"hit": hit, "existing_id": existing_id}}


def _write_note(note: Note) -> None:
    folder = note_path(note.meta.id, note.meta.type)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(
        json.dumps(note.meta.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "body.md").write_text(note.body or "", encoding="utf-8")


def _touch(note_id: str) -> None:
    """去重命中时轻微更新时间戳。"""
    meta = load_meta(note_id)
    if meta:
        from .models import now_iso
        meta.updated_at = now_iso()
        save_json(note_path(note_id, meta.type) / "meta.json", meta.to_dict())


def load_meta(note_id: str) -> Optional[NoteMeta]:
    folder = note_path(note_id, _find_type(note_id))
    p = folder / "meta.json"
    if not p.exists():
        return None
    return NoteMeta.from_dict(load_json(p))


def load_note(note_id: str) -> Optional[Note]:
    meta = load_meta(note_id)
    if not meta:
        return None
    body_p = note_path(note_id, meta.type) / "body.md"
    body = body_p.read_text(encoding="utf-8") if body_p.exists() else ""
    return Note(meta=meta, body=body)


def list_notes(note_type: Optional[str] = None, status: Optional[str] = None,
               topic: Optional[str] = None) -> List[NoteMeta]:
    """列出笔记元数据，支持 type / status / topic 过滤。
    目录层级：notes/{source|entity|concept}/{slug}/meta.json
    """
    out: List[NoteMeta] = []
    types = [note_type] if note_type else list(NOTE_TYPES)
    for t in types:
        base = config.NOTES_DIR / t if t in NOTE_TYPES else config.NOTES_DIR
        if not base.exists():
            continue
        for folder in base.glob("*/"):
            meta_p = folder / "meta.json"
            if not meta_p.exists():
                continue
            meta = NoteMeta.from_dict(load_json(meta_p))
            if status and meta.status != status:
                continue
            if topic and meta.topic != topic:
                continue
            out.append(meta)
    out.sort(key=lambda m: m.updated_at, reverse=True)
    return out


def find_by_fingerprint(fingerprint: str, exclude: str = "") -> Optional[str]:
    for folder in config.NOTES_DIR.glob("*/*/"):
        if folder.name not in NOTE_TYPES:
            continue
        meta_p = folder / "meta.json"
        if not meta_p.exists():
            continue
        m = NoteMeta.from_dict(load_json(meta_p))
        if m.id != exclude and m.fingerprint == fingerprint:
            return m.id
    return None


def delete_note(note_id: str) -> bool:
    folder = note_path(note_id, _find_type(note_id))
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
        return True
    return False


def _find_type(note_id: str) -> str:
    """在 notes/{source|entity|concept} 下查找笔记所属类型。"""
    for t in NOTE_TYPES:
        if (config.NOTES_DIR / t / _safe(note_id) / "meta.json").exists():
            return t
    return "source"


def _safe(s: str) -> str:
    import re
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", s).strip("_") or "默认主题"


# ---------- 模板 ----------
def _ensure_template(name: str, content: str) -> None:
    p = config.TEMPLATES_DIR / name
    if not p.exists():
        p.write_text(content, encoding="utf-8")


# ---------- 通用 JSON ----------
def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def make_body_fingerprint(body: str) -> str:
    from .models import make_fingerprint
    return make_fingerprint(body)


# 内置模板内容
_ARTICLE_TEMPLATE = """---
title: {{title}}
topic: {{topic}}
source_url: {{source_url}}
---

# {{title}}

<!-- 正文 -->
"""

_META_SCHEMA = json.dumps({
    "id": "string",
    "title": "string",
    "source_url": "string|null",
    "created_at": "datetime",
    "updated_at": "datetime",
    "topic": "string",
    "tags": "string[]",
    "keywords": "string[]",
    "summary": "string",
    "status": "draft|clean|archived",
    "fingerprint": "string",
}, ensure_ascii=False, indent=2)
