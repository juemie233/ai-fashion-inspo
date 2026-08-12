# AI 穿搭灵感库

专为个人打造的 AI 穿搭灵感管理工具，通过自动化采集与视觉识别，将碎片化的穿搭内容转化为可智能检索的专属素材资产。

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.12 + FastAPI + SQLAlchemy async + SQLite |
| Web 前端 | Vue 3 + Vite + TypeScript + Pinia + Naive UI |
| 移动端 | React Native (Expo) + Zustand |
| 浏览器插件 | Chrome Extension Manifest V3 |
| AI 推理 | Ollama + MiniCPM-V:8b（本地 GPU） |
| 采集引擎 | Playwright + CDP 连接真实 Chrome（零检测采集） |

## 功能概览

| 模块 | 功能 |
|------|------|
| **灵感库** | 瀑布流浏览、收藏、删除、无限滚动加载 |
| **高级搜索** | 多维标签筛选（AND/OR 组合）、排除标签、实时搜索 |
| **上传素材** | 单张/批量上传、文件夹导入、自动生成缩略图 |
| **素材详情** | 大图预览、标签展示、相似素材推荐 |
| **采集管理** | 小红书/抖音 CDP 采集、Cookie 持久化、失败重试、验证码恢复、成功率统计 |
| **标签管理** | 浏览/编辑/合并/批量操作、相似标签扫描、导入导出、拖拽分类、标签详情 |
| **AI 模型管理** | 模型列表/下载/切换、批量分析、历史分页、分析详情、参数调优、数据重置 |
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
ollama pull minicpm-v:8b
```

### 3. 启动服务

```bash
# 后端 (端口 8000)
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Web 前端 (端口 5173)
cd web
npm run dev
```

浏览器打开 `http://localhost:5173`

### 4. 启动采集引擎（可选）

采集引擎通过 CDP 连接用户真实 Chrome 实现零检测采集：

```bash
# 以调试模式启动 Chrome（与日常使用的 Chrome 隔离）
"C:/Users/Administrator/AppData/Local/Google/Chrome/Application/chrome.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="C:/Users/Administrator/Desktop/chrome-scraper-profile"
```

