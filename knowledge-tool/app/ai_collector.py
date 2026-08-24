"""AI 采集器：操作者给出采集方向，由 LLM 按所选提示词模板完成内容组织与结构化。

流程：
1. 给定 direction（如「AI 芯片人才流动」）与 template（fast/deep/tutorial/自定义）
2. 读取该模板的系统提示词 → 调 LLM 生成结构化内容（JSON：title/summary/要点/实体/概念/思考）
3. 组装正文：摘要+要点 + `## 相关实体/概念` 双链区块 + （如有）`## 个人思考`
4. 走与「粘贴正文」一致路径入库（collect_text）→ cleaner 建骨架页/双链

这样操作者可针对不同场景选用不同提示词模板，无人维护格式成本；
且最终进入图谱的仍是同一套 schema（source→entity→concept 双链）。
"""
from __future__ import annotations

from . import cleaner, collector, llm, prompts

DEFAULT_TEMPLATE = "deep"

# 组装正文用后缀模板（把 LLM 返回的 lists 渲染成 markdown）
def _render_body(data: dict, direction: str) -> tuple:
    """返回 (body, title, summary, topic, tags, source_type)。"""
    title = str(data.get("title") or direction)[:60]
    summary = str(data.get("summary") or "").strip()
    topic = str(data.get("topic") or "综合")
    tags = [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()]
    source_type = str(data.get("source_type") or "AI采集")

    parts = []
    if summary:
        parts.append(summary)
    for p in (data.get("bullet_points") or []):
        if str(p).strip():
            parts.append(f"- {str(p).strip()}")
    entities = data.get("related_entities") or []
    concepts = data.get("related_concepts") or []
    if entities:
        parts.append("## 相关实体")
        for e in entities[:6]:
            parts.append(f"- [[{e.get('name', '').strip()}]] - {e.get('desc', '').strip()}")
    if concepts:
        parts.append("## 相关概念")
        for c in concepts[:6]:
            parts.append(f"- [[{c.get('name', '').strip()}]] - {c.get('desc', '').strip()}")
    thoughts = data.get("thoughts") or []
    if thoughts:
        parts.append("## 个人思考")
        for t in thoughts[:3]:
            parts.append(f"- {str(t).strip()}")
    body = "\n".join(parts) or direction
    return body, title, summary, topic, tags, source_type


def collect_by_direction(direction: str, template: str = DEFAULT_TEMPLATE) -> dict:
    """按所选模板让 LLM 组织内容并入库。返回任务结果（含 note_id/cleaned）。"""
    tpl = prompts.get_template(template) or prompts.get_template(DEFAULT_TEMPLATE)
    system = f"{tpl['prompt']}\n\n请严格只输出 JSON（不要 markdown 代码块）。"

    data = llm.chat_json(system, f"采集方向：{direction}", temperature=0.4)
    body, title, summary, topic, tags, source_type = _render_body(data, direction)

    result = collector.collect_text(body, title=title, topic=topic, tags=tags, source_type=source_type)
    nid = result["note_id"]
    try:
        r = cleaner.clean_note(nid)
        result["skeletons"] = cleaner.ensure_links(nid)
        result["cleaned"] = r
    except Exception as e:  # noqa: BLE001
        result["cleaned"] = {"error": str(e)}
    return {"note_id": nid, "title": title, "topic": topic,
            "template": tpl["name"], "collect": result, "cleaned": result.get("cleaned", {})}