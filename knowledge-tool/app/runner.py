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
    MAX_FINISHED = 50  # 保留最近 N 条已完成任务，防止内存无限增长

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
            self._trim_locked()
        thread = threading.Thread(target=self._run, args=(task_id, fn, args, kwargs), daemon=True)
        thread.start()
        return task_id

    def _trim_locked(self) -> None:
        """上限裁剪：保留全部 running + 最近 MAX_FINISHED 条已完成（done/error）。"""
        finished = sorted(
            (t for t in self._tasks.values() if t["status"] in ("done", "error")),
            key=lambda x: x.get("finished_at") or x["created_at"],
            reverse=True,
        )
        if len(finished) <= self.MAX_FINISHED:
            return
        keep = {t["id"] for t in finished[: self.MAX_FINISHED]}
        for tid in list(self._tasks):
            if self._tasks[tid]["status"] in ("done", "error") and tid not in keep:
                del self._tasks[tid]

    def clear_finished(self) -> int:
        """清空全部已完成（done/error）任务，返回删除条数。"""
        with self._lock:
            removed = [tid for tid, t in self._tasks.items() if t["status"] in ("done", "error")]
            for tid in removed:
                del self._tasks[tid]
            return len(removed)

    def _run(self, task_id: str, fn: Callable, args: tuple, kwargs: dict) -> None:
        try:
            result = fn(*args, **kwargs)
            with self._lock:
                self._tasks[task_id].update(status="done", progress=1.0, result=result)
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._tasks[task_id].update(status="error", error=_friendly_error(e))
        finally:
            with self._lock:
                self._tasks[task_id]["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._trim_locked()   # 完成时也裁剪，防止『连续完成但不再提交』时超 50 条

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


# ---------- 错误中文映射 ----------
_PATTERNS = [
    # LLM 业务错误（Key/权限/模型不存在）——信息里带 "LLM"，与采集页 401/403 区分
    ("invalid_api_key", "API Key 无效：请到「设置」页重新填写"),
    ("api_key", "API Key 错误：请检查 Key 是否正确"),
    ("llm 返回 401", "鉴权失败（401）：API Key 无效或已过期，请到「设置」页重新填写"),
    ("llm 返回 403", "权限不足（403）：API Key 无权访问该模型或已限流"),
    ("model not found", "模型不存在或不可用：请检查模型名"),
    ("model_not_found", "模型不存在或不可用：请检查模型名"),
    ("not support", "模型能力不支持该参数：已尝试降级重试"),
    # 采集/网络类
    ("Request URL is missing protocol", "链接地址无效：需以 http:// 或 https:// 开头"),
    ("RemoteProtocolError", "网络协议错误：连接被中断，请稍后重试"),
    ("ConnectTimeout", "网络连接超时：无法访问目标服务器"),
    ("ConnectError", "网络连接失败：请检查网络或目标地址"),
    ("ReadTimeout", "读取超时：目标服务器响应过慢"),
    ("ReadError", "网络读取失败：连接被重置"),
    ("SSL", "TLS 证书/加密连接错误"),
    ("404", "资源不存在（404）"),
    ("403", "服务器拒绝访问（403，可能被反爬限制）"),
    ("401", "访问被拒绝（401）：链接可能需登录或已失效"),
    ("NameResolutionError", "域名解析失败：地址可能拼写有误"),
    ("UnicodeDecodeError", "内容编码解析失败（非 UTF-8）"),
]


def _friendly_error(e: Exception) -> str:
    """把异常转成操作者可读的中文提示。

    顺序：先做模式匹配——LLM 业务错误信息常已含中文（如「LLM 返回 401」），
    需要覆盖；未命中任何模式的纯中文信息才原样透传。
    """
    s = str(e)
    low = s.lower()
    for key, zh in _PATTERNS:
        if key in low:
            return zh
    if any("\u4e00" <= ch <= "\u9fff" for ch in s):
        return s
    return f"任务执行失败：{s[:200]}"


manager = TaskManager()
