"""AI 增强整理：在规则整理（cleaner.clean_note）之上，可选地用 LLM 对单篇/批量笔记做增强。

混合模式原则（对应设计文档）：
- 规则整理是默认与离线兜底（cleaner.py，零依赖、全库快）。
- 本模块是可选的「AI 增强」：对链接/粘贴等无结构化正文的来源，用 LLM 补
  标题/摘要/标签/关键词，并识别实体/概念、写入双链、建骨架页——弥补规则整理
  对「任意正文实体识别」的短板。
- 未配置 LLM 时该功能不可用（前端给出引导），不影响规则整理。

与 ai_collector 的区别：
- ai_collector 从「方向」凭空生成结构化内容（采集入口）。
- 本模块针对**已有笔记**的正文做提炼增强（整理入口）。
"""
from __future__ import annotations

from . import cleaner, llm, store

# 提炼正文的最大输入长度（控制 token 成本）
BODY_LIMIT = 6000

_ENHANCE_PROMPT = """你是一个知识库整理助手。用户会给你一篇笔记正文（可能是网页正文或粘贴文本）。
任务：从中提炼出可用于知识图谱与检索的结构化信息——优化标题、一句话摘要、标签、关键词，并识别关键实体与关键概念。

- 实体：公司/组织/人物/产品/项目/国家机构 等具体对象
- 概念：趋势/领域/方法论/抽象名词 等
- 不臆造：只基于正文客观内容；正文过短或信息稀少时照实精简提取
- 描述保持 10~20 字

输出 JSON（严格如下结构，不要 markdown 代码块）：
{
  "title": "优化后的标题（≤30字）",
  "summary": "一句话摘要（≤60字）",
  "tags": ["标签1", "标签2", "标签3"],
  "keywords": ["关键词1", "关键词2", "关键词3"],
  "entities": [{"name": "实体名", "desc": "描述"}],
  "concepts": [{"name": "概念名", "desc": "描述"}]
}"""


def _llm_extract(note) -> dict:
    """调用 LLM 提炼结构化信息；未配置/失败时抛异常由调用方处理。"""
    body = (note.body or "").strip()
    user = f"笔记标题（可能为空）：{note.meta.title or ''}\n\n正文：\n{body[:BODY_LIMIT]}"
    return llm.chat_json(_ENHANCE_PROMPT, user, temperature=0.3)


def enhance_clean(note_id: str) -> dict:
    """对单篇笔记做 AI 增强整理：提炼 + 更新元数据 + 写入双链 + 建骨架页。

    规则整理结果（标签/关键词）会被合并保留，不覆盖。
    """
    note = store.load_note(note_id)
    if not note:
        raise FileNotFoundError(f"笔记不存在: {note_id}")
    if note.meta.type != "source":
        # 只增强来源笔记；实体/概念骨架页用聚合补全（aggregate_entity_concept），
        # 否则 LLM 会把实体自身写进 related 造成自环边污染图谱。
        return {"note_id": note_id, "skipped": True, "reason": "仅来源笔记可增强（实体/概念页请用聚合补全）"}
    body = (note.body or "").strip()
    if len(body) < 10:
        return {"note_id": note_id, "skipped": True, "reason": "正文过短，无需增强"}

    data = _llm_extract(note)

    # 标题 / 摘要 / 关键词
    title = str(data.get("title") or note.meta.title or "").strip()
    if title:
        note.meta.title = title[:60]
    summary = str(data.get("summary") or "").strip()
    if summary:
        note.meta.summary = summary[:120]
    keywords = [str(k).strip() for k in (data.get("keywords") or []) if str(k).strip()]
    if keywords:
        note.meta.keywords = keywords[:8]

    # 标签：合并规则标签 + LLM 标签（规则优先，去重）
    rule_tags = cleaner.suggest_tags(body, note.meta.tags)
    llm_tags = [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()]
    merged = list(rule_tags)
    for t in llm_tags:
        if t not in merged:
            merged.append(t)
    note.meta.tags = sorted(set(merged))
    note.meta.status = "clean"

    # 实体/概念：写入双链区块（仅当正文还没有相关区块时追加）
    ents = [e for e in (data.get("entities") or []) if str(e.get("name", "")).strip()]
    cons = [c for c in (data.get("concepts") or []) if str(c.get("name", "")).strip()]
    parts = []
    if ents and "## 相关实体" not in body:
        parts.append("## 相关实体")
        for e in ents[:6]:
            parts.append(f"- [[{e['name'].strip()}]] - {str(e.get('desc', '')).strip()}")
    if cons and "## 相关概念" not in body:
        parts.append("## 相关概念")
        for c in cons[:6]:
            parts.append(f"- [[{c['name'].strip()}]] - {str(c.get('desc', '')).strip()}")
    if parts:
        note.body = body + "\n\n" + "\n".join(parts)
    note.meta.related_entities = [e["name"].strip() for e in ents][:6]
    note.meta.related_concepts = [c["name"].strip() for c in cons][:6]

    store._write_note(note)

    # 建骨架页（已存在实体/概念复用，走 cleaner 的归并逻辑）
    skeletons = cleaner.ensure_links(note_id)
    return {"note_id": note_id, "title": note.meta.title, "tags": note.meta.tags,
            "keywords": note.meta.keywords, "entities": len(ents), "concepts": len(cons),
            "skeletons": skeletons}


