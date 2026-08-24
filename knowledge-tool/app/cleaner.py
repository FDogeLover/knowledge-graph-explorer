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
    """整理单篇：抽关键词、打标签、补摘要；source 型还做实体/概念识别与双链。"""
    note = store.load_note(note_id)
    if not note:
        raise FileNotFoundError(f"笔记不存在: {note_id}")

    keywords = extract_keywords(note.body, top_k=8)
    tags = suggest_tags(note.body, note.meta.tags)
    summary = note.meta.summary or summarize(note.body)

    note.meta.keywords = keywords
    note.meta.tags = sorted(set(tags))
    note.meta.summary = summary
    note.meta.status = "clean"

    # 双链：source → entity / concept（借鉴 Obsidian extract_entities.py）
    links = {}
    if note.meta.type == "source":
        links = link_entities_concepts(note)
        note.meta.related_entities = links.get("entities", [])
        note.meta.related_concepts = links.get("concepts", [])

    store._write_note(note)
    return {"note_id": note_id, "keywords": keywords, "tags": note.meta.tags,
            "summary": summary, "links": links}


def _find_existing_skeleton(name: str, kind: str) -> str | None:
    """实体/概念页归并：避免 AI 重复采集输出近似别名时产生并行节点。

    规则（保守，防误并）：
    1. 完全同名 → 直接复用（slug 相同）；
    2. 子串近似：一名称是另一名称的连续子串，且短名 ≥3 字、长度差 ≤6 → 复用已存在页
       （如 Libby/OverDrive⇄OverDrive、中国国家图书馆⇄国家图书馆）。
    """
    target = (name or "").strip()
    if not target:
        return None
    from .models import slugify
    nid = slugify(target)
    if store.load_meta(nid):
        return nid
    base = store.config.NOTES_DIR / kind
    if not base.exists():
        return None
    for folder in base.glob("*/"):
        meta_p = folder / "meta.json"
        if not meta_p.exists():
            continue
        m = NoteMeta.from_dict(store.load_json(meta_p))
        title = (m.title or "").strip()
        if not title or title == target:
            continue
        short, long = (title, target) if len(title) <= len(target) else (target, title)
        if len(short) < 3:
            continue
        if short in long and len(long) - len(short) <= 6:
            return m.id
    return None


def ensure_links(note_id: str) -> dict:
    """对单篇 source：解析其双链表，为不存在的实体/概念建骨架页。
    返回 {"entity": 新建数, "concept": 新建数}。
    """
    note = store.load_note(note_id)
    if not note:
        return {"entity": 0, "concept": 0}
    if note.meta.type != "source":
        return {"entity": 0, "concept": 0}
    links = link_entities_concepts(note)
    created = {"entity": 0, "concept": 0}
    for item in links["entities"]:
        target_id = _find_existing_skeleton(item["name"], "entity")
        up = upsert_entity_concept(item["name"], item["desc"], "entity", note_id,
                                   note.meta.topic, target_id=target_id)
        if up["action"] == "created":
            created["entity"] += 1
    for item in links["concepts"]:
        target_id = _find_existing_skeleton(item["name"], "concept")
        up = upsert_entity_concept(item["name"], item["desc"], "concept", note_id,
                                   note.meta.topic, target_id=target_id)
        if up["action"] == "created":
            created["concept"] += 1
    return created


def clean_all() -> dict:
    metas = store.list_notes()
    results = []
    created = {"entity": 0, "concept": 0}
    for m in metas:
        try:
            r = clean_note(m.id, m.topic)
            if r.get("links"):
                c = ensure_links(m.id)
                created["entity"] += c["entity"]
                created["concept"] += c["concept"]
            results.append({"id": m.id, "ok": True})
        except Exception as e:  # noqa: BLE001
            results.append({"id": m.id, "ok": False, "error": str(e)})
    # 整理完成后重建索引（主题/标签/关键词），供报告与图谱消费
    from . import indexer
    index_summary = indexer.rebuild()
    return {"total": len(metas), "cleaned": sum(1 for r in results if r["ok"]), "results": results,
            "index": index_summary, "skeletons_created": created}


