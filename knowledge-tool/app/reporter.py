"""报告器：聚合统计 + 内容摘要 + 知识结构三视图。

报告数据以 JSON 返回（前端用图表渲染），并可导出 Markdown 存档。
知识结构视图提供主题/标签/关键词关联数据，供前端知识图谱视图消费。
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime

from . import store
from .models import NoteMeta


def build_daily() -> dict:
    """生成今日报告（数据视图）。"""
    metas = store.list_notes()
    today = datetime.now().strftime("%Y-%m-%d")

    # 按入库日期统计（近 7 日）
    day_counter = Counter(m.created_at[:10] for m in metas)
    last7 = []
    from datetime import timedelta
    for i in range(6, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        last7.append({"date": d, "count": day_counter.get(d, 0)})

    index = store.load_json(store.config.INDEX_FILE, default={})

    return {
        "date": today,
        "summary": {
            "total": len(metas),
            "today_new": day_counter.get(today, 0),
            "topics": len(index.get("topics", {})),
            "tags": len(index.get("tags", {})),
            "by_type": index.get("by_type", {}),
        },
        "trend": last7,
        "topic_dist": index.get("topics", {}),
        "tag_dist": index.get("tags", {}),
        "by_status": index.get("by_status", {}),
        "governance": build_governance(),
    }


def build_governance() -> dict:
    """今日内容治理洞察：按 updated_at 统计今天的增值操作。

    - updated_today     今天改动过的笔记数
    - overviews_updated 其中新增/更新「## 概览」的实体/概念页
    - timelines_updated 其中被处理过「## 事件时间线」的富实体页
    - definitions_kept  今天补过「定义」的骨架页
    - summaries_filled  今天补齐「## 摘要」的来源页
    - update_trend      近 7 日按 updated_at 的改动曲线
    """
    metas = store.list_notes()
    today = datetime.now().strftime("%Y-%m-%d")
    from datetime import timedelta

    # 区块信息来自 index.json 的 note_flags（rebuild 后可用），避免逐个 load_note 读全文
    flags = store.load_json(store.config.INDEX_FILE, default={}).get("note_flags", {}) or {}

    upd_counter = Counter((m.updated_at or "")[:10] for m in metas)
    stats = {
        "updated_today": 0, "overviews_updated": 0, "timelines_updated": 0,
        "definitions_kept": 0, "summaries_filled": 0,
    }
    for m in metas:
        if not (m.updated_at or "").startswith(today):
            continue
        stats["updated_today"] += 1
        f = flags.get(m.id, {})
        if f.get("has_overview"):
            stats["overviews_updated"] += 1
        if f.get("has_timeline"):
            stats["timelines_updated"] += 1
        if f.get("has_def"):
            stats["definitions_kept"] += 1
        if m.type == "source" and f.get("has_summary"):
            stats["summaries_filled"] += 1

    upd_trend = []
    for i in range(6, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        upd_trend.append({"date": d, "count": upd_counter.get(d, 0)})
    stats["update_trend"] = upd_trend
    return stats


def build_content() -> dict:
    """内容摘要视图：今日新入库 + 摘要 + 关键词云。"""
    metas = store.list_notes()
    today = datetime.now().strftime("%Y-%m-%d")
    today_notes = [m for m in metas if m.created_at[:10] == today]

    keyword_counter = Counter()
    for m in metas:
        for k in (m.keywords or []):
            keyword_counter[k] += 1

    return {
        "today_notes": [m.to_dict() for m in today_notes[:20]],
        "keywords": dict(keyword_counter.most_common(30)),
        "recent": [m.to_dict() for m in metas[:10]],
    }


def build_structure() -> dict:
    """知识结构视图：主题分布 + 主题内笔记排行 + 标签关联。"""
    metas = store.list_notes()
    topic_counter = Counter(m.topic or "默认主题" for m in metas)
    tag_counter = Counter(t for m in metas for t in (m.tags or []))

    # 主题内笔记排行（横向条形图数据）
    topic_rank = [{"topic": k, "count": v} for k, v in topic_counter.most_common(12)]

    # 标签关联（热度网格：出现频次）
    tag_top = [{"tag": k, "count": v} for k, v in tag_counter.most_common(20)]

    return {
        "topic_rank": topic_rank,
        "tag_heat": tag_top,
        "topic_count": len(topic_counter),
        "tag_count": len(tag_counter),
    }


def export_markdown() -> str:
    """把今日报告导出为 Markdown（可存档到 reports/）。"""
    data = build_daily()
    lines = [
        f"# 每日知识报告 · {data['date']}",
        "",
        f"- 总笔记：{data['summary']['total']}",
        f"- 今日新增：{data['summary']['today_new']}",
        f"- 主题数：{data['summary']['topics']}",
        f"- 标签数：{data['summary']['tags']}",
        "",
        "## 主题分布",
    ]
    for t, c in data["topic_dist"].items():
        lines.append(f"- {t}: {c}")
    text = "\n".join(lines)
    path = store.config.REPORTS_DIR / f"{data['date']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text