def enhance_all() -> dict:
    """批量 AI 增强整理全库（逐篇调用，单篇失败不中断）。"""
    metas = store.list_notes()
    results = []
    created = {"entity": 0, "concept": 0}
    for m in metas:
        try:
            r = enhance_clean(m.id)
            sk = r.get("skeletons") or {}
            created["entity"] += sk.get("entity", 0)
            created["concept"] += sk.get("concept", 0)
            results.append({"id": m.id, "ok": r.get("skipped") is None,
                            "skipped": bool(r.get("skipped")), "error": r.get("reason")})
        except Exception as e:  # noqa: BLE001
            results.append({"id": m.id, "ok": False, "error": str(e)})
    from . import indexer
    index = indexer.rebuild()
    return {"total": len(metas), "cleaned": sum(1 for r in results if r["ok"]),
            "skipped": sum(1 for r in results if r.get("skipped")),
            "results": results, "skeletons_created": created, "index": index}


# ---------- 实体/概念页聚合补全（LLM） ----------
# 背景：骨架页建页时只有一句话「定义」，多来源引用后不再聚合更新 → 节点内容单薄。
# 这里把引用该实体/概念的各来源摘要喂给 LLM，生成一段「概览」写入骨架页。

_AGGREGATE_PROMPT = """你是知识库聚合助手。下面列出 {count} 篇来源笔记对「{name}」（{kind}）的描述片段。
任务：把各来源的信息综合成一段连贯、信息密度高的「概览」（150~250 字），覆盖：
- 它是什么（一句话定位）
- 各来源提供的核心事实/数据/观点
- 不同来源侧重的侧面或分歧
要求：只基于给定片段，不臆造；中文输出；不要分点列表。

来源片段：
{items}

输出 JSON（严格）：
{{"overview": "聚合概览（150~250字）"}}"""


def _referencing_sources(kind: str, title: str) -> list:
    """返回所有引用该实体/概念（按结构化双链名称精确匹配）的 source 摘要。"""
    from . import cleaner
    refs = []
    for m in store.list_notes(note_type="source"):
        note = store.load_note(m.id)
        if not note:
            continue
        try:
            links = cleaner.link_entities_concepts(note)
        except Exception:  # noqa: BLE001
            continue
        names = [it["name"] for it in links.get("entities", [])] if kind == "entity" \
            else [it["name"] for it in links.get("concepts", [])]
        if not any(nm == title for nm in names):
            continue
        summary = (note.meta.summary or "").strip()
        if not summary:
            # 摘要为空时截取正文首段
            summary = note.body.strip()[:120]
        refs.append({"title": note.meta.title or m.id, "summary": summary or "(无摘要)"})
    return refs


def aggregate_entity_concept(note_id: str) -> dict:
    """对单个实体/概念页做 LLM 聚合：收集引用来源 → 综合成概览 → 写入骨架页。"""
    note = store.load_note(note_id)
    if not note:
        raise FileNotFoundError(f"笔记不存在: {note_id}")
    if note.meta.type not in ("entity", "concept"):
        return {"note_id": note_id, "skipped": True, "reason": "仅实体/概念页可聚合"}

    kind_cn = "实体" if note.meta.type == "entity" else "概念"
    refs = _referencing_sources(note.meta.type, note.meta.title)
    if len(refs) < 2:
        return {"note_id": note_id, "kind": note.meta.type, "title": note.meta.title,
                "skipped": True, "reason": f"仅 {len(refs)} 篇来源引用，无需聚合"}

    items = "\n".join(f"- 《{r['title']}》：{r['summary']}" for r in refs)
    prompt = _AGGREGATE_PROMPT.format(count=len(refs), name=note.meta.title,
                                      kind=kind_cn, items=items)
    data = llm.chat_json(prompt, "请输出 JSON。", temperature=0.3)
    overview = str(data.get("overview") or "").strip()
    if not overview:
        return {"note_id": note_id, "skipped": True, "reason": "LLM 未返回概览"}

    body = note.body or ""
    if "## 概览" not in body:
        from .models import now_iso
        ov = f"## 概览\n{overview}\n"
        if body.lstrip().startswith("#"):
            first_nl = body.find("\n")
            if first_nl >= 0:
                body = body[:first_nl + 1] + "\n" + ov + body[first_nl + 1:]
            else:
                body = body + "\n\n" + ov
        else:
            body = ov + body
        note.body = body
        note.meta.updated_at = now_iso()
        store._write_note(note)
    return {"note_id": note_id, "kind": note.meta.type, "title": note.meta.title,
            "sources": len(refs), "overview": overview}


def aggregate_all() -> dict:
    """批量 LLM 聚合补全：对全部被 ≥2 来源引用的实体/概念页生成概览。"""
    metas = store.list_notes(note_type="")
    results = []
    aggregated = 0
    for m in metas:
        if m.type not in ("entity", "concept"):
            continue
        try:
            r = aggregate_entity_concept(m.id)
            if r.get("skipped"):
                results.append({"id": m.id, "title": m.title, "ok": False,
                                "skipped": True, "reason": r.get("reason")})
            else:
                aggregated += 1
                results.append({"id": m.id, "title": m.title, "ok": True, "sources": r.get("sources")})
        except Exception as e:  # noqa: BLE001
            results.append({"id": m.id, "title": m.title, "ok": False, "error": str(e)})
    from . import indexer
    index = indexer.rebuild()
    return {"aggregated": aggregated, "skipped": sum(1 for r in results if r.get("skipped")),
            "failed": sum(1 for r in results if not r.get("ok") and not r.get("skipped")),
            "results": results, "index": index}
