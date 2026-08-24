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
    """抓取链接并入库。返回 store.save_note 的结果 + 元数据。"""
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

    note = _build_note(
        title=title,
        body=body_text,
        source_url=url,
        topic=topic,
        tags=tags or [],
        source_type=source_type,
    )
    return store.save_note(note)


def collect_text(text: str, title: str = "", topic: str = "", tags: Optional[list] = None,
                 source_type: str = "网页") -> dict:
    """粘贴正文/HTML 入库。"""
    if "<" in text[:200] and ">" in text[:200]:
        soup = BeautifulSoup(text, "html.parser")
        title = title or _pick_title(soup, "") or text.strip().splitlines()[0][:40]
        body_text = soup.get_text("\n", strip=True)
    else:
        title = title or text.strip().splitlines()[0][:40]
        body_text = text.strip()

    note = _build_note(title=title, body=body_text, source_url=None, topic=topic,
                       tags=tags or [], source_type=source_type)
    return store.save_note(note)


def _build_note(title, body, source_url, topic, tags, source_type="网页") -> Note:
    meta = NoteMeta(
        id=slugify(title),
        title=title,
        source_url=source_url,
        topic=topic or "默认主题",
        tags=tags,
        type="source",
        source_type=source_type,
    )
    meta.fingerprint = store.make_body_fingerprint(body)
    return Note(meta=meta, body=body)


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
