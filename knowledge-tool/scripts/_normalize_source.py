"""source 正文模板统一（幂等）：补齐「## 摘要」区块（数据来自 meta.summary）。

不做要点提取（避免与正文列表重复）；「关键要点/个人思考/双链」由采集/增强生产线自带。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import store, models

changed = 0
for m in store.list_notes(note_type="source"):
    note = store.load_note(m.id)
    body = (note.body or "").strip()
    summary = (note.meta.summary or "").strip()
    if not body or not summary or "## 摘要" in body:
        print(f"[ok  ] {m.title}")
        continue
    # 在首个 ## 区块前插入摘要（标题 # 之后）
    if body.startswith("# "):
        nl = body.find("\n")
        head, rest = body[:nl + 1], body[nl + 1:]
    else:
        head, rest = "", body
    note.body = head.rstrip() + f"\n\n## 摘要\n{summary}\n\n" + rest.lstrip() + "\n"
    note.meta.updated_at = models.now_iso()
    store._write_note(note)
    changed += 1
    print(f"[fill] {m.title}  摘要({len(summary)}字)")
print(f"\n补齐 {changed} 篇")