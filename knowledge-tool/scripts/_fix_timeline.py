"""修复实体/概念页「## 事件时间线」的批量日污染：
采集器把批量采集日（表格中同一天出现 >5 次）错填到各引用记录的日期列，
事件内容正确。本脚本：
1. 把批量日行按行内 [[来源]] 笔记的 date_published / created_at 改写真实日期；
2. 整表按日期升序重排（同日保持原序）；
3. 去除完全重复行（同日期同描述）。
幂等：修正后批量日消失，再次运行不改。
"""
import re
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import models, store  # noqa: E402

ROW_RE = re.compile(r"^\|?\s*(\d{4}-\d{2}-\d{2})\s*\|")
LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]|]*)?\]\]")
BULK_TH = 5  # 同日行数超过此值才视为"批量采集日"


def resolve_date(rest_line: str, bulk_days=()) -> str | None:
    """优先从行内 [[...]] 链接的 slug 提取真实日期（采集批量日错填时 slug 多数自带发布日期）；
    其次回退来源笔记 meta（跳过与批量日相同的值）。"""
    for L in LINK_RE.findall(rest_line):
        mm = re.search(r"(20\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]))", L)
        if mm:
            return mm.group(1)
    for L in LINK_RE.findall(rest_line):
        meta = store.load_meta(L.strip())
        if not meta:
            continue
        d = (meta.date_published or "").strip() or (meta.created_at or "").strip()
        mm = re.match(r"(20\d{2}-\d{2}-\d{2})", d)
        if mm and mm.group(1) not in bulk_days:
            return mm.group(1)
    return None


def fix_timeline(note_id: str) -> dict:
    note = store.load_note(note_id)
    if not note or "## 事件时间线" not in (note.body or ""):
        return {"id": note_id, "skipped": "no timeline"}
    seg = note.body.split("## 事件时间线", 1)[1]
    m = re.search(r"\n##\s", seg)
    block = seg[: m.start()] if m else seg
    tail = seg[m.start():] if m else ""
    heads, rows = [], []
    for ln in block.split("\n"):
        if ROW_RE.match(ln):
            rows.append(ln)
        else:
            heads.append(ln)
    cnt = Counter(ROW_RE.match(ln).group(1) for ln in rows)
    bulk = {d for d, c in cnt.items() if c > BULK_TH}
    fixed, applied = 0, 0
    dated = []
    for ln in rows:
        mm = ROW_RE.match(ln)
        d, rest = mm.group(1), ln[mm.end() :]
        if d in bulk:
            sd = resolve_date(rest, bulk)
            if sd:
                # 只替换行内日期组（保留前导 | 与列格式）
                ln = ln[: mm.start(1)] + sd + ln[mm.end(1):]
                d = sd
                fixed += 1
        if d in cnt and d in bulk:
            applied += 1
        dated.append((d, ln))
    dated.sort(key=lambda x: x[0])
    uniq, seen = [], set()
    for d, ln in dated:
        if ln in seen:
            continue
        seen.add(ln)
        uniq.append(ln)
    new_block = "\n".join(heads).rstrip() + "\n" + "\n".join(uniq) + "\n"
    note.body = note.body.replace(block, new_block)
    note.meta.updated_at = models.now_iso()
    store._write_note(note)
    # 修正后统计
    dates = [ROW_RE.match(x).group(1) for x in uniq if ROW_RE.match(x)]
    c2 = Counter(dates)
    return {
        "id": note_id, "rows": len(rows), "bulk_days": sorted(bulk),
        "rows_fixed_to_src": fixed, "dup_removed": len(dated) - len(uniq),
        "remaining_gt5_days": {k: v for k, v in c2.items() if v > BULK_TH},
        "first": dates[0] if dates else "-", "last": dates[-1] if dates else "-",
    }


if __name__ == "__main__":
    import json
    out = []
    for t in ("entity", "concept"):
        for m in store.list_notes(note_type=t):
            r = fix_timeline(m.id)
            if r.get("skipped"):
                continue
            print(json.dumps(r, ensure_ascii=False))
            out.append(r)
    if out:
        from app import indexer
        try:
            indexer.rebuild()
        except Exception:  # noqa: BLE001
            pass