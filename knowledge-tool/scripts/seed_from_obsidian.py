# -*- coding: utf-8 -*-
"""从 GitHub FDogeLover/Obsidian 拉取选定的真实 wiki 内容，转换为正式版 seed 格式。
obsidian frontmatter/markdown -> seed/notes/{type}/{slug}/meta.json + body.md
"""
import base64
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "seed" / "notes"

FILES = [
    # (repo_path, type)
    ("wiki/sources/量子位-2026-06-08-OpenAI芯片工程师跳槽Anthropic.md", "source"),
    ("wiki/sources/智谱官方-2026-06-14-GLM-5.2开源.md", "source"),
    ("wiki/sources/GitHub-Trending-2026-06-09.md", "source"),
    ("wiki/entities/OpenAI.md", "entity"),
    ("wiki/entities/Anthropic.md", "entity"),
    ("wiki/entities/AI Agent.md", "entity"),
    ("wiki/concepts/AI芯片人才争夺战.md", "concept"),
    ("wiki/concepts/AI全栈能力竞争.md", "concept"),
]

REPO = "FDogeLover/Obsidian"


def fetch(path: str) -> str:
    enc = path.replace("//", "/")
    url = f"repos/{REPO}/contents/{enc}"
    out = subprocess.run(["gh", "api", url, "--jq", ".content"],
                         capture_output=True, text=True, encoding="utf-8")
    b64 = out.stdout.strip()
    return base64.b64decode(b64).decode("utf-8")


def parse_frontmatter(content: str) -> (dict, str):
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", content, re.DOTALL)
    fm, body = {}, content
    if m:
        for line in m.group(1).split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip('"')
        body = m.group(2).strip()
    return fm, body


def slugify(text: str, max_len=40) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", text).strip("-")
    return (s or "note")[:max_len]


def parse_links(body: str, section: str) -> list:
    out = []
    m = re.search(rf"##\s*{section}\n(.*?)(?=\n##\s|\Z)", body, re.DOTALL)
    if not m:
        return out
    for line in m.group(1).split("\n"):
        line = line.strip()
        if not line or line.startswith("|"):
            continue
        m2 = re.match(r"-\s*\[\[([^\]]+)\]\]\s*[-—]\s*(.+)", line)
        if m2:
            out.append({"name": m2.group(1).strip(), "desc": m2.group(2).strip()})
    return out


def main():
    for repo_path, ntype in FILES:
        content = fetch(repo_path)
        fm, body = parse_frontmatter(content)

        title = fm.get("title", Path(repo_path).stem)
        note_id = slugify(fm.get("name") or title)
        topic = "示例·AI科技"

        rel_e = [{"name": e["name"], "desc": e["desc"], "type": "entity"} for e in parse_links(body, "相关实体")]
        rel_c = [{"name": c["name"], "desc": c["desc"], "type": "concept"} for c in parse_links(body, "相关概念")]

        # 摘要：source 取 `## 摘要` 首段；entity/concept 取 `## 定义` 首段
        summary = ""
        for sec in ("摘要", "定义"):
            sm = re.search(rf"##\s*{sec}\n(.*?)(?=\n##\s|\Z)", body, re.DOTALL)
            if sm:
                summary = sm.group(1).strip().split("\n")[0][:200]
                break

        meta = {
            "id": note_id,
            "title": title,
            "source_url": fm.get("url") or None,
            "created_at": fm.get("date_accessed", "2026-06-09 00:00:00"),
            "updated_at": fm.get("last_updated", fm.get("date_accessed", "2026-06-09 00:00:00")),
            "topic": topic,
            "tags": [t.strip() for t in (fm.get("tags", "") or "").strip("[]").split(",") if t.strip()] or [],
            "keywords": [],
            "summary": summary,
            "status": "clean",
            "fingerprint": hashlib.sha1(re.sub(r"\s+", " ", body).strip().lower().encode("utf-8")).hexdigest(),
            "type": ntype,
            "source_type": fm.get("source_type", ""),
            "date_published": fm.get("date_published", ""),
            "related_entities": rel_e if ntype == "source" else [],
            "related_concepts": rel_c if ntype == "source" else [],
        }

        folder = SEED / ntype / note_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / "body.md").write_text(body, encoding="utf-8")
        print(f"[{ntype}] {note_id}  ({title[:30]})  relE={len(rel_e)} relC={len(rel_c)}")


if __name__ == "__main__":
    main()
    print("SEED_REGEN_DONE")