在打开的 Chrome 窗口中登录小红书，然后在采集管理页面创建任务即可自动搜索、下载、入库。

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
│   │   ├── models/               # 数据模型
│   │   │   ├── inspiration.py    # 灵感素材 + AI分析日志
│   │   │   ├── tag.py            # 标签（含 source 来源标识）
│   │   │   └── scraper.py        # 采集任务
│   │   ├── schemas/              # Pydantic 请求/响应
│   │   ├── routers/              # API 路由
│   │   │   ├── inspirations.py   # 素材 CRUD
│   │   │   ├── tags.py           # 标签管理 + 批量/统计/扫描/导入导出
│   │   │   ├── search.py         # 多维度搜索 + 相似素材
│   │   │   ├── ai.py             # AI 分析 + 模型管理 + 数据重置
│   │   │   ├── scraper.py        # 采集管理
│   │   │   ├── files.py          # 静态文件
│   │   │   └── ws.py             # WebSocket
│   │   ├── services/             # 业务逻辑
│   │   │   ├── ai_service.py     # Ollama 视觉分析 + 标签提取 + 颜色映射
│   │   │   ├── file_service.py   # 文件管理
│   │   │   ├── tag_service.py    # 标签 CRUD + 合并 + 预设导入 + 相似度
│   │   │   ├── embedding_service.py  # 向量嵌入
│   │   │   └── scraper_service.py    # 采集编排
│   │   ├── scrapers/             # 平台爬虫
│   │   │   ├── base.py           # 抽象基类
│   │   │   ├── xiaohongshu.py    # 小红书
│   │   │   └── douyin.py         # 抖音
│   │   └── utils/                # 工具函数
│   │       ├── auth.py           # API Key 认证中间件
│   │       ├── image_utils.py    # 缩略图/颜色提取
│   │       └── tag_normalizer.py # 标签标准化 + 同义词映射
│   ├── scripts/                  # 维护脚本
│   │   ├── run_scraper.py         # CDP 采集执行脚本
│   │   ├── cleanup_tags.py        # 数据库脏标签清洗
│   │   ├── validate_tags.py       # 标签合法性校验
│   │   └── diagnose_scraper.py    # 采集诊断工具
│   └── storage/                  # 本地文件存储 (gitignore)
│       ├── images/
│       ├── thumbnails/
│       └── videos/
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
│   │   │   └── ui.ts             # UI 状态
│   │   ├── views/                # 页面组件
│   │   │   ├── HomeView.vue      # 首页画廊
│   │   │   ├── UploadView.vue    # 上传素材
│   │   │   ├── SearchView.vue    # 高级搜索
│   │   │   ├── DetailView.vue    # 素材详情
│   │   │   ├── ScraperView.vue   # 采集管理
│   │   │   ├── TagManageView.vue # 标签管理（全功能）
│   │   │   └── ModelManageView.vue # AI 模型管理（全功能）
│   │   ├── components/           # 通用组件
│   │   │   ├── layout/AppLayout.vue
│   │   │   ├── inspiration/      # MasonryGrid, InspirationCard, ImageLightbox
│   │   │   └── search/           # SearchBar, TagFilter
│   │   └── composables/          # Vue composables
│   │       ├── useWebSocket.ts
│   │       └── useInfiniteScroll.ts
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
│  │               FastAPI Backend (:8000)                 │    │
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
|----|------|----------|
| `inspirations` | 灵感素材 | id, source_type, file_path, media_type, dominant_colors |
| `tags` | 标签 | id, name, category, source (seed/ai_generated/manual) |
| `inspiration_tags` | 素材-标签关联 | inspiration_id, tag_id, confidence |
| `ai_analysis_log` | AI 分析日志 | inspiration_id, model_name, processing_time_ms, error |
| `scraper_tasks` | 采集任务 | platform, status, items_found/added |

### 标签类别体系

| 类别 | 示例 | 说明 |
|------|------|------|
| `style` | JK制服, 汉服, Y2K, 法式, 新中式 | 风格体系 |
| `item_type` | 百褶裙, 过膝袜, 西装外套, 马丁靴 | 单品类型 |
| `color` | 白色, 海军蓝, 酒红, 格纹 | 颜色 |
| `body_part` | 过膝, 高腰, V领, 拖地 | 穿着方式 |
| `fit` | 宽松, 修身, Oversized, 直筒 | 版型 |
| `occasion` | 日常, 通勤, 约会, 校园 | 场合 |
| `season` | 春季, 夏季, 秋季, 冬季 | 季节 |
| `attribute` | 露脸, 全身, 对镜自拍, 叠穿 | 图片属性 |

### 标签来源标识

| source | 含义 | 颜色标记 |
|--------|------|:---:|
| `seed` | 预设标签（系统初始化导入） | 灰色 |
| `ai_generated` | AI 分析自动提取 | 紫色 |
| `manual` | 用户手动创建 / 导入 | 蓝色 |

## API 概览

### 素材管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/inspirations` | 素材列表（分页） |
| `POST` | `/api/inspirations` | 上传素材 |
| `GET` | `/api/inspirations/{id}` | 素材详情 |
| `PATCH` | `/api/inspirations/{id}` | 更新素材 |
| `DELETE` | `/api/inspirations/{id}` | 删除素材 |

