"""FastAPI 应用入口：挂载 API 路由 + 托管原生前端静态页。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .routers import api

app = FastAPI(title="知识库工作流工具", version="0.1.0", description="把网页采集→整理→日报沉淀为本地 Web 管理台")


@app.on_event("startup")
def _start_scheduler():
    """启动定时调度器（后台线程，读 data/schedules.json 按 cron 触发任务）。"""
    try:
        from . import scheduler
        scheduler.start()
    except Exception:  # noqa: BLE001
        pass


@app.on_event("shutdown")
def _stop_scheduler():
    try:
        from . import scheduler
        scheduler.stop()
    except Exception:  # noqa: BLE001
        pass

app.include_router(api.router)

# 托管原生前端静态资源
app.mount("/css", StaticFiles(directory=config.FRONTEND_DIR / "css"), name="css")
app.mount("/js", StaticFiles(directory=config.FRONTEND_DIR / "js"), name="js")
app.mount("/graph", StaticFiles(directory=config.FRONTEND_DIR / "graph"), name="graph")


@app.get("/")
def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")


@app.get("/{page}")
def page(page: str):
    """静态页路由：/wizard /collect /clean /report /library /tasks /settings /graph-page"""
    mapping = {
        "wizard": "wizard.html",
        "collect": "collect.html",
        "clean": "clean.html",
        "report": "report.html",
        "library": "library.html",
        "tasks": "tasks.html",
        "settings": "settings.html",
        "graph-page": "graph.html",
    }
    if page in mapping:
        return FileResponse(config.FRONTEND_DIR / mapping[page])
    return JSONResponse({"detail": "Not Found"}, status_code=404)


@app.get("/api/health")
def health():
    return {"ok": True, "version": app.version}
