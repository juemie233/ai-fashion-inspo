# AI 穿搭素材库 — 项目标准

## 核心规则

1. **代码标识符**（变量名、函数名、类名、表名、字段名）：使用英文，遵循语言社区惯例
2. **注释和文档**：全部使用中文
3. **用户界面文案**：所有页面提示、警告、错误信息、状态标签、按钮文字、空状态引导等，一律使用中文
4. **数据内容**（标签名、AI prompt 等）：使用中文
5. **禁止启动子代理**：项目内的一切开发工作（含代码迁移、重构、批量修改等）均由主会话直接完成，**不得启动 subagent / 子代理 / 并行代理**。需要并行或拆解的工作，改为在主会话内顺序执行或自行批量处理，保证每个改动的上下文完整、可追溯。
6. **服务启停由用户手动执行**：**禁止自动执行一键重启/启动脚本**（`scripts/restart.sh`、`scripts/ensure-services.sh` 等涉及服务启停的脚本）。当代码改动需要重启后端/前端/worker 才能生效时，**不得自行执行脚本**，只需明确提示用户「需要重启服务」，由用户手动执行。
7. **时间列单行显示**：所有表格/列表中的时间列必须保持单行（`white-space: nowrap`），禁止换行；新增或修改时间列时同样遵守（Arco 表格单元格默认 `word-break: break-all`，时间字符串在窄列下会被断行，需显式加 nowrap）。实现约定：render 函数列统一用 `web/src/utils/format.ts` 的 `renderTimeCell(text, extra?)`（内部包 nowrap span，extra 可附加颜色/class）；模板列包 `<span style="white-space: nowrap">`。

## 代码探索策略（jcodemunch MCP）

代码导航一律优先使用 jCodeMunch-MCP，不要回退到 Read / Grep / Glob / Bash 做代码探索。
**例外：** 即将编辑某个文件时用 `Read` —— harness 要求 `Edit`/`Write` 之前必须先 `Read`。先用 jCodeMunch **定位和理解**代码，再只 `Read` 你要改的那个文件。

本服务器运行 **front door（前门）** 表面：三个工具可触达所有 jCodeMunch 能力，因此工具列表保持精简，目录仅在需要时才拉取。

**会话开始时：**
1. `order { "action": "resolve_repo", "args": { "path": "." } }` —— 确认项目已索引。若未索引：`order { "action": "index_folder", "args": { "path": "." } }`

**之后针对任何任务：**
- 明确知道要什么 → `order { "action": "<name>", "args": { ... } }`
- 知道目标但不知用哪个工具 → `route { "query": "用一句话描述你的任务" }` 自动选择动作并构造参数
- 想看有哪些能力 → `menu { "query": "你想做什么" }` 返回匹配的动作及示例参数
- 想看完整目录与使用规则 → `jcodemunch_guide`

`menu` 与 `jcodemunch_guide` 会列出本服务器可运行的所有动作，包括不在你工具列表里的。这是预期行为：front door 就是调用它们的方式。

**解读结果：**
- `verdict` 为 `no_implementation_found` 是「不存在」的证据，直接报告缺失，不要换措辞重搜。
- `verdict` 为 `degraded` 表示某通道不可用，因此「不存在」并未被证明，依赖结果前先读 note。
- `source: ""` 同时带 `source_status` 表示函数体无法读取，而非符号为空。

**编辑文件后：**
- 装了 PostToolUse hook（Claude Code）时，编辑过的文件会自动重建索引。
- 否则编辑后用 `order { "action": "register_edit", "args": { "paths": [...] } }`，批量改动时合并调用。

**每个会话宣告一次你的模型**，让服务器据此调整回答大小：`announce_model { "model": "<你的模型 id>" }`。

## 技术栈

| 层级 | 技术 | 版本要求 |
| ------ | ------ | ---------- |
| 后端框架 | Python + FastAPI | 3.12+ |
| 数据库 | SQLite + SQLAlchemy (async) | — |
| Web 前端 | Vue 3 + Vite + TypeScript | Node 20+ |
| 移动端 | React Native (Expo) | SDK 52+ |
| AI 推理 | Ollama + Qwen3-VL:8B-Instruct | 本地 |
| 浏览器插件 | Chrome Extension Manifest V3 | — |
| 采集引擎 | Playwright + playwright-stealth | — |

## 代码风格

### Python

- 遵循 PEP 8
- 类型注解必须（`disallow_untyped_defs = True`）
- 异步优先：数据库、HTTP、文件 I/O 均使用 async/await
- Docstring 格式：Google 风格（中文描述）
- 字符串：优先使用双引号，f-string 用于格式化

