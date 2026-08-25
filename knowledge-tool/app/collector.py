"""采集器：抓取网页 / 粘贴正文 → 生成结构化笔记候选。

一期支持两种采集方式：
- 抓取链接：httpx 请求页面，BeautifulSoup 抽取标题与正文（通用读模式，不针对站点写死）
- 粘贴正文：直接粘贴纯文本/HTML 入库
"""
from __future__ import annotations

import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from . import store
from .models import Note, NoteMeta, slugify


def collect_url(url: str, topic: str = "", tags: Optional[list] = None,
                source_type: str = "网页") -> dict:
    """抓取链接并入库。配置了 LLM 时自动提炼为结构化笔记（原文本保留）。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    }
    resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    title = _pick_title(soup, url)
    body_text = _extract_readable(soup)

    # GitHub 仓库页特判：抓官方描述/星数/作者，入库到 source_meta，
    # 并注入正文本体，供 LLM 提炼实体会话使用（杜绝"日增第N"式占位定义）
    repo_meta = _github_repo_meta(url, soup)
    if repo_meta:
        desc = repo_meta.get("description") or ""
        if desc:
            body_text = f"{desc}\n\n{body_text}" if body_text else desc

    note = _build_note(
        title=title,
        body=body_text,
        source_url=url,
        topic=topic,
        tags=tags or [],
        source_type=source_type,
    )
    if repo_meta:
        note.meta.source_type = "GitHub"
        note.meta.source_meta = repo_meta
    refined = _ai_refine(note)   # 自动启用，未配 LLM 静默回退
    result = store.save_note(refined or note)
    if refined is not None:
        # 提炼出炉（含双链表）：写盘后立即整理并建骨架页，入库即入图谱
        try:
            from . import cleaner
            cleaner.clean_note(note.meta.id)
            result["skeletons"] = cleaner.ensure_links(note.meta.id)
        except Exception:  # noqa: BLE001
            pass
    return result


def collect_text(text: str, title: str = "", topic: str = "", tags: Optional[list] = None,
                 source_type: str = "网页") -> dict:
    """粘贴正文/HTML 入库。配置了 LLM 时自动提炼为结构化笔记（原文本保留）。"""
    if "<" in text[:200] and ">" in text[:200]:
        soup = BeautifulSoup(text, "html.parser")
        title = title or _pick_title(soup, "") or text.strip().splitlines()[0][:40]
        body_text = soup.get_text("\n", strip=True)
    else:
        title = title or text.strip().splitlines()[0][:40]
        body_text = text.strip()

    note = _build_note(title=title, body=body_text, source_url=None, topic=topic,
                       tags=tags or [], source_type=source_type)
    refined = _ai_refine(note)
    result = store.save_note(refined or note)
    if refined is not None:
        try:
            from . import cleaner
            cleaner.clean_note(note.meta.id)
            result["skeletons"] = cleaner.ensure_links(note.meta.id)
        except Exception:  # noqa: BLE001
            pass
    return result


def _build_note(title, body, source_url, topic, tags, source_type="网页") -> Note:
    meta = NoteMeta(
        id=slugify(title),
        title=title,
        source_url=source_url,
        topic=topic or "默认主题",
        tags=tags,
        type="source",
        source_type=source_type,
        raw_text=body,
    )
    meta.fingerprint = store.make_body_fingerprint(body)
    return Note(meta=meta, body=body)


def _ai_refine(note: Note) -> Note | None:
    """把已抓取/粘贴的原始笔记交给 LLM 提炼成结构化版本。

    - 配置了 LLM：重写 body 为「精炼稿 + 双链区块」，预填 summary/tags/实体概念，原文保留在 raw_text。
    - 未配置 / 提炼失败：返回 None（调用方沿用原始 note）。
    """
    if not note.body or len(note.body) < 30:
        return None
    # 已含双链区块（AI 方向采集/人工规范文本）：跳过再次提炼
    if "## 相关实体" in note.body or "## 相关概念" in note.body:
        return None
    try:
        from . import llm
        if not llm.is_configured():
            return None
        data = llm.chat_json(
            _REFINE_SYSTEM,
            f"原文标题：{note.meta.title}\n原文全文（{len(note.body)} 字）：\n{note.body[:4000]}",
            temperature=0.2,
        )
    except Exception:  # noqa: BLE001
        return None  # AI 提炼失败/未配置：静默回退，不阻塞采集

    title = str(data.get("title") or note.meta.title).strip()[:60]
    summary = str(data.get("summary") or "").strip()
    topic = str(data.get("topic") or note.meta.topic or "综合").strip()
    tags = [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()]
    entities = data.get("related_entities") or []
    concepts = data.get("related_concepts") or []

    # 精炼稿 = 摘要 + 要点 + 双链区块（与人工/AI 方向采集同构，cleaner 可直接建骨架）
    parts = [summary] if summary else []
    parts.extend(str(p).strip() for p in (data.get("bullet_points") or []) if str(p).strip())
    if entities:
        parts.append("## 相关实体")
        for e in entities[:6]:
            parts.append(f"- [[{e.get('name', '').strip()}]] - {e.get('desc', '').strip()}")
    if concepts:
        parts.append("## 相关概念")
        for c in concepts[:6]:
            parts.append(f"- [[{c.get('name', '').strip()}]] - {c.get('desc', '').strip()}")
    new_body = "\n".join(parts) if parts else note.body

    # 更新元数据：保留原始标题（避免 id 变化破坏去重），补充分类字段
    note.meta.title = title
    note.meta.topic = topic or note.meta.topic or "综合"
    note.meta.summary = summary
    if tags:
        note.meta.tags = sorted(set(note.meta.tags) | set(tags))
    note.meta.source_type = note.meta.source_type or "AI 提炼"
    note.meta.raw_text = note.body  # 原文保留
    note.meta.fingerprint = store.make_body_fingerprint(note.body)  # 按原始文本去重
    note.body = new_body
    return note


_REFINE_SYSTEM = """你是一个知识库精炼助手。用户会给你一篇采集到的原文（可能是网页正文或粘贴文本）。
你的任务：把原文提炼成结构化的知识来源笔记，便于存入知识库和融入知识图谱。

