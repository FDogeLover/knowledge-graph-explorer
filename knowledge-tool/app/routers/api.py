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
                                 body["url"], body.get("topic", ""), body.get("tags") or None)
    elif body.get("text"):
        task_id = manager.submit("粘贴正文", collector.collect_text,
                                 body["text"], body.get("title", ""),
                                 body.get("topic", ""), body.get("tags") or None)
    else:
        raise HTTPException(400, "需要 url 或 text")
    return {"task_id": task_id, "status": "running"}


# ---------- notes ----------
@router.get("/notes")
def list_notes(topic: str = "", status: str = ""):
    metas = store.list_notes(topic or None, status or None)
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
    """把笔记库聚合为知识图谱数据：主题为节点、共享标签为边。

    供前端「知识结构」图谱视图消费（整合自知识图谱探索器）。
    """
    metas = store.list_notes()
    topic_counter = {}
    tag_topic = {}
    for m in metas:
        t = m.topic or "默认主题"
        topic_counter.setdefault(t, 0)
        topic_counter[t] += 1
        for tag in (m.tags or []):
            tag_topic.setdefault(tag, set()).add(t)

    # 节点：主题
    palette = ["#e04f4f", "#f59e0b", "#2f9e7a", "#3b82f6", "#a855f7"]
    nodes, used = [], []
    for i, (t, c) in enumerate(sorted(topic_counter.items(), key=lambda x: -x[1])):
        color = palette[i % len(palette)]
        nodes.append({"id": t, "label": t, "category": "主题", "desc": f"主题「{t}」，含 {c} 篇笔记",
                      "qa": [{"q": f"这个主题收录了什么？", "a": f"共 {c} 篇笔记，可通过笔记库查看。"}],
                      "color": color})
        used.append(t)

    # 边：共享标签的相邻主题
    edges, seen = [], set()
    for tag, topics in tag_topic.items():
        topics = sorted(topics)
        for i in range(len(topics) - 1):
            key = (topics[i], topics[i + 1])
            if key in seen:
                continue
            seen.add(key)
            edges.append({"from": topics[i], "to": topics[i + 1],
                          "label": f"共标签·{tag}" if len(topics) == 2 else f"标签链·{tag}"})

    # 若无共享标签边（如各主题只有单篇、标签无交集），补相邻主题弱连接，
    # 让图谱始终有主干结构，便于浏览同一知识库下的主题全貌。
    if not edges and len(nodes) > 1:
        order = sorted(topic_counter)
        for i in range(len(order) - 1):
            edges.append({"from": order[i], "to": order[i + 1], "label": "同库相邻"})

    return {
        "topic": "知识库结构",
        "title": "知识结构 · 知识库工作流工具",
        "subtitle": "由笔记库实时聚合",
        "categories": [{"id": "主题", "name": "主题", "color": "#3b82f6"}],
        "nodes": nodes,
        "edges": edges,
    }
