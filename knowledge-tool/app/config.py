"""全局配置：数据根目录、模板目录、报告目录等。

数据根目录默认指向应用同级的 data/，可通过环境变量 KNOWLEDGE_ROOT 覆盖，
便于 Docker 挂载与对接用户自建知识库文件夹。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 数据根目录（知识库）
DATA_ROOT = Path(os.environ.get("KNOWLEDGE_ROOT", BASE_DIR / "data"))

# 数据根下的子目录
NOTES_DIR = DATA_ROOT / "notes"          # 笔记库（按来源/实体/概念分目录）
TEMPLATES_DIR = DATA_ROOT / "templates"  # 笔记模板
REPORTS_DIR = DATA_ROOT / "reports"      # 生成的每日报告
INDEX_FILE = DATA_ROOT / "index.json"    # 索引器产物（标签/主题/关键词索引）
TAGS_FILE = DATA_ROOT / "tags.json"      # 标签表
TOPICS_FILE = DATA_ROOT / "topics.json"  # 主题表

# 示例知识库（受版本控制，可一键导入供新手开箱即用）
SEED_DIR = BASE_DIR / "seed"

# 笔记元数据字段（meta.json schema）
META_FIELDS = [
    "id", "title", "source_url", "created_at", "updated_at",
    "topic", "tags", "keywords", "summary", "status", "fingerprint",
]

# 前端静态目录（原生 HTML 管理台）
FRONTEND_DIR = BASE_DIR / "frontend"

# 后台任务并发上限
MAX_TASKS = 8