### TypeScript / Vue

- 使用 Composition API + `<script setup lang="ts">`
- ESLint + Prettier 格式化
- 组件命名：PascalCase
- 文件名：Vue 组件 PascalCase，工具函数 camelCase，目录 kebab-case

### React Native

- Expo Router 文件路由
- 组件：PascalCase，hooks：`useXxx` 命名
- 样式：NativeWind (Tailwind CSS)

## 项目结构

```
fashion-inspo/
├── backend/                # Python 后端
│   ├── app/
│   │   ├── main.py        # FastAPI 入口
│   │   ├── config.py      # Pydantic 配置
│   │   ├── database.py    # 数据库引擎 + 会话
│   │   ├── models/        # SQLAlchemy 数据模型
│   │   ├── schemas/       # Pydantic 请求/响应模型
│   │   ├── routers/       # API 路由
│   │   ├── services/      # 业务逻辑层
│   │   ├── scrapers/      # 平台爬虫
│   │   └── utils/         # 工具函数
│   ├── storage/           # 本地文件存储（gitignore）
│   ├── alembic/           # 数据库迁移
│   └── requirements.txt
├── web/                   # Vue 3 Web 前端
│   └── src/
│       ├── views/         # 页面组件（只做编排）
│       ├── components/    # 通用组件（按域分子目录）
│       ├── stores/        # Pinia 状态
│       ├── api/           # API 客户端
│       ├── composables/   # Vue composables
│       ├── types/         # 跨组件复用的 TS 类型
│       └── utils/         # 纯工具函数（去重）
├── mobile/                # React Native 移动端
├── browser-extension/     # Chrome 浏览器插件
├── shared/types/          # 前后端共享的类型定义
└── scripts/               # 工具脚本
```

## API 设计约定

- URL：`/api/{resource}`，RESTful 风格
- 请求/响应：JSON
- 分页参数：`page`（从 1 开始）、`size`
- 时间格式：ISO 8601 字符串
- 错误响应格式：

  ```json
  {
    "detail": "错误描述"
  }
  ```

## 数据库约定

- 主键：`id`（UUID 字符串或自增整数）
- 时间戳：`created_at`、`updated_at`，UTC
- 外键：`{table}_id` 格式
- 多对多关联表：`{table1}_{table2}` 格式
- 软删除：仅 `inspirations` 素材使用（垃圾桶，`deleted_at` / `trash_reason`，可恢复，默认不自动回收）；其余表仍直接物理删除
- 删除原因枚举：`质量差`/`重复`/`不喜欢`/`隐私`/`其他`（垃圾桶素材全部作为负样本学习输入；`AI生成` 用于疑似 AI 素材自动移入，同为垃圾桶原因）
- 数据库迁移：使用 Alembic（`backend/alembic/`）。新增字段/表时先改 ORM 模型，再 `alembic revision --autogenerate -m "描述"` 生成迁移、`alembic upgrade head` 应用；**不再往 `db_migrations.py` 的 `_SCHEMA_COLUMNS` 手写追加**（`ensure_schema` 仅作兼容兜底）

## 命名约定

| 场景 | 风格 | 示例 |
| ------ | ------ | ------ |
| Python 模块/包 | snake_case | `file_service.py` |
| Python 类 | PascalCase | `InspirationTag` |
| Python 函数/变量 | snake_case | `get_or_create_tag()` |
| Python 常量 | UPPER_SNAKE_CASE | `SEED_TAGS` |
| TS/Vue 组件 | PascalCase | `InspirationCard.vue` |
| TS 函数/变量 | camelCase | `useInspirations()` |
| TS 类型/接口 | PascalCase | `InspirationOut` |
| 数据库表 | snake_case（复数） | `inspirations` |
| 数据库列 | snake_case | `source_type` |

## 前端文件拆分约定

- 视图只做编排（数据加载 + 子组件组装 + 事件接线），单个视图超过 ~800 行优先拆解。
- 自成体系的状态 + 逻辑抽 `composables/useXxx.ts`，内部自行 `useMessage()`，不直接操作 DOM。
- 跨组件复用的 TS 接口放 `web/src/types/{domain}.ts`，不在组件内重复定义。
- 多处重复的纯函数收敛 `web/src/utils/`（如 sourceLabel、formatSize），禁止多文件重复定义。
- 子组件按域放 `web/src/components/{admin|scraper|search|tag|upload|model|inspiration}/`，props + emit 通信。

## 来源类型映射

数据库 `source_type` 字段在前端必须显示为中文：

