# 知识库工作流工具（正式完整版）

把「看网页 → 手动整理进笔记 → 生成报告」沉淀成一套本地 Web 管理台：点一下采集、自动整理入库、一键生成每日报告与图表。一期手动可控，二期预留自动调度与 AI 增强。

> 本项目是「知识图谱探索器」的**正式完整版**——知识图谱探索器作为其前端「知识结构」展示视图被整合进来（`/graph-page`）。
> 与预览版**共享同一 GitHub 仓库**，本目录即正式版源码：`knowledge-tool/`。

## 技术栈
- 后端：Python + FastAPI
- 前端：原生 HTML/CSS/JS（零框架）
- 数据：文件数据层（`meta.json + body.md` 双文件，无重型数据库）
- 部署：Linux · Docker（`docker compose up -d` 一条命令拉起）

## 快速开始

### 本地运行
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
# 打开 http://127.0.0.1:8001/  → 先进「初始化向导」建目录 → 采集 → 整理 → 看报告/图谱
```

### Docker
```bash
docker compose up -d
# 打开 http://localhost:8001/
```
数据目录通过 `./data` 挂载到宿主机，可直接对接你现有的结构化知识库文件夹（Obsidian 兼容）。

## 功能（一期）

| 页面 | 说明 |
|---|---|
| 总览 | 笔记/主题/标签统计 + 快速入口 |
| 采集 | 抓取链接 / 粘贴正文 → 后台任务入库（含去重指纹） |
| 整理 | 全库/单篇去重、jieba 抽关键词、规则打标签、一句话摘要 |
| 报告 | 数据统计（近7日趋势/主题分布）+ 内容摘要 + 知识结构三视图，可导出 Markdown |
| 笔记库 | 检索（标题/正文/标签/关键词）+ 主题/标签过滤 + 详情 + 整理/删除 |
| 知识结构 | 由笔记库实时聚合的知识图谱（整合自知识图谱探索器） |
| 任务中心 | 长任务进度轮询 |
| 设置 | 数据目录 / 主题 / 标签 |
| 初始化向导 | 5 步建目录结构与模板，幂等 |

## 设计要点
- **文件即数据库**：笔记以 `meta.json + body.md` 存储，可被 Obsidian 打开、可 git 版本管理、可手动编辑修正。
- **双链知识分层（借鉴 Obsidian wiki）**：笔记分 `source`（来源）/ `entity`（实体）/ `concept`（概念）三层，`meta.json` 记录 `related_entities / related_concepts` 双向链接；来源正文里的 `## 相关实体 / ## 相关概念` 区块会在整理时被自动解析、为不存在的实体/概念建骨架页。—— 知识结构图谱即据此渲染 **source→entity→concept 关系网**。
- **纯手动驱动**：一期所有操作由用户触发，任务有明确进度反馈。
- **运行期不依赖外部服务**：整理/报告用内置规则，离线可用；AI 增强作为二期可选开关。
- **模块可替换**：采集器 / 整理器 / 报告器 / 索引器 / 任务执行器五模块独立，二期接调度器不改业务逻辑。

## 目录结构
```
knowledge-tool/
├─ app/
│  ├─ main.py        # FastAPI 入口
│  ├─ config.py      # 配置（数据根目录等）
│  ├─ models.py      # 数据模型（meta.json schema）
│  ├─ store.py       # 文件数据层
│  ├─ collector.py   # 采集器
│  ├─ cleaner.py     # 整理器
│  ├─ indexer.py     # 索引器
│  ├─ reporter.py    # 报告器
│  ├─ runner.py      # 任务执行器
│  └─ routers/api.py # RESTful API
├─ frontend/         # 原生前端 8 页（含 graph.html 知识结构图谱）
├─ templates/        # 向导生成的笔记模板
├─ data/             # 知识库数据根目录（git 忽略）
├─ Dockerfile
└─ docker-compose.yml
```

## API（前缀 /api）
`setup` · `collect` · `notes` · `search` · `reports/daily` · `stats/overview` · `graph/data` · `tasks` · `topics` · `tags`
