"""节点价值评估 / 无价值节点软删 / 低密度骨架页 LLM 补全。

用法（在 knowledge-tool 根目录执行）：
    python scripts/assess_notes.py               # 只评估并打印价值分层
    python scripts/assess_notes.py --delete      # 删除无价值占位节点(软删到 .trash)并清理悬空引用
    python scripts/assess_notes.py --enrich      # 对低密度(定义≤20字)骨架页跑 LLM 一句话补全
    python scripts/assess_notes.py --delete --enrich   # 先删后补全

价值判定（针对 entity/concept 骨架页）：
- 无价值（删除）："定义"为空或 ≤4 字，或为纯排名占位（日增第N/第N名 等），无任何实质信息。
- 偏薄（补全）："定义"≤20 字 且包含实质描述（或仅有 1 处定义），有价值但信息不足。
- 正常：定义 >20 字，或含 ## 概览 / ## 事件时间线 等富内容。
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import cleaner, indexer, llm, store  # noqa: E402
from app.models import now_iso  # noqa: E402

# 纯排名占位定义（无信息，值得删除）：如"日增第3"、"第1名"、"排名2"等
PLACEHOLDER_RE = re.compile(
    r"^(日增|新增|上升|涨|跌|排名|rank|No\.?)['\'\"\s]*第?\s*\d+.*$",
    re.I,
)
LEN_TRIM = 20  # 定义长度阈值：偏薄


def extract_def(body: str) -> str:
    m = re.search(r"##\s*定义\s*\n(.*?)(?=\n##\s|\Z)", body, re.S)
    if not m:
        return ""
    return re.sub(r"^[-*]\s*", "", m.group(1)).strip()


def has_rich(body: str) -> bool:
    return ("## 概览" in body) or ("## 事件时间线" in body) or len(body or "") > 400


def referencing_counts() -> dict:
    """name -> 引用它的 source 数量（按结构化双链名称匹配）。"""
    refs = {}
    for m in store.list_notes(note_type="source"):
        note = store.load_note(m.id)
        if not note:
            continue
        try:
            links = cleaner.link_entities_concepts(note)
        except Exception:  # noqa: BLE001
            links = {"entities": [], "concepts": []}
        for it in links.get("entities", []) + links.get("concepts", []):
            name = (it.get("name") or "").strip()
            if name:
                refs[name] = refs.get(name, 0) + 1
    return refs


def assess_all() -> tuple[list, list, list]:
    """返回 (worthless, thin, ok)。"""
    refs = referencing_counts()
    worthless, thin, ok = [], [], []
    for kind in ("entity", "concept"):
        base = store.config.NOTES_DIR / kind
        if not base.exists():
            continue
        for folder in base.glob("*/"):
            meta_p = folder / "meta.json"
            if not meta_p.exists():
                continue
            meta = store.load_meta(folder.name)
            if not meta:
                continue
            note = store.load_note(meta.id)
            def_text = extract_def(note.body) if note else ""
            rich = has_rich(note.body or "")
            ref_count = refs.get(meta.title or meta.id, 0)
            # 无价值 = 空定义 或 纯排名占位（如"日增第N"）；短但有语义的不在此列
            if (not def_text) or PLACEHOLDER_RE.match(def_text):
                if not rich:
                    worthless.append({
                        "id": meta.id, "kind": kind, "title": meta.title,
                        "def": def_text, "refs": ref_count, "path": str(folder),
                    })
                    continue
            if len(def_text) <= LEN_TRIM and not rich:
                thin.append({
                    "id": meta.id, "kind": kind, "title": meta.title,
                    "def": def_text, "refs": ref_count, "path": str(folder),
                })
            else:
                ok.append({"id": meta.id, "kind": kind, "title": meta.title,
                           "refs": ref_count, "def_len": len(def_text)})
    return worthless, thin, ok


def soft_delete(note_id: str, kind: str) -> str:
    """软删除：移入 .trash/YYYYMMDD/{kind}/（同名加后缀）。返回目标路径。"""
    dst = store.config.DATA_ROOT / ".trash" / time.strftime("%Y%m%d") / kind
    dst.mkdir(parents=True, exist_ok=True)
    target = dst / note_id
    if target.exists():
        target = dst / f"{note_id}-{int(time.time())}"
    src = store.config.NOTES_DIR / kind / note_id
    if src.exists():
        shutil.move(str(src), str(target))
    return str(target)


def clean_source_refs(title: str) -> int:
    """从所有 source 的 body「## 相关实体/概念」区块与 meta.related_* 中移除该标题的悬空引用。"""
    removed = 0
    for m in store.list_notes(note_type="source"):
        note = store.load_note(m.id)
        if not note:
            continue
        changed = False
        body = note.body or ""
        # 删除 `- [[Title]] [- 说明]` 整行 / `| [[Title]] | ... |` 行
        pat = re.compile(r"^[^\n]*\[\[" + re.escape(title) + r"\]\][^\n]*$", re.M)
        new_body = pat.sub("", body)
        if new_body.strip() != body.strip():
            note.body = new_body.strip("\n") + "\n"
            changed = True
        # 净化：压缩连续空行；若「相关实体/概念」区块已无任何双链 → 删除整个区块头
        note.body = re.sub(r"\n{2,}", "\n\n", note.body.rstrip() + "\n")
        for sec in ("相关实体", "相关概念"):
            m = re.search(r"##\s*" + sec + r"\s*\n+(?=##\s|\Z)", note.body)
            if m:
                note.body = note.body.replace(m.group(0), "")

        def drop(lst):
            out = []
            for x in (lst or []):
                nm = x.get("name", x) if isinstance(x, dict) else str(x)
                if nm != title:
                    out.append(x)
            return out

        new_e = drop(note.meta.related_entities)
        new_c = drop(note.meta.related_concepts)
        if new_e != note.meta.related_entities:
            note.meta.related_entities = new_e
            changed = True
        if new_c != note.meta.related_concepts:
            note.meta.related_concepts = new_c
            changed = True
        if changed:
            store._write_note(note)
            removed += 1
    return removed


