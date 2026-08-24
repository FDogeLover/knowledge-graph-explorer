"""提示词模板体系：AI 采集的多套可选提示词。

- 内置模板：app/prompts/*.md（随包分发，builtin=true）
- 自定义模板：data/prompts/*.md（用户在设置页新增/编辑，builtin=false）
模板文件格式：顶部 frontmatter（name/label/builtin），正文即系统提示词。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config

BUILTIN_DIR = Path(__file__).resolve().parent / "prompts"


def _parse_template(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return None
    # 兼容：`---\n...\n---\n正文` 或 `name:..\n...\n---\n正文` 两种 frontmatter
    m = re.match(r"^(?:---\n)?(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return None
    meta = {}
    for line in m.group(1).split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    if "name" not in meta:
        return None
    return {
        "name": meta.get("name", path.stem),
        "label": meta.get("label", path.stem),
        "builtin": str(meta.get("builtin", "")).lower() == "true",
        "prompt": m.group(2).strip(),
    }


def _user_dir() -> Path:
    return config.DATA_ROOT / "prompts"


def list_templates() -> list:
    out = []
    for p in sorted(BUILTIN_DIR.glob("*.md")):
        t = _parse_template(p)
        if t:
            out.append(t)
    ud = _user_dir()
    if ud.exists():
        for p in sorted(ud.glob("*.md")):
            t = _parse_template(p)
            if t:
                out.append(t)
    return out


def get_template(name: str) -> dict | None:
    if not name:
        return None
    for t in list_templates():
        if t["name"] == name:
            return t
    return None


def save_custom_template(name: str, label: str, prompt: str) -> dict:
    """保存/更新自定义模板（data/prompts/{name}.md）。name 需规范化。"""
    import re as _re
    safe = _re.sub(r"[^\w\u4e00-\u9fff-]+", "-", name).strip("-") or "custom"
    ud = _user_dir()
    ud.mkdir(parents=True, exist_ok=True)
    p = ud / f"{safe}.md"
    text = f"---\nname: {safe}\nlabel: {label or safe}\nbuiltin: false\n---\n{prompt.strip()}\n"
    p.write_text(text, encoding="utf-8")
    return {"name": safe, "label": label or safe, "builtin": False, "prompt": prompt.strip()}


def delete_custom_template(name: str) -> bool:
    ud = _user_dir()
    p = ud / f"{name}.md"
    if p.exists():
        p.unlink()
        return True
    return False