### 搜索

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/search` | 多维度标签搜索 |
| `GET` | `/api/search/similar/{id}` | 相似素材推荐 |

### 标签管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/tags` | 标签列表（按类别分组） |
| `GET` | `/api/tags/popular` | 热门标签 Top 50 |
| `GET` | `/api/tags/stats` | 标签统计（总数/未使用/来源分布） |
| `GET` | `/api/tags/duplicates` | 相似标签扫描 |
| `GET` | `/api/tags/export` | 导出全部标签 JSON |
| `GET` | `/api/tags/suggestions/{name}` | 创建时去重建议 |
| `GET` | `/api/tags/{id}/inspirations` | 使用该标签的素材 |
| `POST` | `/api/tags` | 创建标签 |
| `POST` | `/api/tags/import` | 批量导入标签 |
| `POST` | `/api/tags/merge` | 合并标签 |
| `POST` | `/api/tags/batch-delete` | 批量删除标签 |
| `PATCH` | `/api/tags/{id}` | 编辑标签（重命名/改类别） |
| `DELETE` | `/api/tags/{id}` | 删除标签 |
| `DELETE` | `/api/tags/unused` | 删除所有未使用标签 |

### AI 分析

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/ai/status` | AI 服务状态检查 |
| `GET` | `/api/ai/models` | 已安装模型列表 |
| `POST` | `/api/ai/models/pull` | 下载模型（SSE 进度） |
| `PUT` | `/api/ai/models/active` | 切换活跃模型 |
| `DELETE` | `/api/ai/models/{name}` | 删除模型 |
| `POST` | `/api/ai/analyze/{id}` | 触发单个分析 |
| `POST` | `/api/ai/batch-analyze` | 批量分析 |
| `POST` | `/api/ai/retry/{id}` | 重试失败分析 |
| `GET` | `/api/ai/queue` | 分析队列统计 |
| `GET` | `/api/ai/unanalyzed-ids` | 未分析素材 ID 列表 |
| `GET` | `/api/ai/active-analyses` | 正在分析的任务 |
| `GET` | `/api/ai/history` | 分析历史（分页/筛选） |
| `GET` | `/api/ai/history/{id}` | 分析详情（含标签） |
| `DELETE` | `/api/ai/history/{id}` | 删除单条日志 |
| `DELETE` | `/api/ai/history/failed/all` | 删除所有失败日志 |

### AI 参数调优

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/ai/settings` | 获取分析参数 |
| `PUT` | `/api/ai/settings` | 更新参数（可选持久化） |
| `GET` | `/api/ai/sampling-params` | 获取采样参数 |
| `PUT` | `/api/ai/sampling-params` | 更新采样参数（可选持久化） |
| `POST` | `/api/ai/retry-all-failed` | 重试所有失败（仅图片） |
| `DELETE` | `/api/ai/reset?confirm=yes` | 重置所有数据+文件 |

> **注：** 视频文件暂不参与 AI 分析。WebP 图片会自动转为 JPEG 以兼容 MiniCPM-V 模型。

### 采集管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/scraper/tasks` | 创建采集任务（支持 CDP 端口） |
| `GET` | `/api/scraper/tasks` | 采集任务列表 |
| `GET` | `/api/scraper/tasks/{id}` | 任务详情 |
| `GET` | `/api/scraper/sources` | 可用采集源及状态 |
| `POST` | `/api/scraper/tasks/retry-failed` | 重试所有失败任务 |
| `DELETE` | `/api/scraper/tasks` | 清空所有采集任务 |
| `DELETE` | `/api/scraper/tasks/{id}` | 取消/删除单个任务 |

### 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/files/{path}` | 静态文件访问 |
| `WS` | `/ws` | WebSocket 实时推送 |

## 环境要求

| 软件 | 用途 | 必须？ |
|------|------|:---:|
| Python 3.12+ | 后端 | ✅ |
| Node.js 20+ | Web + Mobile 前端 | ✅ |
| Ollama | AI 视觉推理 | ✅ |
| MiniCPM-V:8b | 穿搭标签识别 | ✅ |
| ffmpeg | 视频关键帧提取 | ❌ 尚未使用 |
| Chrome + CDP | 采集引擎（连接用户真实浏览器） | ❌ 可选 |
| Playwright | 采集引擎驱动 | ❌ 可选 |