_FILL_PROMPT = """你是知识库骨架页补全助手。为「{name}」（{kind}）基于以下引用来源的信息，生成一句准确的简介（一句话，≤45 字，中文）。
要求：
- 只陈述来源信息中已知的事实，不臆造；信息不足以描述细节时，写「类型 + 定位」即可（如「开源AI项目；GitHub Trending 排行榜收录的仓库」）；
- 输出 JSON（严格，不要 markdown 代码块）：{{"definition": "一句话简介"}}。

引用来源信息：
{items}"""


def enrich_thin(thin: list) -> dict:
    """对偏薄骨架页用 LLM 补全「定义」一句话。"""
    if not thin:
        return {"enriched": 0, "skipped": 0, "failed": 0, "results": []}
    if not llm.is_configured():
        raise RuntimeError("未配置 LLM API Key（设置页或环境变量 LLM_API_KEY），无法补全")

    results, enriched, failed = [], 0, 0
    for item in thin:
        try:
            refs = []
            for m in store.list_notes(note_type="source"):
                note = store.load_note(m.id)
                if not note:
                    continue
                try:
                    links = cleaner.link_entities_concepts(note)
                except Exception:  # noqa: BLE001
                    continue
                names = [it["name"] for it in links.get("entities", [])] if item["kind"] == "entity" \
                    else [it["name"] for it in links.get("concepts", [])]
                if item["title"] not in names:
                    continue
                s = (note.meta.summary or "").strip() or note.body.strip()[:120] or "(无摘要)"
                refs.append(f"- 《{note.meta.title}》：{s}")
            items = "\n".join(refs[:8]) or "(无可用来源——请基于名称针对性给出保守的一定位简介)"
            kind_cn = "实体" if item["kind"] == "entity" else "概念"
            data = llm.chat_json(
                _FILL_PROMPT.format(name=item["title"], kind=kind_cn, items=items),
                "请输出 JSON。", temperature=0.3)
            definition = str(data.get("definition") or "").strip()
            if not definition:
                raise RuntimeError("LLM 未返回 definition")
            note = store.load_note(item["id"])
            body = note.body or f"# {item['title']}"
            # 替换/插入 ## 定义
            new_body = re.sub(
                r"##\s*定义\s*\n.*?(?=\n##\s|\Z)", f"## 定义\n{definition}\n",
                body, count=1, flags=re.S)
            if new_body == body:
                new_body = new_body.rstrip() + f"\n\n## 定义\n{definition}\n"
            note.body = new_body
            note.meta.summary = (definition[:120])
            note.meta.updated_at = now_iso()
            store._write_note(note)
            enriched += 1
            results.append({"id": item["id"], "title": item["title"], "def": definition})
        except Exception as e:  # noqa: BLE001
            failed += 1
            results.append({"id": item["id"], "title": item["title"], "error": str(e)[:120]})
    return {"enriched": enriched, "skipped": 0, "failed": failed, "results": results}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete", action="store_true", help="删除无价值占位节点（软删）")
    ap.add_argument("--enrich", action="store_true", help="对偏薄骨架页跑 LLM 补全")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    worthless, thin, ok = assess_all()
    summary = {
        "total": len(worthless) + len(thin) + len(ok),
        "worthless": len(worthless), "thin": len(thin), "ok": len(ok),
        "worthless_list": worthless, "thin_list": [{"id": x["id"], "kind": x["kind"],
                                                    "title": x["title"], "def": x["def"]} for x in thin],
    }

    if args.delete and worthless:
        removed = []
        for item in worthless:
            target = soft_delete(item["id"], item["kind"])
            n = clean_source_refs(item["title"])
            removed.append({**{k: item[k] for k in ("id", "kind", "title", "def")},
                            "trash": target, "refs_cleaned": n})
        summary["deleted"] = removed

    if args.enrich:
        summary["enrich"] = enrich_thin(thin)

    if store.ensure_initialized():
        try:
            summary["index"] = indexer.rebuild()
        except Exception as e:  # noqa: BLE001
            summary["index"] = {"error": str(e)[:100]}

    if args.json:
        import json
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"====== 节点价值评估 ======")
        print(f"总计 {summary['total']} | 无价值 {len(worthless)} | 偏薄 {len(thin)} | 正常 {len(ok)}")
        if worthless:
            print("\n-- 无价值（待删/已删） --")
            for w in worthless:
                print(f"  [{w['kind']}] {w['title']}  定义=`{w['def']}`  refs={w['refs']}")
        if thin:
            print("\n-- 偏薄（可补全） --")
            for t in thin:
                print(f"  [{t['kind']}] {t['title']}  定义=`{t['def']}`  refs={t['refs']}")
        if "deleted" in summary:
            print(f"\n已软删 {len(summary['deleted'])} 个无价值节点 → {store.config.DATA_ROOT}/.trash")
        if "enrich" in summary:
            e = summary["enrich"]
            print(f"补全 {e['enriched']} 成功 / {e['failed']} 失败")


if __name__ == "__main__":
    main()