"""索引器：维护标签表 / 主题表 / 关键词索引，供全局检索与结构统计。

全库整理后调用 rebuild()，把元数据汇总写入 index.json / tags.json / topics.json。
"""
from __future__ import annotations

from collections import Counter

from . import store


def rebuild() -> dict:
    """重建全库索引。返回统计概览。"""
    metas = store.list_notes()

    topic_counter = Counter()
    tag_counter = Counter()
    keyword_counter = Counter()

    for m in metas:
        topic_counter[m.topic or "默认主题"] += 1
        for t in (m.tags or []):
            tag_counter[t] += 1
        for k in (m.keywords or []):
            keyword_counter[k] += 1

    store.save_json(store.config.TOPICS_FILE, {"topics": sorted(topic_counter)})
    store.save_json(store.config.TAGS_FILE, {"tags": sorted(tag_counter)})
    store.save_json(store.config.INDEX_FILE, {
        "topics": dict(topic_counter.most_common()),
        "tags": dict(tag_counter.most_common()),
        "keywords": dict(keyword_counter.most_common(30)),
        "total": len(metas),
        "by_status": dict(Counter(m.status for m in metas)),
        "by_type": dict(Counter(m.type for m in metas)),
    })
    return {
        "total": len(metas),
        "topics": len(topic_counter),
        "tags": len(tag_counter),
        "keywords": len(keyword_counter),
        "types": dict(Counter(m.type for m in metas)),
    }


def search(q: str, topic: str = "", tag: str = "") -> list:
    """简单全局检索：标题/正文/标签/关键词 包含匹配。"""
    q = q.strip().lower()
    results = []
    for m in store.list_notes(topic=topic or None):
        if tag and tag not in (m.tags or []):
            continue
        if q:
            note = store.load_note(m.id)
            hay = " ".join([m.title, m.topic, " ".join(m.tags or []), " ".join(m.keywords or []),
                            (note.body if note else "")]).lower()
            if q not in hay:
                continue
        results.append(m.to_dict())
    return results
