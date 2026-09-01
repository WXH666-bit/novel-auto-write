# 章回：长篇小说连续性写作室

“章回”把长篇小说的正文、人物状态、世界规则、时间线、关系、物品、剧情线、伏笔与审核记录保存成可追溯的故事正典。草稿只有在用户审核接受后，才会与正典在同一事务中生效；拒绝草稿不会污染后续记忆。

当前版本支持账号与完全私有的数据空间。每位用户只能看到自己的项目、章节、正典、任务、审核包、导入文件和模型配置。部署者可以选择“邮箱 + 密码并验证邮箱”或“用户名 + 密码、无需邮箱”两种登录模式。系统不内置 Demo 模型，新账号必须自行添加 Provider 并明确设为默认，才能开始生成。

## 连续性与隔离保证

- 正文修订只追加、不覆盖；每条事实保留章节、修订、原文范围和摘录。
- 严重矛盾默认阻止接受；强制接受必须填写理由并写入审计日志。
- 每个生成阶段持久化快照和内容哈希，重启后从最近完成阶段恢复。
- 修改已确认旧章会隔离受影响的后续摘要与正典，记忆重建前暂停继续生成。
- SQLite/FTS5 与 MySQL/ngram 都只索引已接受正文、已确认正典，并同时按用户和项目过滤。
- API Key 由用户自行提供，按 `user_id + provider_id` 存入操作系统凭据库；数据库、日志、任务快照和项目 ZIP 都不保存密钥。

## Windows 本地运行

前置条件：Python 3.11、Node.js（含 npm）、PowerShell。首次安装先执行：

```powershell
Set-Location C:\Users\<你的用户名>\Desktop\novel_auto_write
./setup.ps1
```

部署者必须为整套服务选择一种登录模式。默认的邮箱验证模式可使用 Mailpit 接收本地测试邮件：

```powershell
$env:NOVEL_AUTH_MODE = "email"
docker compose -f deploy/compose.local.yml up -d
./start.ps1
```

如果希望直接使用用户名和密码、不收集邮箱也不验证邮箱，则不需要 Docker 或 SMTP：

```powershell
$env:NOVEL_AUTH_MODE = "username"
./start.ps1
```

打开：

- 写作室：<http://127.0.0.1:8000>
- Mailpit 收件箱：<http://127.0.0.1:8025>
- API 文档：<http://127.0.0.1:8000/docs>

邮箱模式注册后，到 Mailpit 打开验证邮件；用户名模式注册后可直接登录。登录后在“模型设置”中添加 Provider。`setup.ps1` 会在项目根目录创建 `.venv`、安装后端依赖、安装 npm 依赖并构建前端；`start.ps1` 默认只监听 `127.0.0.1:8000`。

`NOVEL_AUTH_MODE` 只允许 `email` 或 `username`，默认是 `email`。它是服务器级配置，不是用户级选项；同一实例不会在登录页同时开放两种注册方式。应在首次上线前确定并保持稳定：改变该值只会改变登录入口，不会自动把已有邮箱账号转换为用户名账号，反之亦然。

用户名会做 NFKC、去除首尾空格和大小写归一化；长度为 3–64 个字符，可使用中文等文字、数字，以及位于中间的 `.`、`_`、`-`。例如 `Writer_01` 与 `writer_01` 视为同一账号。

用户名模式不收集邮箱，因此也没有“忘记密码”邮件找回入口；用户应自行妥善保管密码。已登录用户仍可在“账号与安全”中修改密码并撤销其他设备会话。

如果 PowerShell 禁止脚本执行：

```powershell
powershell -ExecutionPolicy Bypass -File ./setup.ps1
powershell -ExecutionPolicy Bypass -File ./start.ps1
```

邮箱模式不使用 Docker 时，可以在启动前设置标准 SMTP：

```powershell
$env:NOVEL_SMTP_HOST = "smtp.example.com"
$env:NOVEL_SMTP_PORT = "587"
$env:NOVEL_SMTP_USERNAME = "novel@example.com"
$env:NOVEL_SMTP_PASSWORD = "应用专用密码"
$env:NOVEL_SMTP_USE_TLS = "1"
$env:NOVEL_SMTP_FROM = "novel@example.com"
./start.ps1
```

## 认领升级前的旧项目

原单用户数据库升级时，旧项目会先归属一个不可登录的 `legacy_owner`，不会自动交给公网第一个注册者。由本机运维根据当前登录模式交给目标账号：

```powershell
# 邮箱模式：目标账号必须已完成邮箱验证
$env:PYTHONPATH = "backend"
./.venv/Scripts/python.exe -m app.cli claim-legacy --email "owner@example.com"

# 用户名模式：目标账号注册后即可认领
./.venv/Scripts/python.exe -m app.cli claim-legacy --username "owner_name"
```

