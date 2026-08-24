"""AI 采集器：操作者只需给出采集方向，由 LLM 完成内容组织与结构化。

流程：
1. 给定 direction（如「AI 芯片人才流动」/「苹果新品发布」）
2. 调 LLM 生成一篇结构化的 source 内容（JSON：title/summary/topic/tags/
   related_entities/related_concepts/body，其中 body 内自动含 ## 相关实体/概念 区块）
3. 以与「粘贴正文」一致的路径入库（collect_text）→ 由 cleaner 建骨架页/双链

这样操作者不需要手动维护 `## 相关实体` 格式规范——由 AI 代劳，
且最终进入图谱的仍是同一套 schema（source→entity→concept 双链）。
"""
from __future__ import annotations

from . import cleaner, collector, llm

SYSTEM_PROMPT = """你是一个知识库采集助手。用户会给你一个要采集的方向（主题/话题/问题）。
你的任务：把该方向组织成一篇**结构清晰的知识来源笔记**，以便存入知识库并融入知识图谱。

要求：
- 内容准确、客观，用中文写成一段 100-220 字的正文（含关键要点）；
- 识别出这条内容涉及的**实体**（公司/组织/产品/人物等具体名词）与**概念**（抽象名词/领域/趋势）；
- 每个实体/概念给一句 10-20 字的描述（说明它与本内容的关联）。

输出 JSON（严格如下结构，不要多余字段）：
{
  "title": "主标题（不超过 30 字）",
  "summary": "一句话摘要（不超过 60 字）",
  "topic": "归入的主题（如 科技/商业/人文/生活，若不确定用 综合）",
  "tags": ["标签1", "标签2"],
  "source_type": "AI采集",
  "related_entities": [{"name": "实体名", "desc": "描述"}],
  "related_concepts": [{"name": "概念名", "desc": "描述"}],
  "paragraphs": ["正文段落1", "正文段落2", "正文段落3"]
}

注意：实体/概念名称用中文或通用简称，作的越精炼越好，数量各 1-3 个即可；
不要在 related_* 中出现与标题重复的通用词。
"""


def collect_by_direction(direction: str) -> dict:
    """按用户给出的采集方向，让 LLM 组织内容并入库。返回任务结果同 collect_text。"""
    data = llm.chat_json(SYSTEM_PROMPT, f"采集方向：{direction}", temperature=0.4)

    title = str(data.get("title") or direction)[:60]
    # 组装正文：段落后追加双链区块（与人工采集规范一致，供 cleaner 解析）
    paras = data.get("paragraphs") or [str(data.get("summary") or direction)]
    body_parts = [p.strip() for p in paras if str(p).strip()]
    entities = data.get("related_entities") or []
    concepts = data.get("related_concepts") or []
    if entities:
        body_parts.append("## 相关实体")
        for e in entities[:6]:
            body_parts.append(f"- [[{e.get('name', '').strip()}]] - {e.get('desc', '').strip()}")
    if concepts:
        body_parts.append("## 相关概念")
        for c in concepts[:6]:
            body_parts.append(f"- [[{c.get('name', '').strip()}]] - {c.get('desc', '').strip()}")
    body = "\n".join(body_parts)

    summary = str(data.get("summary") or "")
    topic = str(data.get("topic") or "综合")
    tags = [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()]
    source_type = str(data.get("source_type") or "AI采集")

    # 走与「粘贴正文」同样的入库 + 整理链路（自动建实体/概念骨架页 + 双链）
    result = collector.collect_text(body, title=title, topic=topic, tags=tags, source_type=source_type)
    nid = result["note_id"]

    note = cleaner_after(nid)
    return {"note_id": nid, "title": title, "topic": topic, "collect": result, "cleaned": note}


def cleaner_after(note_id: str) -> dict:
    """入库后整理一次，确保 summary/关键词/双链落库（存在则更新，不强建）。"""
    try:
        r = cleaner.clean_note(note_id)
        # 骨架页：库里没有的实体/概念建出来
        created = cleaner.ensure_links(note_id)
        r["skeletons"] = created
        return r
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}