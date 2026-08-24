"""整理器：去重、打标签、归类、抽关键词。

一期用内置规则实现，运行期不调 AI（对齐设计文档原则：不依赖网络也能跑）。
- 关键词：jieba 分词 + 词频统计
- 打标签：基于预置关键词库的规则匹配
- 归类：topic 已在采集时指定，未指定时按关键词归入候选主题
"""
from __future__ import annotations

import re
from typing import List

import jieba
import jieba.analyse

from . import store
from .models import Note


# 预置标签规则：关键词 → 标签
TAG_RULES = [
    (r"算法|AI|人工智能|机器学习|模型|大模型", "AI"),
    (r"编程|代码|开发|前端|后端|框架|开源", "编程"),
    (r"产品|需求|设计|交互|体验|用户", "产品设计"),
    (r"市场|营销|运营|增长|流量|品牌", "营销运营"),
    (r"管理|组织|团队|协作|效率", "管理"),
    (r"历史|哲学|人文|社会|文明|思想", "人文"),
    (r"科学|物理|生物|化学|宇宙|研究", "科学"),
    (r"金融|投资|经济|消费|商业", "商业金融"),
    (r"健康|饮食|运动|睡眠|心理", "生活健康"),
    (r"新闻|报道|时事|资讯", "资讯"),
]

# 候选主题规则（按关键词推断主题，供归类用）
TOPIC_RULES = [
    ("技术", r"算法|AI|人工智能|编程|代码|前端|后端|框架|开源|大模型"),
    ("商业", r"产品|市场|营销|运营|商业|创业|投资|增长"),
    ("人文", r"历史|哲学|人文|社会|文明|思想|文化"),
    ("科学", r"科学|物理|生物|化学|宇宙|研究|医学"),
    ("生活", r"健康|饮食|运动|睡眠|生活|心理"),
]


def clean_note(note_id: str, topic: str = "") -> dict:
    """整理单篇：抽关键词、打标签、补摘要。"""
    note = store.load_note(note_id, topic or None)
    if not note:
        raise FileNotFoundError(f"笔记不存在: {note_id}")

    keywords = extract_keywords(note.body, top_k=8)
    tags = suggest_tags(note.body, note.meta.tags)
    summary = note.meta.summary or summarize(note.body)

    note.meta.keywords = keywords
    note.meta.tags = sorted(set(tags))
    note.meta.summary = summary
    note.meta.status = "clean"
    store._write_note(note)
    return {"note_id": note_id, "keywords": keywords, "tags": note.meta.tags, "summary": summary}


def clean_all() -> dict:
    metas = store.list_notes()
    results = []
    for m in metas:
        try:
            r = clean_note(m.id, m.topic)
            results.append({"id": m.id, "ok": True})
        except Exception as e:  # noqa: BLE001
            results.append({"id": m.id, "ok": False, "error": str(e)})
    # 整理完成后重建索引（主题/标签/关键词），供报告与图谱消费
    from . import indexer
    index_summary = indexer.rebuild()
    return {"total": len(metas), "cleaned": sum(1 for r in results if r["ok"]), "results": results,
            "index": index_summary}


def extract_keywords(text: str, top_k: int = 8) -> List[str]:
    if not text or len(text) < 4:
        return []
    try:
        words = jieba.analyse.extract_tags(text, topK=top_k)
        return [w for w in words if len(w) > 1][:top_k]
    except Exception:  # noqa: BLE001
        return []


def suggest_tags(text: str, existing: List[str]) -> List[str]:
    tags = list(existing or [])
    for pattern, tag in TAG_RULES:
        if re.search(pattern, text) and tag not in tags:
            tags.append(tag)
    return tags


def infer_topic(text: str) -> str:
    for topic, pattern in TOPIC_RULES:
        if re.search(pattern, text):
            return topic
    return "默认主题"


def summarize(text: str, max_len: int = 120) -> str:
    """极简摘要：取正文前几句拼接。"""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    sentences = re.split(r"(?<=[。！？!?])", text)
    out = ""
    for s in sentences:
        if len(out) + len(s) > max_len:
            break
        out += s
    return out or text[:max_len]