命令不重写项目、章节、修订或正典内容，内容哈希保持不变；若旧项目含原始导入文件，会同时将它们安全迁入 `uploads/<user_id>/<project_id>/` 的新租户目录。旧版以裸 `provider_id` 保存的系统凭据也会原子地改名为 `user_id:provider_id`；请务必使用原服务系统账号执行命令，以便访问同一凭据库。迁移前会自动创建 SQLite 一致性快照。

## 添加模型 Provider

支持三种固定协议：

- `chat_completions`：OpenAI 兼容的 `POST /v1/chat/completions`；
- `responses`：OpenAI `POST /v1/responses`，结构化任务使用 `text.format`；
- `anthropic_messages`：Anthropic 原生 `POST /v1/messages`，结构化任务使用 `output_config.format`。

Anthropic 表单会自动填入 `https://api.anthropic.com/v1` 和默认 API 版本。Provider 可以配置六个角色模型：剧情规划、正文写作、事实提取、连续性审查、风格审查和定向修订。添加多个 Provider 后必须主动设置账户默认项；每次生成也可以临时选择另一个 Provider。任务一旦创建会冻结 Provider ID、协议、角色映射和配置版本，但永远不冻结密钥。

公网模式默认只允许 `api.openai.com` 与 `api.anthropic.com`。自定义网关需要运维加入 `NOVEL_ALLOWED_PROVIDER_HOSTS`；生产环境拒绝回环、私网、重定向和 DNS 重绑定。

## 数据与备份

```text
data/
├─ novel.sqlite3
├─ backups/                     # 迁移/审核前快照
└─ uploads/
   └─ <user_id>/<project_id>/   # 租户隔离的原始导入文件
```

项目 ZIP 包含正文、历史修订、正典、时间线、剧情线、审核包和原始导入文件，但不含 Provider 或密钥。SQLite 在迁移和审核接受前创建一致性数据库快照；MySQL 审核接受前创建用户项目 ZIP，基础设施迁移前使用 `deploy/backup-before-migrate.sh` 的 `mysqldump --single-transaction`，备份文件默认仅服务账号可读。

## Linux 生产部署

生产目标为单台应用主机、MySQL 8.4 LTS、固定 HTTPS 域名和固定 Linux 服务账号；只有邮箱模式需要标准 SMTP。示例位于：

- `deploy/compose.mysql.yml`：MySQL 8.4、`utf8mb4_0900_ai_ci`、`ngram_token_size=2`；
- `deploy/novel-auto-write.service`：systemd 服务；
- `deploy/Caddyfile`：HTTPS 反向代理；
- `.env.example`：应用环境变量参考。

生产必须设置 `NOVEL_ENV=production`、`NOVEL_AUTH_MODE=email` 或 `NOVEL_AUTH_MODE=username`、HTTPS 的公开地址、可信 Host、精确 CORS 来源和安全 Cookie。邮箱模式还必须配置 SMTP；用户名模式不会调用邮件服务。Linux 凭据库使用 Secret Service，服务必须以固定账号运行并拥有可用的 DBus/Secret Service 会话。多应用节点不在首版范围内；扩容前需把凭据迁移到外部 Secrets Manager。

执行数据库迁移：

```bash
export PYTHONPATH=backend
.venv/bin/python -m app.cli migrate
```

## 测试

```powershell
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m ruff check backend
npm --prefix frontend run build
```

CI 分别执行 SQLite 测试和 MySQL 8.4 集成测试。覆盖认证、CSRF、令牌过期/复用、跨租户 404、Provider 默认/临时选择、Anthropic 非流式与 SSE 错误、审核原子性、任务恢复、中文全文检索、备份密钥扫描，并从真实旧版 SQLite 表结构验证内容指纹、owner 外键和原始导入文件迁移。

## 技术栈与边界

- 后端：Python 3.11、FastAPI、SQLAlchemy 2、Alembic、SQLite/FTS5、MySQL 8.4/ngram。
- 前端：React、TypeScript、Vite、TanStack Query。
- 密码：Argon2id；会话：服务端随机令牌、HttpOnly/SameSite Cookie、Origin 与双提交 CSRF 校验。
- 首版只支持 TXT/Markdown，不含小说共享、多人协作、组织空间、多世界线分支、图数据库或分布式任务队列。

系统不能保证模型永不犯错；可靠性来自持久正典、来源追踪、审查闸门、原子提交、租户隔离和可恢复版本，而不是无限延长提示词。
