"""索引器：维护标签表 / 主题表 / 关键词索引，供全局检索与结构统计。

全库整理后调用 rebuild()，把元数据汇总写入 index.json / tags.json / topics.json。
"""
from __future__ import annotations

from collections import Counter

from . import store


def rebuild() -> dict:
    """重建全库索引。返回统计概览。
    额外写入 note_flags（区块标记 + 正文摘录），供报告治理区与检索直接读取，
    避免每次都逐个 load_note 读全部正文。"""
    metas = store.list_notes()

    topic_counter = Counter()
    tag_counter = Counter()
    keyword_counter = Counter()
    flags = {}

    for m in metas:
        topic_counter[m.topic or "默认主题"] += 1
        for t in (m.tags or []):
            tag_counter[t] += 1
        for k in (m.keywords or []):
            keyword_counter[k] += 1
        body = ""
        note = store.load_note(m.id)
        if note:
            body = note.body or ""
        flags[m.id] = {
            "updated": m.updated_at or "",
            "excerpt": " ".join(body.split())[:500],
            "has_overview": "## 概览" in body,
            "has_timeline": "## 事件时间线" in body,
            "has_def": "## 定义" in body,
            "has_summary": "## 摘要" in body,
        }

    store.save_json(store.config.TOPICS_FILE, {"topics": sorted(topic_counter)})
    store.save_json(store.config.TAGS_FILE, {"tags": sorted(tag_counter)})
    store.save_json(store.config.INDEX_FILE, {
        "topics": dict(topic_counter.most_common()),
        "tags": dict(tag_counter.most_common()),
        "keywords": dict(keyword_counter.most_common(30)),
        "total": len(metas),
        "by_status": dict(Counter(m.status for m in metas)),
        "by_type": dict(Counter(m.type for m in metas)),
        "note_flags": flags,
    })
    return {
        "total": len(metas),
        "topics": len(topic_counter),
        "tags": len(tag_counter),
        "keywords": len(keyword_counter),
        "types": dict(Counter(m.type for m in metas)),
    }


# 模块级缓存：search 高频调用，避免每次重读 index.json
_search_cache = {"mtime": None, "flags": {}}


def _load_flags() -> dict:
    p = store.config.INDEX_FILE
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}
    if _search_cache["mtime"] == mtime:
        return _search_cache["flags"]
    flags = store.load_json(p, default={}).get("note_flags", {}) or {}
    _search_cache["mtime"] = mtime
    _search_cache["flags"] = flags
    return flags


def search(q: str, topic: str = "", tag: str = "") -> list:
    """简单全局检索：标题/摘要（正文摘录）/标签/关键词 包含匹配（免逐个读全文）。"""
    q = q.strip().lower()
    flags = _load_flags()
    results = []
    for m in store.list_notes(topic=topic or None):
        if tag and tag not in (m.tags or []):
            continue
        if q:
            fl = flags.get(m.id, {})
            hay = " ".join([m.title or "", m.topic or "", " ".join(m.tags or []),
                            " ".join(m.keywords or []), fl.get("excerpt", "")]).lower()
            if q not in hay:
                continue
        results.append(m.to_dict())
    return results
