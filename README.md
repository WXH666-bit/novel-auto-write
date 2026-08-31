# 章回：长篇小说连续性自动写作工作流

“章回”是个人单机使用的中文长篇小说编剧室。它把人物状态、世界规则、时间线、关系、物品、主支线、伏笔、章节摘要和硬约束保存为可追溯的故事正典，生成内容必须经过审查包确认后才会进入后续记忆。

## 产品价值与连续性保障

- 正典是持久数据，不依赖聊天记录；每条事实带有章节、修订和原文范围等来源信息。
- 正文修订采用追加版本，历史正文不可覆盖；拒绝审核包不会改变正典版本。
- 严重矛盾默认阻止普通接受，强制接受必须填写理由并留下审计记录。
- 章节、正典变化和审计结果作为审核包原子提交；生成阶段、输入快照和产物持久化，重启后可从最近完成阶段恢复。
- 修改已确认旧章后，后续摘要和相关正典会标记待复核，并暂停继续生成，待记忆重建完成。
- SQLite 使用 WAL、外键和 FTS5；项目可导出带 schema 版本的 ZIP，API Key 不进入数据库或备份。

## 核心工作流

1. 新建项目，或导入 TXT/Markdown。
2. 预览拆章结果，调整章节的合并、拆分和标题。
3. 提取并审核故事圣经、人物、时间线、剧情线和伏笔，确认初始正典。
4. 冻结正典版本，按章节和场景生成上下文与规划。
5. 生成正文草稿，提取事实变化和线索，执行连续性与风格审查。
6. 对严重冲突进行最多两轮定向修订，生成章节与正典审核包。
7. 编辑正文、重新审查、拒绝或接受；接受时原子提交章节修订和正典版本。
8. 自动更新摘要、检索索引、审计日志和本地备份。

生成任务阶段为：`queued → preparing_context → planning → drafting → extracting → auditing → revising → awaiting_review → committing → completed`；异常时会进入 `failed`、`needs_retry` 或 `cancelled`。

## 功能清单

- 项目库、故事圣经、题材/视角/文风、章节树和三栏写作台。
- TXT/Markdown 导入：UTF-8、BOM、GB18030，识别中文“第 X 章”、序章、番外等标题，并提供拆章预览。
- 不可变章节修订、历史查看、差异审核、人物/时间线/剧情线/伏笔/冲突账本。
- 结构化正典与 SQLite FTS5 全文检索；上下文片段带来源引用并按预算裁剪。
- 可恢复生成任务、幂等键、租约、SSE 进度、流式正文和结构化 JSON 输出降级校验。
- Demo Provider、OpenAI-compatible Chat Completions/Responses 配置、角色模型映射、连接测试。
- 原子审核、拒绝、普通接受、强制接受、旧章失效传播、版本恢复和 ZIP 导入导出。
- Markdown 导入禁止原始 HTML/脚本；正文按数据处理，避免被当作提示词指令执行。

## Windows PowerShell 一键安装与启动

前置条件：Python 3.11+、Node.js（含 npm）和 PowerShell。

在项目根目录执行：

```powershell
Set-Location C:\Users\<用户名>\Desktop\novel_auto_write
.\setup.ps1
.\start.ps1
```

`setup.ps1` 会创建 `.venv`，升级 pip，安装 `requirements-dev.txt`，安装前端 npm 依赖并构建 `frontend/dist`。`start.ps1` 使用 `.venv` 中的 Python，以 `127.0.0.1:8000` 启动后端并提供已构建的前端。

如果本机禁止执行脚本，可在当前 PowerShell 会话临时放行：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

启动后打开 <http://127.0.0.1:8000>。默认仅监听本机回环地址；可在启动前设置 `NOVEL_HOST`、`NOVEL_PORT`、`NOVEL_DATA_DIR` 或 `NOVEL_DATABASE_URL` 环境变量。

## Demo Provider 与真实模型

未配置真实模型时使用明确标记的 Demo Provider，无需 API Key；它只生成可审阅样稿，不会自动写入正典。

真实模型可在“工作室设置”中配置，或调用 `PUT /api/providers/default`：

- `base_url`：OpenAI-compatible 服务的 Base URL，例如 `http://127.0.0.1:1234/v1`；
- `protocol`：`chat_completions` 或 `responses`；
- `model_role_mapping`：为规划、写作、提取、审查、修订角色指定模型；
- `context_length`、`timeout_seconds`、`capabilities`：上下文和能力参数；
- `api_key`：仅用于写入操作系统凭据库（Windows Credential Manager/keyring）。

数据库只保存 Provider 配置和凭据引用，不保存密钥本身；密钥不会写入日志、生成快照、项目 ZIP 或 API 返回。切勿把 API Key 写进故事正文、项目 JSON 或提交到 Git。

## 数据与备份目录

默认数据目录为项目根目录下的 `data/`：

```text
data/
├─ novel.sqlite3       # SQLite 主数据库
├─ novel.sqlite3-wal   # SQLite 运行时 WAL 文件（可能短暂出现）
├─ novel.sqlite3-shm   # SQLite 运行时共享内存文件（可能短暂出现）
├─ backups/            # 迁移、接受审核包等操作前的一致性快照
└─ uploads/            # 原始导入文件（按内容哈希保存）
```

在界面或 `GET /api/projects/{project_id}/export` 导出完整项目 ZIP；使用 `POST /api/projects/restore` 恢复。导出包含正文、修订、正典、时间线、剧情线和审核包，并排除 Provider 凭据。

## API 文档与常用入口

启动服务后访问：

- Swagger UI：<http://127.0.0.1:8000/docs>
- ReDoc：<http://127.0.0.1:8000/redoc>
- OpenAPI JSON：<http://127.0.0.1:8000/openapi.json>
- 健康检查：`GET /api/health`

主要 API 分组：`/api/projects`、`/api/projects/{id}/import/*`、`/api/projects/{id}/chapters`、`/api/chapters`、`/api/projects/{id}/canon`、`/api/projects/{id}/story-map`、`/api/projects/{id}/generations`、`/api/generations/{run_id}/events`、`/api/reviews`、`/api/providers`、`/api/projects/{id}/export` 和 `/api/projects/restore`。

## 测试与本地校验

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check backend
npm --prefix frontend run build
```

测试覆盖拆章、不可变修订、正典版本、审核原子性、旧章失效传播、生成幂等/恢复、Provider 兼容性和导出恢复等场景。

## 技术栈

- 后端：Python 3.11、FastAPI、Pydantic 2、SQLAlchemy 2 同步 Session、SQLite/WAL/外键/FTS5、Uvicorn。
- 前端：React 18、TypeScript、Vite、TanStack Query、Lucide 图标。
- 本地安全：`keyring` 系统凭据库、参数化 SQL、Markdown 清洗、SQLite 一致性快照。

## 首版边界

首版面向个人 Windows 单机使用，不包含登录、多人协作、云部署、分布式队列或外部连载发布；只支持 TXT/Markdown，不处理 DOCX/PDF。首版不提供多世界线分支、图数据库或向量数据库，优先使用结构化正典与可解释的 FTS5。系统不能保证模型永不犯错，可靠性来自持久正典、来源追踪、审查闸门、原子提交和可回滚备份。