def extract_keywords(text: str, top_k: int = 8) -> List[str]:
    if not text or len(text) < 4:
        return []
    try:
        # 先剥离 Markdown 语法，避免 `##`、`[[...]]` 等符号混入关键词
        clean = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
        clean = re.sub(r"[#*`_>|-]", " ", clean)
        words = jieba.analyse.extract_tags(clean, topK=top_k)
        return [w for w in words if len(w.strip(" #*`_>|-")) > 1][:top_k]
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


# ---------- 双链：实体 / 概念识别（借鉴 Obsidian extract_entities.py） ----------

def _parse_link_section(text: str, section: str) -> list:
    """解析正文中 `## 相关实体 / ## 相关概念` 区块的双链条目。
    支持 `- [[名称]] - 说明` 与 `| [[名称]] | 类型 | 说明 |` 两种形态。
    """
    out = []
    m = re.search(rf"##\s*{section}\n(.*?)(?=\n##\s|\Z)", text, re.DOTALL)
    if not m:
        return out
    for line in m.group(1).split("\n"):
        line = line.strip()
        if not line or line.startswith("|") and ("名称" in line or "---" in line):
            continue
        m2 = re.match(r"-\s*\[\[([^\]]+)\]\]\s*[-—]\s*(.+)", line)
        if m2:
            out.append({"name": m2.group(1).strip(), "desc": m2.group(2).strip()})
            continue
        m2 = re.match(r"\|?\s*\[\[([^\]]+)\]\]\s*\|\s*([^|]+?)\s*\|\s*([^|]+)\s*\|?", line)
        if m2:
            out.append({"name": m2.group(1).strip(), "desc": m2.group(3).strip()})
    # 去重（同名保留首个）
    seen, uniq = set(), []
    for item in out:
        if item["name"] not in seen:
            seen.add(item["name"])
            uniq.append(item)
    return uniq


def link_entities_concepts(note) -> dict:
    """对 source 笔记：识别正文中的实体/概念，返回双链列表与骨架页通道。

    返回 {"entities": [...], "concepts": [...]} —— 均带 type 标记，供建链。
    """
    entities = _parse_link_section(note.body, "相关实体")
    concepts = _parse_link_section(note.body, "相关概念")
    return {
        "entities": [{"name": e["name"], "desc": e["desc"], "type": "entity"} for e in entities],
        "concepts": [{"name": c["name"], "desc": c["desc"], "type": "concept"} for c in concepts],
    }


def upsert_entity_concept(name: str, desc: str, kind: str, source_id: str = "",
                          topic: str = "", target_id: str = "") -> dict:
    """创建/更新 entity 或 concept 骨架页（借鉴 Obsidian update_or_create_entity）。
    kind ∈ entity / concept；已存在则仅记录来源引用，不覆盖内容。
    target_id：归并目标（同一实体被别名引用时复用已存在页），留空走默认 slug。
    """
    from .models import Note, NoteMeta, now_iso, slugify

    note_id = target_id or slugify(name)
    existing = store.load_meta(note_id)
    today = now_iso()

    if existing:
        # 已存在骨架页：记录来源引用（别名归并也走这里，action=reused）
        body = existing and store.load_note(note_id)
        if body is not None and source_id and f"[[{source_id}]]" not in body.body:
            body.body = body.body + f"\n- [[{source_id}]]\n"
            body.meta.updated_at = today
            store._write_note(body)
        action = "reused" if target_id else ("updated" if source_id else "exists")
        return {"id": note_id, "kind": kind, "action": action}

    meta = NoteMeta(
        id=note_id,
        title=name,
        topic=topic or "",
        type=kind,
        summary=desc or f"{kind}「{name}」",
        created_at=today,
        updated_at=today,
        status="clean",
    )
    body = f"# {name}\n\n## 定义\n{desc or ''}\n" + (f"\n## 相关来源\n- [[{source_id}]]\n" if source_id else "")
    store._write_note(Note(meta=meta, body=body))
    return {"id": note_id, "kind": kind, "action": "created"}
