# AI 穿搭素材库 — 项目标准

## 核心规则

1. **代码标识符**（变量名、函数名、类名、表名、字段名）：使用英文，遵循语言社区惯例
2. **注释和文档**：全部使用中文
3. **用户界面文案**：所有页面提示、警告、错误信息、状态标签、按钮文字、空状态引导等，一律使用中文
4. **数据内容**（标签名、AI prompt 等）：使用中文
5. **禁止启动子代理**：项目内的一切开发工作（含代码迁移、重构、批量修改等）均由主会话直接完成，**不得启动 subagent / 子代理 / 并行代理**。需要并行或拆解的工作，改为在主会话内顺序执行或自行批量处理，保证每个改动的上下文完整、可追溯。
6. **服务启停由用户手动执行**：**禁止自动执行一键重启/启动脚本**（`scripts/restart.sh`、`scripts/ensure-services.sh` 等涉及服务启停的脚本）。当代码改动需要重启后端/前端/worker 才能生效时，**不得自行执行脚本**，只需明确提示用户「需要重启服务」，由用户手动执行。
7. **时间列单行显示**：所有表格/列表中的时间列必须保持单行（`white-space: nowrap`），禁止换行；新增或修改时间列时同样遵守（Arco 表格单元格默认 `word-break: break-all`，时间字符串在窄列下会被断行，需显式加 nowrap）。实现约定：render 函数列统一用 `web/src/utils/format.ts` 的 `renderTimeCell(text, extra?)`（内部包 nowrap span，extra 可附加颜色/class）；模板列包 `<span style="white-space: nowrap">`。
8. **新建文件位置受限**：**新文件的落点必须是「已登记」的位置**——即本文档 [项目结构](#项目结构) 与 [前端文件拆分约定](#前端文件拆分约定) 列出的目录，加上仓库中既有的约定目录（`backend/app/**`（含 `services/task_runners`）、`backend/{scripts,tests,alembic}`、`web/src/{views,components,composables,stores,api,types,utils}`、`mobile/`、`browser-extension/`、`shared/types/`、`docs/`、`scripts/`（含 `scripts/oneoff/` 一次性脚本））。**禁止**在仓库根目录散落文件、随手新建目录、或把产物写到仓库之外的路径（系统临时目录、桌面、其他盘）。
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
│       ├── types/         # 跨组件复用的 TS 类型
│       └── utils/         # 纯工具函数（去重）
├── mobile/                # React Native 移动端
├── browser-extension/     # Chrome 浏览器插件
├── shared/types/          # 前后端共享的类型定义
└── scripts/               # 工具脚本（oneoff/ 为一次性/诊断脚本，见其 README）
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
- **默认分支固定为 `master`**：开发、提交、推送一律在 `master` 上进行（`git push origin master`）；功能分支只在用户明确要求时使用，且分支合并后**不要**继续在旧分支上提交。
- **不再按行数决定是否提交**：任何类型（feat / fix / refactor / style / docs / chore）的改动，完成后一律自动 `git commit` 并 `git push origin master`。**唯一例外**：用户明确说了「先别提交」。
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

## 自动化测试

- 后端：`cd backend && pytest`（集成测试 + 服务单测；使用临时数据库/临时存储，**不触碰真实数据**）
- 前端：`cd web && npm test`（vitest：纯函数 / composable / store）
- 移动端：`cd mobile && npm test`（jest-expo + @testing-library/react-native；含**裸字符串检查**——遍历渲染树断言非 `<Text>` 节点下没有字符串/数字，专门防 `Text strings must be rendered within a <Text> component` 这类只有原生 RN 才会拦、Web 预览与普通渲染测试都发现不了的白屏；改页面后请补一条渲染用例）
- 前端 lint：`cd web && npm run lint`（ESLint flat config + Prettier 协同；要求 0 error，`no-explicit-any` 等告警渐进清理）
- 后端格式：Python 侧 `ruff format` 同样**只对本次新增/改动的文件**跑，且**不要把它和功能改动混进同一个提交**（`ruff format` 会把文件里的历史代码一起重排，实测一次就让 `routers/tags.py`、`tag_query.py` 的 diff 里混进几十行无关改动，脏化 `git blame/bisect`）。要格式化就单独提一个 `style:` 提交
- 前端格式：`npm run format:check` 只检查**本次改动**的前端文件（`scripts/check-web-format.mjs`；全库检查会因 45 个历史未格式化文件永远为红）、`npm run format <文件>` 增量格式化；**禁止**全库 `prettier --write`（会产生数千行无关 diff 污染 git 历史），只格式化本次改动的文件。提交时 lint-staged 已对暂存的前端文件自动 `prettier --write`
- 一键门禁：`bash scripts/test.sh`（后端 pytest+覆盖率棘轮/ruff/迁移链自检/库漂移 + **脚本规范（只读连接/argparse）** + 前端 vue-tsc/vitest/eslint/死代码基线/格式，共 **12 项**，与 `.github/workflows/ci.yml` 对齐）
- 端到端冒烟：`cd web && npm run test:e2e`（Playwright，11 条、约 50 秒；**需后端在跑**——Playwright 只自动起前端 dev server，开跑前 `web/e2e/global-setup.ts` 会预检 `/api/health` 并给出启动命令；串行执行，产物在 `web/test-results/`）
- 测试依赖：`pip install -r backend/requirements-dev.txt`（后端）；vitest 等已含在 `web/package.json` devDependencies
- **约定**：修改核心链路（软删除/垃圾桶/内容去重/破坏性接口认证/人物模块）或新增破坏性接口时，须在对应 `backend/tests/` 或 `web/src/**/__tests__/` 补充用例并跑通
- 误将测试文件写入真实存储目录时，用 `python scripts/clean_test_files.py --delete` 清理（判定：DB 无记录 + 测试时段 + 纯色小图）

## 移动端构建（Android APK）

手机没有 Google Play → 装不了 Expo Go → **只能自建 APK**。下面这些不是建议，是实测撞出来的固定动作（2026-10-23）。

**工具链（一次性，本机已装）**

| 组件 | 位置 |
|---|---|
| JDK 17 | `C:\Users\Administrator\jdk17\jdk-17.0.20.1+1`（系统默认 java 是 25，Gradle 8.10 不认，**必须显式 `JAVA_HOME`**） |
| Android SDK | `C:\Users\Administrator\AndroidSdk`（platform-tools / platforms;android-35 / build-tools 35 / NDK 26.1 / CMake 3.22） |

**必须在短真实路径构建**

项目在 `...\Desktop\Claude Code\...`（带空格），CMake 会把源路径压成 `Claude_Code`，react-native-reanimated 的 C++ 对象路径超 Windows MAX_PATH → ninja `mkdir` 失败。`mklink /J` 和 `subst` 都会被 Gradle/CMake 解回真实路径（都试过，无效）——只能**真实复制**到短路径构建：

```bash
robocopy "<repo>\mobile" "C:\mb" /MIR
cd C:\mb\android
set JAVA_HOME=C:\Users\Administrator\jdk17\jdk-17.0.20.1+1
set ANDROID_HOME=C:\Users\Administrator\AndroidSdk
.\gradlew assembleRelease
# 产物：C:\mb\android\app\build\outputs\apk\release\app-release.apk
```

**版本号**：`android/` 是 prebuild 生成物且已 gitignore，`versionCode`/`versionName` 直接改 `C:\mb\android\app\build.gradle`，且必须用**正则**改——`/MIR` 会用仓库里的旧 `build.gradle` 覆盖副本，按字符串替换会静默失配（踩过：打的包版本号没变）。

**prebuild 重跑后要补的两行**（`android/` 会被重置）

- `android.kotlinVersion=1.9.24` —— RN 0.76 带的是 Kotlin 1.9.24，而 `android/build.gradle` 的 ext 默认 1.9.25，会让 expo-modules-core 报 Compose 1.5.15 与编译器不匹配
- `android.usesCleartextTraffic: true`（写在 `app.json` 的 `expo-build-properties` 里，不需手改 gradle）

**提速配方（实测 6m37s → 31s；无改动重跑 9s）**

| 做法 | 收益 | 说明 |
|---|---|---|
| **不要加 `--no-daemon`** | 最大 | 冷启 JVM + 重新配置要 2~3 分钟，daemon 复用后配置阶段几秒 |
| **纯 JS 改动不要清** `android/.cxx` / `build/` | 最大 | 清一次就全量重编 reanimated、expo-modules 的 C++（几分钟）；只有动原生依赖才需要清 |
| `reactNativeArchitectures=arm64-v8a` | 中 | 手机是 arm64，编 4 个 ABI 纯浪费（仓库侧 `gradle.properties` 已配） |
| `org.gradle.caching=true` + `jvmargs -Xmx4096m` | 中 | 任务产物复用；2G heap 偏小（已配） |
| **Defender 排除** `C:\mb`、`~/.gradle`、`AndroidSdk`、仓库目录 | 大 | 实时扫描极拖慢（62s → 31s）；`Add-MpPreference -ExclusionPath` 需管理员（已配） |
| 不要开 `--configuration-cache` | —— | Expo/RN 插件有 45 处不兼容，直接 BUILD FAILED（试过） |

**两个环境陷阱**

- Gradle 依赖走本机代理（clash 7890）：`android/gradle.properties` 里 `systemProp.http(s).proxyHost/Port`；代理没开就删掉这几行（依赖已缓存时不影响增量构建）
- **npm 不要设代理**：它走 npmmirror，设了必 `ECONNREFUSED`

**日常改 UI 的更快路子（未落地）**：装一次 debug APK + `npx expo start --host lan`，手机同网即可 Fast Refresh，改 JS 秒级生效、完全不用打包。

**增量同步不要用 `/MIR`**：`/MIR` 会删掉 `C:\mb\android\build`、`.cxx`，等于每次全量重编原生（几分钟）。日常改 JS 用 `/E`（只覆盖不删除），保留构建缓存后一次 **30~40 秒**；只有依赖大改/换 prebuild 产物时才 `/MIR`。

**图标必须用 `mobile/components/Icon.tsx`，不要写 `<Ionicons>`**（2026-10-24 真机踩坑）

- 现象：真机上**所有图标**（tab、按钮、爱心、返回箭头）全部空白，且没有任何报错日志
- 根因两层：① `@expo/vector-icons` 在 Android 用**小写文件名**做 fontFamily（`'ionicons'`），字体必须内嵌为 `assets/fonts/ionicons.ttf` 才能被原生字体表找到；② 更致命的是组件挂载前先判 `Font.isLoaded('ionicons')`，而 expo-font 只把**自己 loadAsync 过的**字体算已加载，内嵌字体不算 → 判假 → 组件直接渲染空 `<Text />`
- 结论：字体已由 `app.json` 的 `expo-font` plugin 内嵌（`./assets/fonts/Ionicons.ttf`，探针验证 `fontFamily: 'Ionicons'` 可渲染），`Icon.tsx` 直接用该 family 画码点，绕过那层判断；**不要再改回 `<Ionicons>`**，也不要在 `_layout` 里用 `useFonts` 做字体门（字体加载失败会让整页停在白屏底板）
- 教训：这类问题**只有真机能复现**（Jest / react-native-web 都不校验），排查时用 `adb logcat` + 探针（同一屏并排渲染几种写法对比）最快

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
