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
