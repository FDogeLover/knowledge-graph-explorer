"""任务执行器：统一执行长耗时任务（采集/全库整理/生成报告），支持进度查询。

设计：创建任务返回 task_id，前端轮询 /api/tasks/{id}。
二期接定时调度时，只需为 runner 增加计划触发器，业务逻辑不变。
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Optional


class TaskManager:
    def __init__(self) -> None:
        self._tasks: dict = {}
        self._lock = threading.Lock()

    def submit(self, name: str, fn: Callable, *args, **kwargs) -> str:
        task_id = "d" + time.strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6]
        with self._lock:
            self._tasks[task_id] = {
                "id": task_id, "name": name, "status": "running",
                "progress": 0.0, "result": None, "error": None,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "finished_at": None,
            }
        thread = threading.Thread(target=self._run, args=(task_id, fn, args, kwargs), daemon=True)
        thread.start()
        return task_id

    def _run(self, task_id: str, fn: Callable, args: tuple, kwargs: dict) -> None:
        try:
            result = fn(*args, **kwargs)
            with self._lock:
                self._tasks[task_id].update(status="done", progress=1.0, result=result)
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._tasks[task_id].update(status="error", error=str(e))
        finally:
            with self._lock:
                self._tasks[task_id]["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    def update_progress(self, task_id: str, progress: float) -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["progress"] = progress

    def get(self, task_id: str) -> Optional[dict]:
        with self._lock:
            t = self._tasks.get(task_id)
            return dict(t) if t else None

    def list(self) -> list:
        with self._lock:
            return [dict(t) for t in sorted(self._tasks.values(), key=lambda x: x["created_at"], reverse=True)]


manager = TaskManager()