| 数据库值 | 显示文案 |
| ---------- | ---------- |
| `manual_upload` | 手动上传 |
| `scraper` | 自动采集 |
| `xiaohongshu` | 小红书 |
| `douyin` | 抖音 |
| `browser_extension` | 浏览器插件 |

新增来源类型时，只需在 `web/src/utils/sourceLabel.ts` 与 `mobile/utils/sourceLabel.ts` 各改一处。

## Git 提交格式

```
<类型>: <简短描述>

<详细说明（可选）>

类型：
- feat: 新功能
- fix: 修复
- refactor: 重构
- style: 样式/格式
- docs: 文档
- chore: 工具/配置

注意事项：
- **fix（修复）提交无限制**：修复功能的代码提交不受行数限制，完成后自动 `git commit` 并 `git push origin master`。
- **其他类型 < 100 行**：不自动提交。仅在用户明确要求时提交。
- **其他类型 ≥ 100 行**：自动 `git commit` 并 `git push origin master`。
- **TODO 维护**：完成 TODO.md 中某个功能后，自动删除对应的文档条目（章节标题 + 描述 + 方案 + 涉及模块等完整内容），保持 TODO.md 仅包含未完成项。
```

### 提交粒度约定

为保持 `git log` 可读、便于 `git bisect`，遵循以下粒度规则（当前仓库提交偏碎，优先执行第 1、2 条）：

1. **「功能 + 审查修复」合并提交**：同一功能域内的跟手修复（如「代码审查修复 N 个缺陷」）与它的功能提交属于同一逻辑变更，应合并为一个提交（改完后一次性提交，或 push 前 `git rebase -i` squash），不要拆成两个提交。
2. **微提交攒批**：`docs`/`chore` 类的纯格式、忽略文件、TODO 整理等小改动（通常 < 100 行），不要单独占一条历史，攒一批再提交，或并入相邻的功能提交。
3. **大提交保持单一主题**：一个提交只做一件事；若一个提交里同时包含多个不相关的主题（如「自动化测试」+「模型配置统一」），应拆分为多个提交。
4. **自动提交的合并约束**：命中「≥ 100 行自动提交」时，若本次改动属于上一个提交同一功能域的收尾/修复，优先 `git commit --amend`（或 `git rebase -i` 合并）而非新建提交；仅在属于新的独立主题时才新建提交。

## 自动化测试

- 后端：`cd backend && pytest`（集成测试 + 服务单测；使用临时数据库/临时存储，**不触碰真实数据**）
- 前端：`cd web && npm test`（vitest：纯函数 / composable / store）
- 前端 lint：`cd web && npm run lint`（ESLint flat config + Prettier 协同；要求 0 error，`no-explicit-any` 等告警渐进清理）
- 前端格式：`npm run format:check` 检查、`npm run format <文件>` 增量格式化；**禁止**全库 `prettier --write`（会产生数千行无关 diff 污染 git 历史），只格式化本次改动的文件
- 测试依赖：`pip install -r backend/requirements-dev.txt`（后端）；vitest 等已含在 `web/package.json` devDependencies
- **约定**：修改核心链路（软删除/垃圾桶/内容去重/破坏性接口认证/人物模块）或新增破坏性接口时，须在对应 `backend/tests/` 或 `web/src/**/__tests__/` 补充用例并跑通
- 误将测试文件写入真实存储目录时，用 `python scripts/clean_test_files.py --delete` 清理（判定：DB 无记录 + 测试时段 + 纯色小图）

## 代码审查范围

- 自动代码审查（无用文件 / 重复代码 / 未使用依赖等审计与清理任务）**忽略** `scripts/` 与 `backend/scripts/` 两个目录：其中的工具脚本、调试脚本、一次性迁移脚本不在自动审查结论范围内，不得将其列入删除 / 重构建议。
- scripts 目录的代码审查由用户**手动指定**时执行；未经用户明确要求，不对该目录文件做审查类改动。
- 本约定不影响正常功能开发与 bug 修复：业务功能需要新增 / 修改脚本时照常进行。
- **链路走查约定**：涉及核心链路（垃圾桶软删除 / 采集入库 / AI 质量审核与负样本 / 任务队列执行）的审查，按「链路」走查而非仅按文件：一人从 router → service → worker → 前端全程走完一条链路，重点核对事务边界、状态转移（软删除三字段同真同假）、幂等、审计留痕、枚举一致性；垃圾桶状态合法性可调用 `inspiration_service.verify_trash_invariants` 校验。

## 环境要求

- Windows 11
- Python 3.12+
- Node.js 20+
- Ollama（本地 AI 推理）
- ffmpeg（视频关键帧提取）
- Git Bash（Shell 环境）
