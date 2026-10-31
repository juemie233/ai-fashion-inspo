# AI 穿搭素材库 — 项目标准

## 核心规则

1. **代码标识符**（变量名、函数名、类名、表名、字段名）：使用英文，遵循语言社区惯例
2. **注释和文档**：全部使用中文
3. **用户界面文案**：所有页面提示、警告、错误信息、状态标签、按钮文字、空状态引导等，一律使用中文
4. **数据内容**（标签名、AI prompt 等）：使用中文
5. **禁止启动子代理**：项目内的一切开发工作（含代码迁移、重构、批量修改等）均由主会话直接完成，**不得启动 subagent / 子代理 / 并行代理**。需要并行或拆解的工作，改为在主会话内顺序执行或自行批量处理，保证每个改动的上下文完整、可追溯。
6. **服务启停由用户手动执行**：**禁止自动执行一键重启/启动脚本**（`scripts/restart.sh`、`scripts/ensure-services.sh` 等涉及服务启停的脚本）。当代码改动需要重启后端/前端/worker 才能生效时，**不得自行执行脚本**，只需明确提示用户「需要重启服务」，由用户手动执行。
7. **时间列单行显示**：所有表格/列表中的时间列必须保持单行（`white-space: nowrap`），禁止换行；新增或修改时间列时同样遵守（Arco 表格单元格默认 `word-break: break-all`，时间字符串在窄列下会被断行，需显式加 nowrap）。实现约定：render 函数列统一用 `web/src/utils/format.ts` 的 `renderTimeCell(text, extra?)`（内部包 nowrap span，extra 可附加颜色/class）；模板列包 `<span style="white-space: nowrap">`。
8. **新建文件位置受限**：**新文件的落点必须是「已登记」的位置**——即本文档 [项目结构](#项目结构) 与 [前端文件拆分约定](#前端文件拆分约定) 列出的目录，加上仓库中既有的约定目录（`backend/app/**`（含 `services/task_runners`）、`backend/{scripts,tests,alembic}`、`web/src/{views,components,composables,stores,api,types,utils,router,constants,styles,assets}`、`mobile/`、`browser-extension/`、`shared/types/`、`docs/`、`scripts/`（含 `scripts/oneoff/` 一次性脚本））。**禁止**在仓库根目录散落文件、随手新建目录、或把产物写到仓库之外的路径（系统临时目录、桌面、其他盘）。
   - **例外**：业务确实需要新位置（新增顶层文件、新增目录、必须落在仓库外）时，**先说明理由并征得用户同意**；若属长期结构，同意后同步登记进「项目结构」一节，使其成为「已登记」位置。
   - **临时文件**（排查脚本、一次性诊断、提交信息草稿等）可以直接创建，但必须满足两条：**用完即删**（`git status` 自查，不得残留）且**不得入库**（不 `git add`、不提交）；优先落在系统临时目录或已 gitignore 的位置，不得把临时产物留在仓库根、`scripts/`、`storage/` 里冒充交付物。
   - **判据**：提交前问自己「这个新文件的位置在本文档里登记过吗」——没有登记且未经同意，就换到已登记位置，或先来问。

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
│       ├── router/        # 路由表与导航守卫
│       ├── constants/     # 前端常量（筛选选项 / 默认值 / 枚举映射）
│       ├── styles/        # 全局样式（Arco 主题、场景样式）
│       ├── types/         # 跨组件复用的 TS 类型
│       └── utils/         # 纯工具函数（去重）
├── mobile/                # React Native 移动端
├── browser-extension/     # Chrome 浏览器插件
├── shared/types/          # 前后端共享的类型定义
├── docs/                  # 方案 / 实现说明、实验报告、移动端构建手册
└── scripts/               # 工具脚本（oneoff/ 为一次性/诊断脚本，见其 README）
```

## API 设计约定

- URL：`/api/{resource}`，RESTful 风格
- 请求/响应：JSON
- 分页参数：`page`（从 1 开始，默认 1）、`size`（默认 50，**上限 200**，超限由 FastAPI 直接 422）。新接口一律沿用 `Query(50, ge=1, le=200)`，不要放开上限——`size=10000` 会把整个素材库拖进一次响应
- **时间格式（跨层契约，别各写一份）**：数据库存 **naive UTC**（`backend/app/utils/time.py` 的 `utcnow()`）；API 输出**必须带时区后缀**，常规走 `format_utc()` → `2026-10-30T14:00:00Z`。人脸模块的历史载荷用 `+00:00`（`utcnow_iso()`，刻意保持不改口径），前端 `new Date()` 两种都能正确转本地时间。**禁止输出无后缀的时间字符串**——那会被浏览器按本地时间解析，正是「差 8 小时」的来源
- 错误响应格式：

  ```json
  {
    "detail": "错误描述"
  }
  ```

  **不引入 `code` 字段**（2026-10-30 决定）：前端只把 `detail` 当提示文案展示（`web/src/utils/apiError.ts`），没有任何按错误类型分支的消费者；给 27 个 router 加错误码再配一套前端映射是纯开销。等真的需要按类型处理（例如 409 冲突要弹「合并还是覆盖」）时再统一引入，不要半套半套地加

## 数据库约定

- 主键：**`inspirations` 用 UUID 字符串**（`String(36)`，36 位小写带连字符）——浏览器插件/移动端离线写入时需要客户端可预先生成 ID，且素材 ID 会出现在分享链接里，不能可枚举；**其余业务表一律自增整数**（`Integer, primary_key=True, autoincrement=True`）。新表默认自增，只有「需要客户端生成 ID」的表才用 UUID。两套并存有明确边界，不要为新表随手挑一种
- 时间戳：`created_at`、`updated_at`，UTC（存 naive UTC，输出带后缀，见「API 设计约定」的时间格式）
- 外键：`{table}_id` 格式
- 多对多关联表：`{table1}_{table2}` 格式
- 软删除：仅 `inspirations` 素材使用（垃圾桶，`deleted_at` / `trash_reason` / `trash_source` **三字段同真同假**，可恢复，默认不自动回收；合法性用 `inspiration_service.verify_trash_invariants` 校验）；其余表仍直接物理删除
- **删除原因与来源要分开看**（这是两件事，别再合并成一个枚举）：
  - `trash_reason`（`TrashReason`，`schemas/inspiration.py`）= **为什么删**：`质量差`/`重复`/`不喜欢`/`隐私`/`其他` 是用户主动删，`AI生成` 是系统自动判定
  - `trash_source` = **谁移进来的**：`manual`（手动）/ `auto`（质量审核自动）。统计「自动清理了多少」只看它，不要靠猜原因
- **负样本口径**：垃圾桶素材整体作为质量初筛器的负样本，但 **`隐私` 除外**（`quality_learner.NEGATIVE_EXCLUDED_TRASH_REASONS`）——隐私图不是「穿得不好」，学进去会让分类器把露肤/私密场景判成低质。新增删除原因时先判断它适不适合进负样本，不适合就加进该常量
- SQLite：**已开 WAL**（`database.py` 启动执行 `PRAGMA journal_mode=WAL`，读不阻塞写）+ `busy_timeout` 30s；worker 与 API 共用同一个库文件，写操作按**串行化**设计，长事务里不要再开独立会话去写（会等到 busy_timeout 超时）
- 索引：常用过滤/排序列加 `index=True`（现有 73 处）；软删除表上的唯一约束**必须用部分唯一索引**（`sqlite_where=text("deleted_at IS NULL")`，见 `ix_inspirations_source_platform_id`），否则素材进垃圾桶后该值被永久占死，无法重新采集/导入
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
| `generated` | AI 生成 |
| `batch_import` | 目录导入 |
| `url_import` | 链接导入 |

映射的**唯一真源**是 `web/src/utils/sourceLabel.ts` 的 `SOURCE_TYPE_LABELS`；素材库/搜索页的来源筛选 chip 由 `buildSourceOptions()` 从同一张表生成，新增类型不需要另改筛选器。

新增来源类型时按顺序改三处（只改前后端 label 会出现「库里能存、前端显示英文值」）：

1. `web/src/utils/sourceLabel.ts` —— 加 `SOURCE_TYPE_LABELS` 条目（筛选器自动跟上）
2. `mobile/utils/sourceLabel.ts` —— 移动端同款映射
3. `backend/app/schemas/inspiration.py` —— 若后端对 `source_type` 有枚举/字面量校验，同步加；写入侧（`scrapers/` 或对应 service）实际落库的值必须与上面完全一致

改完用「上传 / 插件 / 采集」三条路径各造一条数据，确认列表显示与筛选都对。

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
- **默认分支固定为 `master`**：开发、提交、推送一律在 `master` 上进行（`git push origin master`）；功能分支只在用户明确要求时使用，且分支合并后**不要**继续在旧分支上提交。
- **不再按行数决定是否提交**：任何类型（feat / fix / refactor / style / docs / chore）的改动，完成后一律自动 `git commit` 并 `git push origin master`。**唯一例外**：用户明确说了「先别提交」。
  （2026-10-31：此前「暂停自动推送」的临时规则已由用户取消，恢复双端推送。）
- **提交粒度仍见下节**：自动提交 ≠ 碎片提交 —— 同一轮的 docs/chore 小改合并成一条，避免历史被格式/文档噪音刷屏。
- **推送后不要等 CI**：提交推送完成即结束本轮，**禁止**轮询 / 等待 / 查询 GitHub Actions 结果（不调 CI 接口、不 `sleep` 等结果）。CI 是否通过由用户在 GitHub 上查看；若失败，用户会把错误信息贴回来，届时再修。
- **TODO 维护**：完成 TODO.md 中某个功能后，自动删除对应的文档条目（章节标题 + 描述 + 方案 + 涉及模块等完整内容），保持 TODO.md 仅包含未完成项。
```

### 提交粒度约定

为保持 `git log` 可读、便于 `git bisect`，遵循以下粒度规则（当前仓库提交偏碎，优先执行第 1、2 条）：

1. **「功能 + 审查修复」合并提交**：同一功能域内的跟手修复（如「代码审查修复 N 个缺陷」）与它的功能提交属于同一逻辑变更，应合并为一个提交（改完后一次性提交，或 push 前 `git rebase -i` squash），不要拆成两个提交。
2. **微提交攒批**：`docs`/`chore` 类的纯格式、忽略文件、TODO 整理等小改动（通常 < 100 行），不要单独占一条历史，攒一批再提交，或并入相邻的功能提交。
3. **大提交保持单一主题**：一个提交只做一件事；若一个提交里同时包含多个不相关的主题（如「自动化测试」+「模型配置统一」），应拆分为多个提交。
4. **自动提交的合并约束**：自动提交时，若本次改动属于上一个提交同一功能域的收尾/修复，优先 `git commit --amend`（或 `git rebase -i` 合并）而非新建提交；仅在属于新的独立主题时才新建提交。

## 文档要求（按任务规模）

**原则：文档是为了让后来者（或半年后的自己）能独立复原「为什么这么做、怎么落地」，不是为了留痕。**
按任务规模决定是否单独产出文档：

### 小任务：不单独生成文档

判定（满足其一即可）：

- 单文件 / 局部改动：修 bug、改样式、补校验、调参数、加/改一个字段；
- 一次性排查、临时脚本、配置调整；
- 已有成熟测试能锁住行为的小改动。

要求：改动说明写进**提交信息**（做了什么、为什么、怎么验证），不新增 `docs/` 文档；
一次性排查脚本用完即删（见「核心规则」第 8 条）。

### 大任务 / 大工作量：必须生成文档

判定（满足其一即算）：

- 跨模块新功能（前端 + 后端 + 数据/任务链路，如「接入某能力」「新页面 + 新接口 + 新表」）；
- 新增或改动**数据模型 / 数据库迁移 / API 契约 / 任务类型**；
- 引入新依赖、第三方服务、外部数据源，或改动部署/构建方式；
- 涉及性能、安全、并发、数据一致性等方案性取舍；
- 预估工作量 ≥ 2 人日，或改动面 ≥ 5 个文件且需要设计取舍；
- 需要「先评估、再开发」的产品/架构需求。

要求：

1. 先出**评估/方案文档**（可行性、方案对比、改造点、风险），评审通过再写代码；
2. 开发完成后补齐「实际实现」部分（数据流、接口、配置、验证方式、已知边界），
   **以最终代码为准**，不保留与实现不符的设想；
3. 文档放 `docs/`，用中文文件名（如 `docs/xxx方案.md`、`docs/xxx实现说明.md`）；
4. 涉及用户可感知能力时，同步更新 README 对应表格/清单；
5. 大任务的跟手修复并入同一文档与同一功能提交，不单独造文档。

### 测试 / 实验报告：每次都要留下可读文档

**任何一次测试、对照实验、参数扫描、PoC 验证，做完都必须产出一份「人能独立读懂」的报告**，
放在 `docs/`（中文文件名），不靠聊天记录承载结论。报告至少包含：

1. **目的与假设**：这次要验证什么、不验证什么；
2. **生成流程**：用的模型/档位、关键参数（steps/cfg/分辨率/seed）、数据来源与样本量、随机种子；
3. **结果**：量化指标（如姿势保真、身份相似、耗时、失败率）+ 逐样本结果；
4. **图片对比**：把「输入 / 中间产物 / 最终结果」拼成对比图（拼图文件放 `docs/` 或注明 storage 路径），
   让审阅者不用打开一堆文件就能横向比较；
5. **判断与下一步**：成功/失败/部分成功，以及下一次实验要改什么。

**豁免**：一次性诊断/对比脚本（跑完即删、结论只写进提交信息或 TODO）不必单独出报告；
参数微调且已有测试能锁住行为的改动同理。判据是「这个结论以后还会被谁用到」——
没人用就写进提交信息，别为一个一次性数字造一篇文档。

> 目的：人与 AI 协同审图时，结论必须可复核——报告要能脱离对话单独阅读。

### 与其他约定的关系

- `docs/decisions/`（脚本按周生成的提交决策台账）是本机派生文档，**不能当作大任务文档**
  替代品，也不入库（见 `.gitignore`）；
- 文档本身同样遵守「注释与文档用中文」的约定。

## 自动化测试

- 后端：`cd backend && pytest`（集成测试 + 服务单测；使用临时数据库/临时存储，**不触碰真实数据**）
- 前端：`cd web && npm test`（vitest：纯函数 / composable / store）
- 移动端：`cd mobile && npm test`（jest-expo + @testing-library/react-native；含**裸字符串检查**——遍历渲染树断言非 `<Text>` 节点下没有字符串/数字，专门防 `Text strings must be rendered within a <Text> component` 这类只有原生 RN 才会拦、Web 预览与普通渲染测试都发现不了的白屏；改页面后请补一条渲染用例）
- 前端 lint：`cd web && npm run lint`（ESLint flat config + Prettier 协同；要求 0 error，`no-explicit-any` 等告警渐进清理）
- 后端格式：Python 侧 `ruff format` 同样**只对本次新增/改动的文件**跑，且**不要把它和功能改动混进同一个提交**（`ruff format` 会把文件里的历史代码一起重排，实测一次就让 `routers/tags.py`、`tag_query.py` 的 diff 里混进几十行无关改动，脏化 `git blame/bisect`）。要格式化就单独提一个 `style:` 提交
- 前端格式：`npm run format:check` 只检查**本次改动**的前端文件（`scripts/check-web-format.mjs`；全库检查会因约 45 个历史未格式化文件永远为红（截至 2026-10-30，只减不增））、`npm run format <文件>` 增量格式化；**禁止**全库 `prettier --write`（会产生数千行无关 diff 污染 git 历史），只格式化本次改动的文件。提交时 lint-staged 已对暂存的前端文件自动 `prettier --write`
- 一键门禁：`bash scripts/test.sh`（后端 pytest+覆盖率棘轮/ruff/迁移链自检/库漂移 + **脚本规范（只读连接/argparse）** + 前端 vue-tsc/vitest/eslint/死代码基线/格式 + **移动端 tsc/jest**，共 **13 项**（截至 2026-10-30，以脚本自身输出为准），与 `.github/workflows/ci.yml` 对齐；CI 里 `mobile` 是独立 job，装依赖用 `npm ci --legacy-peer-deps`——移动端有 peer 冲突（RNTL 12 需要 React 18））
- 端到端冒烟：`cd web && npm run test:e2e`（Playwright，11 条、约 50 秒，截至 2026-10-30；**需后端在跑**——Playwright 只自动起前端 dev server，开跑前 `web/e2e/global-setup.ts` 会预检 `/api/health` 并给出启动命令；串行执行，产物在 `web/test-results/`）
- 测试依赖：`pip install -r backend/requirements-dev.txt`（后端）；vitest 等已含在 `web/package.json` devDependencies
- **约定**：修改核心链路（软删除/垃圾桶/内容去重/破坏性接口认证/人物模块）或新增破坏性接口时，须在对应 `backend/tests/` 或 `web/src/**/__tests__/` 补充用例并跑通
- 误将测试文件写入真实存储目录时，用 `python scripts/clean_test_files.py --delete` 清理（判定：DB 无记录 + 测试时段 + 纯色小图）

## 移动端构建（Android APK）

构建命令、本机工具链路径、prebuild 后要补的配置、提速配方、真机踩坑（图标字体 / 分享 Intent /
视频播放 / 冷启动竞态）全部在 [docs/移动端.md](docs/移动端.md)。

该文含**本机绝对路径与代理端口**等环境特定信息，换机器时按原文替换；标准里不重复这些内容。

## 安全与数据合规

本项目的部署边界是**单机本地运行**（后端与 Ollama 都在本机，素材不出内网），下面按这个前提写。
一旦要对外提供服务（公网 / 多用户），必须重估并补限流、租户隔离与内容审核。

- **认证**：`settings.api_key` 为空时只放行**回环地址**（`main.py` 中间件 + `utils/auth.py` 的 `require_api_key`）；破坏性接口（清空垃圾桶、重置 AI 数据、批量删除等）必须显式 `Depends(require_api_key)`。新增破坏性接口照此办理，不要只靠「前端没给入口」
- **密钥与环境变量**：只走 `.env`（已 gitignore）或环境变量，**不写进代码、不写进文档、不提交**；示例值放 `.env.example`
- **CORS**：白名单在 `config.cors_origins`（默认只放行 `localhost:17777` 前端）。走 Vite 代理时前端与 `/api` 同源，不用放开；只有前端单独托管、浏览器直连 `:18888` 时才加具体源——**不要写 `*`**（`allow_credentials=True` 下 `*` 本身也不成立）
- **上传限制**：大小按内容类型分档（`file_service.resolve_size_limit`，视频单独一档），真实格式用 `validate_media` 校验，不信任扩展名与 `Content-Type`；新增上传入口必须复用这两个函数
- **隐私与数据**：`隐私` 原因删除的素材**不进负样本**（见「数据库约定」）；分享 / 导出 / 批量下载默认排除垃圾桶；人物人脸特征属生物特征数据，只在本地库内使用，不随素材导出
- **采集合规**：任务串行 + 随机延时；重试有上限，遇限流/验证码即停并留审计，不做「换 IP 硬刷」；`playwright-stealth` 只用于降低误判，不用于绕过登录与风控
- **明确不做**（当前无消费者，写了也是空转）：API 限流/防刷、上传病毒扫描、第三方内容审核、多租户隔离。触发条件：开始对外提供服务，或出现第二个用户

## 代码审查范围

- 自动代码审查（无用文件 / 重复代码 / 未使用依赖等审计与清理任务）**忽略** `scripts/` 一个目录：其中的工具脚本、调试脚本、一次性迁移脚本不在自动审查结论范围内，不得将其列入删除 / 重构建议。
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
