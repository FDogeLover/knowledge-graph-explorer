"""端到端冒烟测试：初始化 → 采集 → 整理 → 报告 → 图谱。

用法：uvicorn 起来后 python test_e2e.py
"""
import json
import time

import httpx

BASE = "http://127.0.0.1:8001"
c = httpx.Client(base_url=BASE, timeout=20)


def wait_task(tid, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = c.get(f"/api/tasks/{tid}").json()
        if t["status"] in ("done", "error"):
            return t
        time.sleep(0.3)
    return {"status": "timeout"}


def main():
    # 1. 初始化
    r = c.post("/api/setup", json={"topics": ["技术", "人文", "商业"], "templates": True})
    assert r.status_code == 200, r.text
    print("[setup]", r.json())

    # 2. 采集（粘贴正文 x3，含双链区块）
    samples = [
        {"text": "人工智能正在重塑编程行业，大模型让算法开发更快，工程师在学习新框架。\n\n## 相关实体\n- [[OpenAI]] - 代表性大模型公司\n- [[Vercel]] - AI 前端框架主力\n\n## 相关概念\n- [[大模型]] - 驱动本轮 AI 应用的核心\n- [[AI编程]] - 编码辅助成为刚需\n",
         "title": "AI 重塑编程", "topic": "技术", "tags": ["AI", "编程"], "source_type": "网页"},
        {"text": "历史与哲学的交汇，人类文明由无数故事构成，社会制度建立于集体想象之上。\n\n## 相关实体\n- [[赫拉利]] - 历史学者\n\n## 相关概念\n- [[集体想象]] - 制度维持的关键\n",
         "title": "人类文明的故事", "topic": "人文", "tags": ["历史", "哲学"], "source_type": "网页"},
        {"text": "商业模式创新与市场增长，产品设计与用户体验决定一款应用能否走红。\n\n## 相关实体\n- [[字节跳动]] - 产品驱动增长典范\n\n## 相关概念\n- [[用户增长]] - 增长核心方法论\n",
         "title": "增长的产品方法论", "topic": "商业", "tags": ["产品", "增长"], "source_type": "网页"},
    ]
    for s in samples:
        r = c.post("/api/collect", json=s)
        assert r.status_code == 200, r.text
        t = wait_task(r.json()["task_id"])
        print("[collect]", t["status"], t.get("result"))

    # 3. 全库整理（应自动建 entity/concept 骨架页 + 双链）
    r = c.post("/api/notes/clean-all")
    t = wait_task(r.json()["task_id"])
    print("[clean-all]", t["status"], "skeletons=", t.get("result", {}).get("skeletons_created"))

    # 4. 报告
    r = c.get("/api/reports/daily/latest")
    assert r.status_code == 200
    print("[report] total=", r.json()["summary"])

    # 5. 图谱数据
    r = c.get("/api/graph/data")
    d = r.json()
    print("[graph] nodes=", len(d["nodes"]), "edges=", len(d["edges"]),
          "labels=", [n["label"] for n in d["nodes"]])

    # 6. 搜索
    r = c.get("/api/search", params={"q": "AI"})
    print("[search] hits=", len(r.json()["results"]))

    # 7. 笔记详情
    nid = c.get("/api/notes").json()["notes"][0]["id"]
    r = c.get(f"/api/notes/{nid}")
    print("[detail]", nid, "keywords=", r.json().get("keywords"))

    print("\nALL OK")


if __name__ == "__main__":
    main()
