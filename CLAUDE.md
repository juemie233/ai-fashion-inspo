# AI 穿搭灵感库 — 项目标准

## 核心规则

1. **代码标识符**（变量名、函数名、类名、表名、字段名）：使用英文，遵循语言社区惯例
2. **注释和文档**：全部使用中文
3. **用户界面文案**：使用中文
4. **数据内容**（标签名、AI prompt 等）：使用中文

## 技术栈

| 层级 | 技术 | 版本要求 |
|------|------|----------|
| 后端框架 | Python + FastAPI | 3.12+ |
| 数据库 | SQLite + SQLAlchemy (async) | — |
| Web 前端 | Vue 3 + Vite + TypeScript | Node 20+ |
| 移动端 | React Native (Expo) | SDK 52+ |
| AI 推理 | Ollama + Qwen2-VL:7b | 本地 |
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
│       ├── views/         # 页面组件
│       ├── components/    # 通用组件
│       ├── stores/        # Pinia 状态
│       ├── api/           # API 客户端
│       └── composables/   # Vue composables
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
- 软删除：不使用，直接物理删除（个人使用，无需恢复）

## 命名约定

| 场景 | 风格 | 示例 |
|------|------|------|
| Python 模块/包 | snake_case | `file_service.py` |
| Python 类 | PascalCase | `InspirationTag` |
| Python 函数/变量 | snake_case | `get_or_create_tag()` |
| Python 常量 | UPPER_SNAKE_CASE | `SEED_TAGS` |
| TS/Vue 组件 | PascalCase | `InspirationCard.vue` |
| TS 函数/变量 | camelCase | `useInspirations()` |
| TS 类型/接口 | PascalCase | `InspirationOut` |
| 数据库表 | snake_case（复数） | `inspirations` |
| 数据库列 | snake_case | `source_type` |

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
- **小功能/微调不提交**：单行修复、样式微调、文字修改等小改动不要自动提交 git。仅在完成完整功能模块、重要 bug 修复、或用户明确要求时才提交。
- **大改动自动提交**：单次修改或新增代码超过 **200 行**时，自动 `git commit` 并 `git push origin master`。
- 提交前需确认用户意图，不要自动提交。
- **代码审查触发条件**：
  | 条件 | 操作 |
  |------|------|
  | 单次改动 > 500 行 | 自动发起代码审查 |
  | 新模块/新文件 | 建议发起审查 |
  | 涉及安全/认证/数据库重置 | 必须审查 |
  | 核心解析逻辑变更（如 AI 标签提取） | 建议审查 |
  | < 200 行小改动 | 可跳过审查 |
- 提交前需确认用户意图，不要自动提交。
```

## 环境要求

- Windows 11
- Python 3.12+
- Node.js 20+
- Ollama（本地 AI 推理）
- ffmpeg（视频关键帧提取）
- Git Bash（Shell 环境）
