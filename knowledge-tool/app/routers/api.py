"""setup / collect / notes / reports / stats / tasks / graph 路由。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import cleaner, collector, indexer, llm, reporter, store
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


class DirectionBody(BaseModel):
    direction: str


@router.post("/collect/direction")
def collect_direction(body: DirectionBody):
    """AI 采集：操作者只给方向，由 LLM 组织内容并按规范入库（含双链/骨架页）。"""
    from .. import ai_collector
    if not llm.is_configured():
        raise HTTPException(400, "未配置 LLM API Key（设置页填写 或 环境变量 LLM_API_KEY）")
    direction = body.direction.strip()
    if not direction:
        raise HTTPException(400, "请填写采集方向")
    task_id = manager.submit("AI方向采集", ai_collector.collect_by_direction, direction)
    return {"task_id": task_id, "status": "running"}


class LLMBody(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    provider: str = "auto"


@router.get("/settings/llm")
def llm_status():
    cfg = llm.get_llm_config()
    return {"configured": llm.is_configured(),
            "base_url": cfg["base_url"],
            "model": cfg["model"],
            "provider": cfg["provider"],
            "api_key_set": bool(cfg["api_key"])}


@router.post("/settings/llm")
def llm_save(body: LLMBody):
    cfg = llm.save_llm_config({"api_key": body.api_key, "base_url": body.base_url,
                               "model": body.model, "provider": body.provider})
    return {"ok": True, "configured": bool(cfg["api_key"]), "model": cfg["model"], "provider": cfg["provider"]}


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


# ---------- schedules（定时调度） ----------
from .. import scheduler as sched


class ScheduleBody(BaseModel):
    id: str = ""
    name: str = ""
    action: str = "clean"          # collect / clean / report
    cron: str = "0 8 * * *"
    enabled: bool = True
    direction: str = ""


@router.get("/schedules")
def schedules_list():
    return {"schedules": sched.load_schedules(), "actions": {k: v[0] for k, v in sched.ACTIONS.items()}}


@router.post("/schedules")
def schedules_add(body: ScheduleBody):
    try:
        item = sched.add_schedule(body.name or body.action, body.action, body.cron,
                                  enabled=body.enabled, direction=body.direction)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "schedule": item}


@router.put("/schedules/{sid}")
def schedules_update(sid: str, body: ScheduleBody):
    try:
        item = sched.update_schedule(sid, name=body.name, action=body.action, cron=body.cron,
                                     enabled=body.enabled, direction=body.direction)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not item:
        raise HTTPException(404, "定时任务不存在")
    return {"ok": True, "schedule": item}


@router.delete("/schedules/{sid}")
def schedules_delete(sid: str):
    if not sched.delete_schedule(sid):
        raise HTTPException(404, "定时任务不存在")
    return {"ok": True}


@router.post("/schedules/{sid}/toggle")
def schedules_toggle(sid: str):
    cur = next((i for i in sched.load_schedules() if i.get("id") == sid), None)
    if not cur:
        raise HTTPException(404, "定时任务不存在")
    item = sched.update_schedule(sid, enabled=not cur.get("enabled", True))
    if not item:
        raise HTTPException(404, "定时任务不存在")
    return {"ok": True, "schedule": item}


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

    # 反向邻接：记录每个实体/概念被哪些来源关联（供详情面板展示双向关联）
    reverse = {m.id: {"entities": [], "concepts": []} for m in metas}
    for m in metas:
        if m.type == "source":
            for e in (m.related_entities or []):
                nm = e.get("name", e) if isinstance(e, dict) else str(e)
                tid = slug_a(nm)
                if tid in reverse and tid != m.id:
                    reverse[tid]["entities"].append(m.id)
            for c in (m.related_concepts or []):
                nm = c.get("name", c) if isinstance(c, dict) else str(c)
                tid = slug_a(nm)
                if tid in reverse and tid != m.id:
                    reverse[tid]["concepts"].append(m.id)

    nodes, nids = [], set()
    for m in metas:
        if m.id in nids:
            continue
        nids.add(m.id)
        color = palette.get(m.type, "#3b82f6")
        node_type = names.get(m.type, m.type)
        # 合并前向+反向关联：source 有前向，entity/concept 靠反向
        if m.type == "source":
            links = {
                "entities": [e.get("name", e) if isinstance(e, dict) else str(e) for e in (m.related_entities or [])],
                "concepts": [c.get("name", c) if isinstance(c, dict) else str(c) for c in (m.related_concepts or [])],
            }
        else:
            links = {
                "entities": reverse.get(m.id, {}).get("entities", []),
                "concepts": reverse.get(m.id, {}).get("concepts", []),
            }
        nodes.append({
            "id": m.id, "label": m.title or m.id, "category": node_type,
            "desc": m.summary or f"{node_type}「{m.title or m.id}」",
            "color": color, "type": m.type,
            "keywords": m.keywords or [],
            "tags": m.tags or [],
            "date_published": m.date_published or m.created_at,
            "url": m.source_url or "",
            "links": links,
            # 正文（Markdown，供详情面板富文本渲染）
            "body": (note_body(m.id) or "")[:3000],
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


def note_body(note_id: str) -> str | None:
    """读取笔记正文（markdown），供图谱详情面板富文本渲染。"""
    note = store.load_note(note_id)
    return note.body if note else None
