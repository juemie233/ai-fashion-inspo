# AI 穿搭素材库

专为个人打造的 AI 穿搭素材管理工具，通过自动化采集与视觉识别，将碎片化的穿搭内容转化为可智能检索的专属素材资产。

## 前置条件

以下软件和环境为 **必须安装**，否则核心功能无法运行：

| 软件 | 版本要求 | 用途 | 安装指引 |
| ------ | ---------- | ------ | ---------- |
| Python | 3.12+ | 后端运行时 | [python.org](https://www.python.org/downloads/) |
| Node.js | 20+ | Web 前端构建 | [nodejs.org](https://nodejs.org/en/download) |
| Ollama | latest | AI 视觉推理引擎 | [ollama.com](https://ollama.com/download/windows) |
| MiniCPM-V:8b | — | 穿搭标签识别模型 | `ollama pull minicpm-v:8b` |

### 采集引擎附加条件（仅 CDP 模式需要）

| 软件 | 要求 | 用途 |
|------|------|------|
| **Google Chrome** | 最新稳定版 | CDP 零检测采集的宿主浏览器 |
| Playwright | 1.40+ | 浏览器自动化驱动 (`pip install playwright && playwright install chromium`) |

> ⚠️ **重要：必须使用 Google Chrome，不可替代！**
>
> CDP (Chrome DevTools Protocol) 采集依赖 Google Chrome 的原生调试协议。以下浏览器**无法**用于 CDP 采集：
>
> | 浏览器 | 是否可用 | 原因 |
> | -------- | :---: | ------ |
> | **Google Chrome** | ✅ | 完整支持 CDP 协议 |
> | 360 极速浏览器 | ❌ | CDP 协议被阉割，无法正常调用 |
> | Microsoft Edge | ❌ | CDP 实现有差异，部分接口不兼容 |
> | Chromium 开源版 | ⚠️ | 可能可用，未充分测试 |
> | 其他 Chrome 内核衍生版 | ❌ | 多数对 CDP 协议做了裁剪 |
>
> 如果系统中同时安装了 Google Chrome 和其他 Chromium 内核浏览器，请确保：
>
> 1. 启动调试模式前**完全关闭** Google Chrome 的所有窗口
> 2. 不要使用 360 极速浏览器执行 `--remote-debugging-port` 命令
> 3. 可以在采集页点击「测试连接」验证连接的是否为 Google Chrome

### Chrome 路径配置

Chrome 安装路径和设备不同可能不一样。可通过以下方式自定义：

**方式一：环境变量（推荐）**

在 `backend/.env` 中设置：

```bash
# Chrome 浏览器可执行文件路径
CHROME_EXECUTABLE="C:/Program Files/Google/Chrome/Application/chrome.exe"

# 采集专用用户数据目录（与日常 Chrome 隔离，避免冲突）
CHROME_USER_DATA_DIR="C:/Users/Administrator/Desktop/chrome-scraper-profile"

# 调试端口（默认 9222，一般无需修改）
CHROME_DEBUG_PORT=9222
```

**方式二：修改配置文件**

直接编辑 `backend/app/config.py` 中 `Settings` 类的默认值：

```python
chrome_executable: str = "C:/Program Files/Google/Chrome/Application/chrome.exe"
chrome_user_data_dir: str = "C:/Users/Administrator/Desktop/chrome-scraper-profile"
chrome_debug_port: int = 9222
```

> **常见 Chrome 安装路径：**
>
> - Windows 默认：`C:/Program Files/Google/Chrome/Application/chrome.exe`
> - Windows 用户安装：`C:/Users/<用户名>/AppData/Local/Google/Chrome/Application/chrome.exe`
> - macOS：`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`
> - Linux：`/usr/bin/google-chrome`

## 技术栈

| 层级 | 技术 |
| ------ | ------ |
| 后端 | Python 3.12 + FastAPI + SQLAlchemy async + SQLite |
| Web 前端 | Vue 3 + Vite + TypeScript + Pinia + Naive UI |
| 移动端 | React Native (Expo) + Zustand |
| 浏览器插件 | Chrome Extension Manifest V3 |
| AI 推理 | Ollama + MiniCPM-V:8b（本地 GPU） |
| 采集引擎 | Playwright + CDP 连接真实 Chrome（零检测采集） |

## 功能概览

| 模块 | 功能 |
| ------ | ------ |
| **素材库** | 瀑布流浏览、多维筛选（来源/媒体/状态）、排序（含随机）、密度调节、分页加载、浏览模式/密度持久化 |
| **高级搜索** | 关键词搜索、标签筛选(AND/OR)、共现推荐、高级筛选(来源/媒体/日期)、排序(匹配优先)、搜索历史、分页、密度调节、语义搜索（文本）、以图搜图（图片上传）、`/` 聚焦与 Esc 退出、复制搜索链接、筛选状态持久化 |
| **上传素材** | 拖拽/粘贴/URL导入、预览队列（视频可预览）、上传进度与速度、快速标签、元数据预设、去重检测、文件夹批量、队列管理（清空二次确认）、偏好设置持久化、500 上限校验 |
| **素材详情** | 大图预览（灯箱左右切换/缩放）、标签展示、穿搭大标签（手动选择/新建 + AI 建议一键入库）、相似素材推荐（可收藏/删除）、重新分析、下载原图、复制原始链接、标签点击跳搜索 |
| **采集管理** | CDP零检测采集、Cookie管理(状态/导入)、任务筛选/排序/取消、采集日志查看、内容MD5去重、URL墓碑表防重复、成功率统计、任务状态/表单草稿持久化 |
| **标签管理** | 分组浏览/搜索/筛选、置顶 + 自定义拖拽排序、别名归一化（AI 识别同义词自动归并）、批量改类别/重命名/合并/删除（二次确认）、重复扫描、拖拽改类、批量打标、标签备注、共现关系图 + 使用趋势、导入导出、素材关联预览、分栏宽度持久化 |
| **AI 模型管理** | 模型列表/下载/切换、GPU 显存监控、批量分析（异步任务队列）、历史分页、多选批量操作、分析结果对比、队列可视化、参数调优、数据重置、质量审核（合格/不合格二分类 + 重新审核，异步）、快捷键（回车下载/Ctrl+S 保存） |
| **浏览器插件** | 一键提取网页穿搭图片 |

## 快速启动

### 1. 安装依赖

```bash
# Python 后端
cd backend
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# Node.js 前端
cd ../web
npm install
```

### 2. 安装 AI 模型

```bash
# 安装 Ollama（从 ollama.com 下载 Windows 版）

# 推荐：Qwen3-VL-8B-Instruct（官方维护、256K 上下文、32 语言 OCR）
ollama pull qwen3-vl:8b-instruct

# 备选：MiniCPM-V:8b（体量更小、速度更快）
ollama pull minicpm-v:8b
```

安装后在 AI 模型管理页切换活跃模型即可。

### 3. 启动服务

```bash
# 后端 (默认端口 18888，可通过 PORT 环境变量或 .env 修改)
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 18888 --reload

# Web 前端 (默认端口 17777，可通过 VITE_FRONTEND_PORT 环境变量修改)
cd web
npm run dev

# 任务队列 worker（处理「批量分析」异步任务，需单独起一个终端）
cd ../backend
python -m app.worker
```

浏览器打开 `http://localhost:17777`

> 一键重启：`bash scripts/restart.sh` 会自动停旧进程并同时拉起后端 + 前端 + worker + agentmemory（若已安装），并校验就绪。

**自定义端口：**

```bash
# 后端 .env
PORT=18888                   # 后端监听端口

# 前端 .env (web/.env)
VITE_FRONTEND_PORT=17777     # 前端开发服务器端口
VITE_BACKEND_URL=http://localhost:18888  # 后端 API 地址
```

### 4. 启动采集引擎（可选）

采集引擎通过 CDP 连接用户真实 Chrome 实现零检测采集。

> **前提：** 必须先安装 Google Chrome 并完成 [Chrome 路径配置](#chrome-路径配置)。

**启动调试 Chrome：**

根据你在 `.env` 中配置的路径，在命令行中执行（端口和目录需与配置一致）：

```bash
"C:/Program Files/Google/Chrome/Application/chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:/Users/Administrator/Desktop/chrome-scraper-profile"
```

> 如果 Chrome 提示"无法在此目录下创建用户数据"，请先关闭所有已打开的 Chrome 窗口再试。

**在 Web 界面创建采集任务：**

1. 在调试 Chrome 窗口中登录小红书（`xiaohongshu.com`）
2. 打开采集页面，确认 CDP 模式已开启
3. 点击「测试连接」按钮，确认显示"已连接"
4. 输入关键词，点击「开始采集」

### 5. 安装浏览器插件

1. Chrome 打开 `chrome://extensions`
2. 开启「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择 `browser-extension/` 目录

### 5. 启动移动端（可选）

```bash
cd mobile
npx expo start
```

## 项目结构

```
fashion-inspo/
├── CLAUDE.md                     # 编码规范与项目标准
├── README.md                     # 本文件
├── TODO.md                       # 待完成功能清单
│
├── backend/                      # Python 后端
│   ├── .env                      # 环境变量
│   ├── requirements.txt          # Python 依赖
│   ├── app/
│   │   ├── main.py               # FastAPI 入口
│   │   ├── config.py             # 配置管理
│   │   ├── database.py           # 数据库引擎
│   │   ├── worker.py             # 任务队列 worker（python -m app.worker）
│   │   ├── models/               # 数据模型
│   │   │   ├── inspiration.py    # 穿搭素材 + AI分析日志
│   │   │   ├── tag.py            # 标签 + 别名（含 source 来源标识）
│   │   │   ├── scraper.py        # 采集任务
│   │   │   └── task.py           # 异步任务队列
│   │   ├── schemas/              # Pydantic 请求/响应
│   │   ├── routers/              # API 路由
│   │   │   ├── inspirations.py   # 素材 CRUD
│   │   │   ├── tags.py           # 标签管理 + 批量/统计/扫描/排序/别名/共现/导入导出
│   │   │   ├── search.py         # 多维度搜索 + 相似素材
│   │   │   ├── ai.py             # AI 路由聚合（拆分见 ai_*.py）
│   │   │   ├── ai_shared.py      # AI 共享状态 + 后台任务
│   │   │   ├── ai_models.py      # 模型管理 + GPU + 模型统计
│   │   │   ├── ai_analysis.py    # 分析 + 队列 + 历史 + 对比
│   │   │   ├── ai_quality.py     # 质量审核
│   │   │   ├── ai_settings.py    # Prompt + 参数调优
│   │   │   ├── ai_dashboard.py   # 分析质量仪表盘
│   │   │   ├── ai_outfit.py      # 穿搭大标签建议
│   │   │   ├── ai_reset.py       # 数据重置
│   │   │   ├── scraper.py        # 采集管理
│   │   │   ├── admin.py          # 管理后台（统计、去重、完整性检查）
│   │   │   ├── tasks.py          # 任务队列（列表/详情/取消）
│   │   │   ├── files.py          # 静态文件
│   │   │   └── ws.py             # WebSocket
│   │   ├── services/             # 业务逻辑
│   │   │   ├── ai_service.py     # AI 编排（分析/审核/大标签总结）
│   │   │   ├── ai_parser.py      # AI 响应解析/修复（畸形处理）
│   │   │   ├── ai_tag_saver.py   # 标签标准化/保存/关联
│   │   │   ├── file_service.py   # 文件管理
│   │   │   ├── tag_service.py    # 标签 CRUD + 合并 + 预设导入 + 相似度
│   │   │   ├── embedding_service.py  # 向量嵌入（文本/图像）
│   │   │   ├── vector_service.py     # 向量回填 / 相似度混合排序
│   │   │   ├── vector_store.py       # LanceDB 读写
│   │   │   ├── task_runner.py        # 异步任务执行器（worker 分发）
│   │   │   └── scraper_service.py    # 采集编排
│   │   ├── scrapers/             # 平台爬虫
│   │   │   ├── base.py           # 抽象基类
│   │   │   ├── xiaohongshu.py    # 小红书
│   │   │   └── douyin.py         # 抖音
│   │   └── utils/                # 工具函数
│   │       ├── auth.py           # API Key 认证中间件
│   │       ├── file_hash.py      # 文件 MD5 哈希
│   │       ├── image_utils.py    # 缩略图/颜色提取
│   │       └── tag_normalizer.py # 标签标准化 + 同义词/别名映射
│   ├── scripts/                  # 维护脚本
│   │   ├── run_scraper.py         # CDP 采集执行脚本
│   │   ├── cleanup_tags.py        # 数据库脏标签清洗
│   │   ├── validate_tags.py       # 标签合法性校验
│   │   └── diagnose_scraper.py    # 采集诊断工具
│   └── storage/                  # 本地文件存储 (gitignore)
│       ├── images/
│       ├── thumbnails/
│       ├── videos/
│       └── lancedb/              # 向量库（文本/图像向量）
│
├── web/                          # Vue 3 Web 前端
│   ├── src/
│   │   ├── main.ts               # 应用入口
│   │   ├── App.vue               # 根组件
│   │   ├── router/index.ts       # 路由配置
│   │   ├── api/                  # API 客户端
│   │   │   ├── client.ts         # Axios 实例
│   │   │   ├── inspirations.ts   # 素材 API
│   │   │   ├── tags.ts           # 标签 API（完整）
│   │   │   └── search.ts         # 搜索 API
│   │   ├── stores/               # Pinia 状态
│   │   │   ├── inspirations.ts   # 素材状态
│   │   │   ├── tags.ts           # 标签筛选状态
│   │   │   ├── aiModels.ts       # AI 模型共享状态
│   │   │   └── ui.ts             # UI 状态
│   │   ├── views/                # 页面组件
│   │   │   ├── HomeView.vue      # 首页画廊
│   │   │   ├── UploadView.vue    # 上传素材
│   │   │   ├── SearchView.vue    # 高级搜索
│   │   │   ├── DetailView.vue    # 素材详情
│   │   │   ├── ScraperView.vue   # 采集管理
│   │   │   ├── TagManageView.vue # 标签管理（全功能）
│   │   │   ├── ModelManageView.vue # AI 模型管理（全功能）
│   │   │   └── AdminView.vue     # 管理后台（统计、去重、完整性检查）
│   │   ├── components/           # 通用组件
│   │   │   ├── layout/AppLayout.vue
│   │   │   ├── inspiration/      # MasonryGrid, InspirationCard, ImageLightbox
│   │   │   ├── model/            # ModelListPanel, AnalysisPanel, SettingsPanel, QualityPanel, ReviewPanel
│   │   │   └── search/           # SearchBar, TagFilter
│   │   ├── utils/                # 工具函数
│   │   │   └── format.ts         # 字节/耗时/日期格式化
│   │   └── composables/          # Vue composables
│   │       ├── useWebSocket.ts
│   │       ├── useInfiniteScroll.ts
│   │       └── useNotification.ts  # 浏览器桌面通知
│   ├── package.json
│   └── vite.config.ts
│
├── mobile/                       # React Native 移动端
│   ├── app/
│   │   ├── _layout.tsx           # 根布局
│   │   ├── (tabs)/               # Tab 页面
│   │   │   ├── index.tsx         # 画廊
│   │   │   ├── search.tsx        # 搜索
│   │   │   └── capture.tsx       # 拍照上传
│   │   └── detail/[id].tsx       # 详情
│   ├── hooks/useInspirations.ts  # Zustand 状态
│   ├── services/api.ts           # API 客户端
│   └── app.json                  # Expo 配置
│
├── browser-extension/            # Chrome 浏览器插件
│   ├── manifest.json
│   ├── background/service-worker.js
│   ├── content-scripts/extract-images.js
│   ├── popup/
│   │   ├── popup.html
│   │   ├── popup.js
│   │   └── popup.css
│   └── icons/
│
├── shared/types/                 # 前后端共享类型
│   ├── inspiration.ts
│   ├── tag.ts
│   └── api.ts
│
└── scripts/                      # 工具脚本
    ├── seed_tags.py              # 预设标签导入
    ├── batch_import.py           # 批量导入本地图片
    ├── backfill_vectors.py       # 存量素材向量回填
    ├── restart.sh                # 一键重启前后端 + worker + agentmemory
    └── generate_icons.py         # 生成插件图标
```

## 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                     Local PC (Windows 11)                     │
│                                                               │
│  ┌───────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │ Vue 3 Web App │  │ Browser Extension│  │ React Native │  │
│  │(Desktop浏览器)  │  │ (一键采集)        │  │ (手机端)     │  │
│  └───────┬───────┘  └────────┬─────────┘  └──────┬───────┘  │
│          │                   │                    │          │
│          └───────────────────┼────────────────────┘          │
│                              │ HTTP/WebSocket                │
│                              ▼                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │               FastAPI Backend (:18888)                │    │
│  │                                                       │    │
│  │  REST API │ WebSocket │ Background Tasks             │    │
│  │  ─────────────────────────────────────────────        │    │
│  │  Scraper Mgr │ AI Service │ File Service            │    │
│  │  Tag Service │ Auth Middleware                       │    │
│  └──────────────────────────┬──────────────────────────┘    │
│                              │                               │
│  ┌──────────────────────────┼──────────────────────────┐    │
│  │   SQLite (元数据)          │  Storage/ (图片文件)      │    │
│  └──────────────────────────┴──────────────────────────┘    │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │          Ollama (GPU: RTX 5060Ti 16GB)               │    │
│  │  MiniCPM-V:8b — 穿搭视觉分析与标签提取                  │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │     Chrome CDP (:9222) — 采集引擎连接真实浏览器         │    │
│  │     小红书/抖音零检测搜索 → 图片自动下载入库             │    │
│  └─────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

## 数据模型

| 表 | 说明 | 关键字段 |
| ---- | ------ | ---------- |
| `inspirations` | 穿搭素材 | id, source_type, file_path, media_type, dominant_colors, quality_status, quality_reason |
| `tags` | 标签 | id, name, category, source (seed/ai_generated/manual), pinned, sort_order, description |
| `tag_aliases` | 标签别名 | id, tag_id, alias — 同义词归一化（AI 识别到别名自动归为主标签） |
| `inspiration_tags` | 素材-标签关联 | inspiration_id, tag_id, confidence |
| `ai_analysis_log` | AI 分析日志 | inspiration_id, model_name, log_type, raw_response, processing_time_ms, error |
| `scraper_tasks` | 采集任务 | platform, status, items_found/added, diagnostics（采集漏斗日志） |
| `scraper_seen_urls` | URL 墓碑表 | source_url (PK), created_at — 删除后防止重复采集 |
| `task_queue` | 异步任务队列 | type（batch_analyze/quality_check/batch_delete/deduplicate）, status（pending/running/success/failed/cancelled）, progress, total/done, result, error, retry_count, next_retry_at |

### 标签类别体系

| 类别 | 示例 | 说明 |
| ------ | ------ | ------ |
| `style` | JK制服, 汉服, Y2K, 法式, 新中式 | 风格体系 |
| `item_type` | 百褶裙, 过膝袜, 西装外套, 马丁靴 | 单品类型 |
| `color` | 白色, 海军蓝, 酒红, 格纹 | 颜色 |
| `body_part` | 过膝, 高腰, V领, 拖地 | 穿着方式 |
| `fit` | 宽松, 修身, Oversized, 直筒 | 版型 |
| `season` | 春季, 夏季, 秋季, 冬季 | 季节 |
| `attribute` | 露脸, 全身, 对镜自拍, 叠穿 | 图片属性 |
| `outfit` | 御姐长腿高跟鞋穿搭, 白色系穿搭, 网球穿搭 | 穿搭大标签（精选层：手动 + AI 总结，宁缺毋滥） |

### 标签来源标识

| source | 含义 | 颜色标记 |
| -------- | ------ | :---: |
| `seed` | 预设标签（系统初始化导入） | 灰色 |
| `ai_generated` | AI 分析自动提取 | 紫色 |
| `manual` | 用户手动创建 / 导入 | 蓝色 |

## API 概览

### 素材管理

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/api/inspirations` | 素材列表（分页） |
| `POST` | `/api/inspirations` | 上传素材 |
| `POST` | `/api/inspirations/from-url` | 从 URL 导入素材 |
| `GET` | `/api/inspirations/{id}` | 素材详情 |
| `PATCH` | `/api/inspirations/{id}` | 更新素材 |
| `DELETE` | `/api/inspirations/{id}` | 删除素材 |
| `POST` | `/api/inspirations/{id}/tags` | 手动给素材关联标签（按名查找/创建，如穿搭大标签） |
| `DELETE` | `/api/inspirations/{id}/tags/{tag_id}` | 解除素材与标签的关联 |

### 搜索

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/api/search` | 多维度搜索（关键词+标签+颜色+日期+来源+媒体） |
| `GET` | `/api/search/similar/{id}` | 相似素材推荐（图像向量+标签加权，视觉/标签/混合来源） |
| `GET` | `/api/search/suggestions?q=` | 标签名自动补全 |
| `GET` | `/api/search/tag-cooccurrence?tag_name=` | 标签共现分析 |
| `POST` | `/api/search/vector` | 语义搜索/以图搜图（multipart：`text` 或 `file` + `top_k`） |
| `GET` | `/api/search/vector/status` | 向量检索能力状态（LanceDB/文本/图像向量/存量数量） |
| `POST` | `/api/search/vector/backfill` | 存量素材向量回填（`mode`=all/text/image，`limit`=条数上限） |

> **向量检索设置：** 文本语义搜索用 Ollama `all-minilm`（零额外依赖）。以图搜图/视觉相似需额外安装 CLIP：
>
> ```bash
> pip install sentence-transformers   # 含 torch，较重
> export HF_ENDPOINT=https://hf-mirror.com   # 国内下载 CLIP 模型用镜像
> python scripts/backfill_vectors.py --mode all   # 存量回填（首次下载 clip-ViT-B-32 约 600MB）
> ```
>
> 未安装 CLIP 时，以图搜图接口返回 503、相似推荐自动退化为纯标签匹配，其余功能不受影响。

### 标签管理

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/api/tags` | 标签列表（按类别分组） |
| `POST` | `/api/tags` | 创建标签 |
| `PATCH` | `/api/tags/{id}` | 编辑标签（重命名/改类别/置顶/排序/备注） |
| `DELETE` | `/api/tags/unused` | 删除所有未使用标签 |
| `POST` | `/api/tags/batch-delete` | 批量删除标签 |
| `POST` | `/api/tags/merge` | 合并标签 |
| `GET` | `/api/tags/suggestions/{name}` | 创建时去重建议 |
| `PATCH` | `/api/tags/batch-category` | 批量修改标签类别 |
| `PATCH` | `/api/tags/batch-rename` | 批量重命名（查找替换） |
| `GET` | `/api/tags/stats` | 标签统计（总数/未使用/来源分布） |
| `GET` | `/api/tags/duplicates` | 相似标签扫描 |
| `GET` | `/api/tags/{id}/inspirations` | 使用该标签的素材 |
| `POST` | `/api/tags/{id}/inspirations/batch-remove` | 批量解除标签与素材关联 |
| `GET` | `/api/tags/export` | 导出全部标签 JSON |
| `POST` | `/api/tags/import` | 批量导入标签 |
| `POST` | `/api/tags/reorder` | 批量更新自定义排序 |
| `GET` | `/api/tags/aliases` | 标签别名列表 |
| `POST` | `/api/tags/{id}/aliases` | 为标签添加别名 |
| `DELETE` | `/api/tags/aliases/{id}` | 删除标签别名 |
| `GET` | `/api/tags/cooccurrence-network` | 标签共现网络（节点 + 加权边） |
| `GET` | `/api/tags/top` | 热门标签排行 |
| `GET` | `/api/tags/{id}/trend` | 标签使用趋势（按日/周/月） |

### AI 分析

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/api/ai/status` | AI 服务状态检查 |
| `GET` | `/api/ai/models` | 已安装模型列表 |
| `POST` | `/api/ai/models/pull` | 下载模型（SSE 进度） |
| `PUT` | `/api/ai/models/active` | 切换活跃模型 |
| `DELETE` | `/api/ai/models/{name}` | 删除模型 |
| `POST` | `/api/ai/analyze/{id}` | 触发单个分析 |
| `POST` | `/api/ai/batch-analyze` | 批量分析（创建异步任务，返回 task_id，由 worker 执行） |
| `POST` | `/api/ai/outfit-tags/suggest` | AI 建议穿搭大标签（只建议不入库） |
| `POST` | `/api/ai/retry/{id}` | 重试失败分析 |
| `GET` | `/api/ai/queue` | 分析队列统计 |
| `GET` | `/api/ai/unanalyzed-ids` | 未分析素材 ID 列表 |
| `GET` | `/api/ai/active-analyses` | 正在分析的任务 |
| `GET` | `/api/ai/history` | 分析历史（分页/筛选） |
| `GET` | `/api/ai/history/{id}` | 分析详情（含标签） |
| `DELETE` | `/api/ai/history/{id}` | 删除单条日志 |
| `DELETE` | `/api/ai/history/failed/all` | 删除所有失败日志 |
| `POST` | `/api/ai/history/batch-delete` | 批量删除分析记录 |
| `POST` | `/api/ai/history/batch-retry` | 批量重试分析 |
| `GET` | `/api/ai/history/model-names` | 历史模型名称列表 |
| `GET` | `/api/ai/gpu-stats` | GPU 显存监控 |
| `POST` | `/api/ai/unload-model` | 卸载模型释放显存 |
| `GET` | `/api/ai/queue/pending` | 排队中素材（含缩略图） |
| `DELETE` | `/api/ai/queue/{id}` | 取消排队任务 |
| `POST` | `/api/ai/queue/pause` | 暂停队列 |
| `POST` | `/api/ai/queue/resume` | 恢复队列 |
| `GET` | `/api/ai/compare/{id}` | 分析结果对比（标签差异+耗时） |
| `GET` | `/api/ai/quality-dashboard` | 分析质量仪表盘（覆盖率/趋势/问题素材） |
| `GET` | `/api/ai/prompt/versions` | Prompt 版本历史 |
| `POST` | `/api/ai/prompt/save-version` | 保存当前 Prompt 为版本 |
| `POST` | `/api/ai/prompt/rollback` | 回滚 Prompt 到指定版本 |

### 质量审核

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `POST` | `/api/ai/quality-check` | 批量审核所有待审核（pending）图片素材（异步任务，返回 `task_id`） |
| `POST` | `/api/ai/quality-recheck` | 重新审核所有已通过（approved）素材：重置为 pending 后用最新标准重判（异步任务，返回 `task_id`） |
| `GET` | `/api/ai/quality-stats` | 质量审核统计（待审核/已通过/已拒绝/通过率） |
| `GET` | `/api/ai/manual-upload-auto-approve` | 获取「手动上传默认免审核」配置 |
| `PUT` | `/api/ai/manual-upload-auto-approve` | 设置「手动上传默认免审核」（`enabled=true/false`，可选持久化到 .env） |
| `DELETE` | `/api/inspirations/quality-rejected` | 批量删除所有已拒绝（rejected）素材 |

> **审核标准：** 判定为「合格」需是能看清整体搭配的完整真人穿搭照片。不合格包括：无人物（平铺图/尺码表/广告/纯文字）、仅单品特写、局部/裁切特写（如只有腿/脚/手臂/领口）、构图裁切过度。

> **手动上传免审核：** 默认开启（配置项 `manual_upload_auto_approve`，对应 .env 的 `MANUAL_UPLOAD_AUTO_APPROVE`）。开启后手动上传的素材直接标记为「已通过」，不进入待审核队列；关闭后恢复为待审核。可在「AI 模型管理 → 质量审核」面板一键切换。

### 任务队列

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/api/tasks` | 任务列表（分页，可按 status/type 筛选） |
| `GET` | `/api/tasks/{id}` | 任务详情（前端进度轮询） |
| `POST` | `/api/tasks/{id}/cancel` | 取消排队中的任务（仅 pending 可取消） |

> 以下重型操作均改造为「数据库驱动的异步任务」：接口秒回 `task_id`，由独立 worker 进程（`python -m app.worker`）串行执行、自动重试（2 次，指数退避）。worker 未启动时任务会一直停留在「排队中」。
>
> | type | 触发接口 | 说明 |
> | ---- | -------- | ---- |
> | `batch_analyze` | `POST /api/ai/batch-analyze` | 批量 AI 分析 |
> | `quality_check` | `POST /api/ai/quality-check` / `quality-recheck` | 批量质量审核 / 重新审核 |
> | `batch_delete` | `POST /api/admin/batch-delete` | 批量删除素材 |
> | `deduplicate` | `POST /api/admin/deduplicate` | 智能去重删除 |

### AI 参数调优

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/api/ai/settings` | 获取分析参数 |
| `PUT` | `/api/ai/settings` | 更新参数（可选持久化） |
| `GET` | `/api/ai/sampling-params` | 获取采样参数 |
| `PUT` | `/api/ai/sampling-params` | 更新采样参数（可选持久化） |
| `POST` | `/api/ai/retry-all-failed` | 重试所有失败（仅图片） |
| `DELETE` | `/api/ai/reset?confirm=yes` | 重置所有数据+文件 |

> **注：** 视频文件暂不参与 AI 分析。WebP 图片会自动转为 JPEG 以兼容 MiniCPM-V 模型。

### 采集管理

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `POST` | `/api/scraper/tasks` | 创建采集任务（CDP 模式下会预检 Chrome 连接） |
| `GET` | `/api/scraper/tasks` | 采集任务列表（最多 20 条） |
| `GET` | `/api/scraper/tasks/{id}` | 任务详情 |
| `GET` | `/api/scraper/sources` | 可用采集源及状态 |
| `GET` | `/api/scraper/cdp-check/{port}` | 检测 Chrome 调试端口是否就绪 |
| `POST` | `/api/scraper/tasks/retry-failed` | 重试所有失败任务 |
| `DELETE` | `/api/scraper/tasks` | 清空所有采集任务 |
| `DELETE` | `/api/scraper/tasks/{id}` | 删除单个任务记录 |
| `POST` | `/api/scraper/tasks/{id}/cancel` | 取消运行中任务（发送SIGTERM） |
| `GET` | `/api/scraper/tasks/{id}/log` | 获取任务日志（最近200行） |
| `GET` | `/api/scraper/cookie-status` | Cookie 文件状态检查 |
| `POST` | `/api/scraper/cookie-import` | 导入平台 Cookie |

### 管理后台

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/api/admin/stats` | 素材总览统计（含墓碑表计数） |
| `GET` | `/api/admin/largest-files` | 最大文件 Top 20 |
| `GET` | `/api/admin/integrity-check` | 数据完整性检查（缺失/孤立文件） |
| `GET` | `/api/admin/duplicates` | 文件哈希重复检测 |
| `GET` | `/api/admin/check-duplicate?hash=` | 上传前去重（MD5 检测） |
| `POST` | `/api/admin/cleanup-orphans` | 清理孤立文件 |
| `POST` | `/api/admin/batch-delete` | 批量删除素材（按ID或条件，异步任务，返回 `task_id`） |
| `POST` | `/api/admin/deduplicate` | 智能去重删除（异步任务，返回 `task_id`） |

### 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/files/{path}` | 静态文件访问 |
| `WS` | `/ws` | WebSocket 实时推送 |

## 环境要求

| 软件 | 用途 | 必须？ |
| ------ | ------ | :---: |
| Python 3.12+ | 后端 | ✅ |
| Node.js 20+ | Web + Mobile 前端 | ✅ |
| Ollama | AI 视觉推理 | ✅ |
| MiniCPM-V:8b | 穿搭标签识别 | ✅ |
| Google Chrome | CDP 采集宿主浏览器 | ⚠️ 采集时必需 |
| Playwright | 采集引擎驱动 | ⚠️ 采集时必需 |
| ffmpeg | 视频关键帧提取 | ❌ 尚未使用 |