要求：
- 忠实原文，不臆造原文没有的事实；原文太碎时归纳成连贯的要点；
- 提取 3～6 条要点（每条 10～30 字）；
- 识别原文涉及的**实体**（公司/组织/产品/人物等）与**概念**（抽象名词/趋势/领域），各 1～3 个，每个给一句 10～20 字描述；
- 摘要 ≤50 字。

输出 JSON（严格如下结构）：
{
  "title": "精炼标题（≤30 字，可沿用原标题）",
  "summary": "一句话摘要（≤50 字）",
  "topic": "主题（科技/商业/人文/生活，不确定用 综合）",
  "tags": ["标签1", "标签2"],
  "bullet_points": ["要点1", "要点2", "要点3"],
  "related_entities": [{"name": "实体名", "desc": "描述"}],
  "related_concepts": [{"name": "概念名", "desc": "描述"}]
}"""


def _github_repo_meta(url: str, soup) -> dict:
    """GitHub 公开仓库页特判：抓官方描述/星数/作者（作为实体定义兜底与 source_meta）。

    仅处理 `github.com/{owner}/{repo}` 命名的仓库页；trending/topics 等列表页与
    `.git`/锚点变体一律忽略，避免把榜单页误当仓库。
    """
    m = re.match(r"^https?://github\.com/([^/?#]+)/([^/?#]+)/?", url)
    if not m:
        return {}
    owner, repo = m.group(1), m.group(2)
    if owner in ("trending", "topics", "collections", "orgs", "search", "login", "features"):
        return {}
    if repo.endswith(".git") or repo.startswith("#") or ".md" in repo.lower():
        return {}
    desc = ""
    og = soup.find("meta", property="og:description")
    if og and og.get("content"):
        desc = og["content"].strip()
    stars = ""
    sm = re.search(r"(\d[\d.,]*[kKmM]?)\s*(stars?)", soup.get_text(" ", strip=True)[:6000])
    if sm:
        stars = sm.group(1)
    return {"owner": owner, "repo": repo, "description": desc, "stars": stars,
            "official_url": f"https://github.com/{owner}/{repo}"}


def _pick_title(soup: BeautifulSoup, fallback: str) -> str:
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()[:120]
    t = soup.find("title")
    if t and t.get_text(strip=True):
        return t.get_text(strip=True)[:120]
    return fallback or "未命名笔记"


def _extract_readable(soup: BeautifulSoup) -> str:
    """通用正文抽取：移除导航/脚本/广告等噪声，取文本密度高的容器。"""
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript", "form", "iframe"]):
        tag.decompose()

    candidates = []
    for node in soup.find_all(["article", "main", "div", "section"]):
        text = node.get_text("\n", strip=True)
        if len(text) < 80:
            continue
        # 文本密度：字符数 / (标签数+1)
        tags = len(node.find_all())
        density = len(text) / (tags + 1)
        candidates.append((len(text), density, node))

    if not candidates:
        return soup.get_text("\n", strip=True)

    # 优先取文本最长、密度最高的容器
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best = candidates[0][2]
    paras = [p.get_text(" ", strip=True) for p in best.find_all("p")]
    paras = [p for p in paras if len(p) > 1]
    if paras:
        return "\n\n".join(paras)
    return best.get_text("\n", strip=True)
