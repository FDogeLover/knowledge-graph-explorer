"""定时调度器：cron 到点自动触发采集/整理/日报（二期核心）。

- 后台守护线程，每 30s 扫描一次已启用的计划（cron 5 段标准表达式，支持 `*`/`*/n`/`a-b`/`a,b`）
- 到点后通过现有 TaskManager.submit 派发任务 → 业务逻辑不变（对齐 runner 预留设计）
- 配置持久化在 data/schedules.json
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime

from . import store
from .runner import manager

SAMPLE_CRON = "0 8 * * *"  # 示例：每天 08:00

# 动作名 → (显示名, 执行函数)
ACTIONS = {
    "collect": ("每日采集", None),        # 占位，执行时拼接方向
    "clean": ("全库整理", "clean_all"),
    "report": ("生成日报", "report_daily"),
}


def _now_fields() -> tuple:
    now = datetime.now()
    return now.minute, now.hour, now.day, now.month, now.isoweekday()


def _cron_match(field: str, value: int) -> bool:
    """匹配单个 cron 字段：`*`、`1,15`、`1-5`、`*/15`、`1-10/2`。"""
    field = field.strip()
    if field == "*":
        return True
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        base = part
        if "/" in part:
            base, _, step_s = part.partition("/")
            step = int(step_s)
        if base == "*":
            if value % step == 0:
                return True
            continue
        if "-" in base:
            lo, _, hi = base.partition("-")
            if int(lo) <= value <= int(hi) and (value - int(lo)) % step == 0:
                return True
        elif int(base) == value:
            return True
    return False


def match_expression(cron: str, minute, hour, day, month, wday) -> bool:
    """校验并匹配 5 段 cron。不符合时报 ValueError。
    周字段兼容标准 cron 语义：0 与 7 均表示周日（isoweekday 周日=7）。
    """
    parts = cron.split()
    if len(parts) != 5:
        raise ValueError("cron 需 5 段：分 时 日 月 周")
    return all([
        _cron_match(parts[0], minute),
        _cron_match(parts[1], hour),
        _cron_match(parts[2], day),
        _cron_match(parts[3], month),
        _cron_match(parts[4], wday) or (wday == 7 and _cron_match(parts[4], 0)),
    ])


def validate_cron(cron: str) -> bool:
    try:
        match_expression(cron, 0, 0, 1, 1, 1)
        return True
    except (ValueError, IndexError):
        return False


def _schedule_file():
    return store.config.DATA_ROOT / "schedules.json"


def load_schedules() -> list:
    return store.load_json(_schedule_file(), default=[])


def save_schedules(items: list) -> None:
    _schedule_file().parent.mkdir(parents=True, exist_ok=True)
    store.save_json(_schedule_file(), items)


def _next_id(items: list) -> str:
    taken = {i.get("id") for i in items}
    n = len(taken) + 1
    while f"s{n}" in taken:
        n += 1
    return f"s{n}"


def add_schedule(name: str, action: str, cron: str, enabled: bool = True,
                 direction: str = "") -> dict:
    if action not in ACTIONS:
        raise ValueError(f"未知动作：{action}")
    if action == "collect" and not direction.strip():
        raise ValueError("采集类定时任务需填写采集方向")
    if not validate_cron(cron):
        raise ValueError(f"cron 表达式无效：{cron}")
    items = load_schedules()
    item = {"id": _next_id(items), "name": name, "action": action,
            "cron": cron, "enabled": enabled, "direction": direction,
            "last_run": None, "last_status": None}
    items.append(item)
    save_schedules(items)
    return item


def update_schedule(sid: str, **fields) -> dict | None:
    items = load_schedules()
    for it in items:
        if it.get("id") != sid:
            continue
        if "cron" in fields and not validate_cron(fields["cron"]):
            raise ValueError(f"cron 表达式无效：{fields['cron']}")
        if "action" in fields and fields["action"] == "collect" and not (fields.get("direction") or it.get("direction", "")).strip():
            raise ValueError("采集类定时任务需填写采集方向")
        it.update({k: v for k, v in fields.items() if k in ("name", "action", "cron", "enabled", "direction")})
        save_schedules(items)
        return it
    return None


def delete_schedule(sid: str) -> bool:
    items = load_schedules()
    nxt = [i for i in items if i.get("id") != sid]
    if len(nxt) == len(items):
        return False
    save_schedules(nxt)
    return True


# ---------- 执行 ----------
def _run_action(action: str, direction: str = "") -> str | None:
    """把定时动作 map 到 runner.submit，返回 task_id。"""
    if action == "clean":
        from . import cleaner
        return manager.submit("定时·全库整理", cleaner.clean_all)
    if action == "report":
        from . import reporter
        return manager.submit("定时·生成日报", reporter.build_daily)
    if action == "collect":
        from . import ai_collector
        return manager.submit("定时·AI采集", ai_collector.collect_by_direction, direction)
    return None


def _tick() -> None:
    """扫描一次：跑到点的计划就触发（防止重入：同计划上次未结束则跳过；
    同一分钟只触发一次——30s tick 下 `* * * * *` 不再双发）。"""
    minute, hour, day, month, wday = _now_fields()
    mkey = time.strftime("%Y-%m-%d %H:%M")
    items = load_schedules()
    fired = False
    for it in items:
        if not it.get("enabled", True):
            continue
        if it.get("fired_minute") == mkey:
            continue  # 本分钟已触发过
        try:
            if not match_expression(it["cron"], minute, hour, day, month, wday):
                continue
        except Exception:  # noqa: BLE001
            continue
        # 触发
        sid = it["id"]
        it["fired_minute"] = mkey
        try:
            task_id = _run_action(it["action"], it.get("direction", ""))
        except Exception as e:  # noqa: BLE001
            it["last_status"] = f"error: {e}"
            it["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
            fired = True
            continue
        it["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
        it["last_status"] = "fired"
        it["task_id"] = task_id
        fired = True
    if fired:
        save_schedules(items)


class SchedulerThread(threading.Thread):
    def __init__(self, interval: float = 30.0) -> None:
        super().__init__(daemon=True, name="kb-scheduler")
        self._interval = interval
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                _tick()
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(self._interval)


_scheduler: SchedulerThread | None = None


def start() -> SchedulerThread:
    global _scheduler
    if _scheduler and _scheduler.is_alive():
        return _scheduler
    _scheduler = SchedulerThread()
    _scheduler.start()
    return _scheduler


def stop() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.stop()
        _scheduler = None