"""setup / collect / notes / reports / stats / tasks / graph 路由。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import cleaner, collector, indexer, reporter, store
from ..runner import manager

router = APIRouter(prefix="/api")


# ---------- setup ----------
class SetupBody(BaseModel):
    root: str | None = None
    topics: list[str] | None = None
    templates: bool = True


@router.get("/setup/status")
def setup_status():
    return {"initialized": store.ensure_initialized(), "root": str(store.config.DATA_ROOT)}


@router.post("/setup")
def run_setup(body: SetupBody):
    result = store.init_knowledge_base(root=body.root, topics=body.topics, templates=body.templates)
    return {"ok": True, **result}


class SeedBody(BaseModel):
    overwrite: bool = True


@router.post("/setup/seed")
def load_seed(body: SeedBody):
    """一键导入示例知识库（seed/），新手开箱即用，无需从零搭建。"""
    if not store.ensure_initialized():
        store.init_knowledge_base()
    result = store.import_seed(overwrite=body.overwrite)
    return {"ok": True, **result}


# ---------- collect ----------
class CollectUrlBody(BaseModel):
    url: str
    topic: str = ""
    tags: list[str] | None = None


class CollectTextBody(BaseModel):
    text: str
    title: str = ""
    topic: str = ""
    tags: list[str] | None = None


@router.post("/collect")
def collect(body: dict):
    """采集入口：body 含 url 或 text，返回任务 id（后台执行）。"""
    if body.get("url"):
        task_id = manager.submit("采集链接", collector.collect_url,
                                 body["url"], body.get("topic", ""), body.get("tags") or None,
                                 body.get("source_type", "网页"))
    elif body.get("text"):
        task_id = manager.submit("粘贴正文", collector.collect_text,
                                 body["text"], body.get("title", ""),
                                 body.get("topic", ""), body.get("tags") or None,
                                 body.get("source_type", "网页"))
    else:
        raise HTTPException(400, "需要 url 或 text")
    return {"task_id": task_id, "status": "running"}


# ---------- notes ----------
@router.get("/notes")
def list_notes(note_type: str = "", status: str = "", topic: str = ""):
    metas = store.list_notes(note_type or None, status or None, topic or None)
    return {"total": len(metas), "notes": [m.to_dict() for m in metas]}


@router.get("/notes/{note_id}")
def note_detail(note_id: str):
    note = store.load_note(note_id)
    if not note:
        raise HTTPException(404, "笔记不存在")
    return note.to_dict()


class CleanBody(BaseModel):
    topic: str = ""


@router.post("/notes/{note_id}/clean")
def clean_one(note_id: str, body: CleanBody):
    try:
        return {"ok": True, **cleaner.clean_note(note_id, body.topic)}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/notes/clean-all")
def clean_all():
    task_id = manager.submit("全库整理", cleaner.clean_all)
    return {"task_id": task_id, "status": "running"}


@router.delete("/notes/{note_id}")
def delete_one(note_id: str):
    ok = store.delete_note(note_id)
    if not ok:
        raise HTTPException(404, "笔记不存在")
    return {"ok": True}


@router.get("/search")
def search(q: str = "", topic: str = "", tag: str = ""):
    return {"results": indexer.search(q, topic, tag)}


# ---------- reports ----------
@router.post("/reports/daily")
def gen_daily():
    task_id = manager.submit("生成今日报告", reporter.build_daily)
    return {"task_id": task_id, "status": "running"}


@router.get("/reports/daily/latest")
def daily_latest():
    return reporter.build_daily()


@router.get("/reports/content")
def report_content():
    return reporter.build_content()


@router.post("/reports/export")
def export_report():
    md = reporter.export_markdown()
    return {"ok": True, "markdown": md}


# ---------- stats ----------
@router.get("/stats/overview")
def stats_overview():
    return {
        "daily": reporter.build_daily(),
        "content": reporter.build_content(),
        "structure": reporter.build_structure(),
    }


# ---------- tasks ----------
@router.get("/tasks")
def tasks_list():
    return {"tasks": manager.list()}


@router.get("/tasks/{task_id}")
def task_detail(task_id: str):
    t = manager.get(task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    return t


@router.get("/topics")
def topics_list():
    d = store.load_json(store.config.TOPICS_FILE, default={"topics": []})
    return d


@router.get("/tags")
def tags_list():
    d = store.load_json(store.config.TAGS_FILE, default={"tags": []})
    return d


# ---------- graph（知识结构视图数据） ----------
@router.get("/graph/data")
def graph_data():
    """知识图谱：基于 source→entity→concept 双链关系网（借鉴 Obsidian wiki）。

    节点分三类：来源 source / 实体 entity / 概念 concept；
    边 = 双链：source→entity（相关实体）、source→concept（相关概念）、
             entity→concept（同属实体的概念，由共享来源串联）。
    """
    metas = store.list_notes()
    by_id = {m.id: m for m in metas}
    palette = {"source": "#4c8bf5", "entity": "#f59e0b", "concept": "#a855f7"}
    names = {"source": "来源", "entity": "实体", "concept": "概念"}

    nodes, nids = [], set()
    for m in metas:
        if m.id in nids:
            continue
        nids.add(m.id)
        color = palette.get(m.type, "#3b82f6")
        node_type = names.get(m.type, m.type)
        nodes.append({
            "id": m.id, "label": m.title or m.id, "category": node_type,
            "desc": m.summary or f"{node_type}「{m.title or m.id}」",
            "color": color, "type": m.type,
            "qa": [{"q": f"这是什么{node_type}？",
                    "a": (m.summary or f"{node_type}「{m.title or m.id}」") +
                         (f"，收录于主题「{m.topic or '未归类'}」。" if m.topic else "。")}],
        })

    # 边：仅连接确实存在的节点
    edges, seen = [], set()

    def link(a_id, b_id, label):
        if a_id not in by_id or b_id not in by_id:
            return
        key = tuple(sorted((a_id, b_id)))
        if key in seen:
            return  # 已连过，跳过
        seen.add(key)
        edges.append({"from": a_id, "to": b_id, "label": label})

    for m in metas:
        for e in (m.related_entities or []):
            name = e.get("name", e) if isinstance(e, dict) else e
            link(m.id, slug_a(name), f"关联实体")
        for c in (m.related_concepts or []):
            name = c.get("name", c) if isinstance(c, dict) else c
            link(m.id, slug_a(name), f"关联概念")

    return {
        "topic": "知识库结构",
        "title": "知识结构 · 知识库工作流工具",
        "subtitle": "由笔记库双链（source→entity→concept）实时聚合",
        "categories": [
            {"id": "来源", "name": "来源", "color": palette["source"]},
            {"id": "实体", "name": "实体", "color": palette["entity"]},
            {"id": "概念", "name": "概念", "color": palette["concept"]},
        ],
        "nodes": nodes,
        "edges": edges,
    }


def slug_a(name: str) -> str:
    """"兄弟 slug：与 cleaner/extract 创建的骨架页 id 一致"""
    from ..models import slugify
    return slugify(name)
