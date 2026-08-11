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
| 采集引擎 | Playwright + playwright-stealth |

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
py -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Web 前端 (端口 5173)
cd web
npm run dev
```

浏览器打开 `http://localhost:5173`

### 4. 安装浏览器插件

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
│
├── backend/                      # Python 后端
│   ├── .env                      # 环境变量
│   ├── requirements.txt          # Python 依赖
│   ├── app/
│   │   ├── main.py               # FastAPI 入口
│   │   ├── config.py             # 配置管理
│   │   ├── database.py           # 数据库引擎
│   │   ├── models/               # 数据模型
│   │   │   ├── inspiration.py    # 灵感素材
│   │   │   ├── tag.py            # 标签
│   │   │   └── scraper.py        # 采集任务
│   │   ├── schemas/              # Pydantic 请求/响应
│   │   ├── routers/              # API 路由
│   │   │   ├── inspirations.py   # 素材 CRUD
│   │   │   ├── tags.py           # 标签管理
│   │   │   ├── search.py         # 多维度搜索
│   │   │   ├── ai.py             # AI 分析
│   │   │   ├── scraper.py        # 采集管理
│   │   │   ├── files.py          # 静态文件
│   │   │   └── ws.py             # WebSocket
│   │   ├── services/             # 业务逻辑
│   │   │   ├── ai_service.py     # Ollama 视觉分析
│   │   │   ├── file_service.py   # 文件管理
│   │   │   ├── tag_service.py    # 标签 CRUD + 合并
│   │   │   ├── embedding_service.py  # 向量嵌入
│   │   │   └── scraper_service.py    # 采集编排
│   │   ├── scrapers/             # 平台爬虫
│   │   │   ├── base.py           # 抽象基类
│   │   │   ├── xiaohongshu.py    # 小红书
│   │   │   └── douyin.py         # 抖音
│   │   └── utils/                # 工具函数
│   │       ├── image_utils.py    # 缩略图/颜色提取
│   │       └── tag_normalizer.py # 标签标准化
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
│   │   │   ├── tags.ts           # 标签 API
│   │   │   └── search.ts         # 搜索 API
│   │   ├── stores/               # Pinia 状态
│   │   │   ├── inspirations.ts   # 素材状态
│   │   │   ├── tags.ts           # 标签状态
│   │   │   └── ui.ts             # UI 状态
│   │   ├── views/                # 页面组件
│   │   │   ├── HomeView.vue      # 首页画廊
│   │   │   ├── UploadView.vue    # 上传素材
│   │   │   ├── SearchView.vue    # 高级搜索
│   │   │   ├── DetailView.vue    # 素材详情
│   │   │   ├── ScraperView.vue   # 采集管理
│   │   │   └── TagManageView.vue # 标签管理
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
└──────────────────────────────────────────────────────────────┘
```

## 数据模型

| 表 | 说明 | 关键字段 |
|----|------|----------|
| `inspirations` | 灵感素材 | id, source_type, file_path, media_type, dominant_colors |
| `tags` | 标签 | id, name, category (8个类别) |
| `inspiration_tags` | 素材-标签关联 | inspiration_id, tag_id, confidence |
| `ai_analysis_log` | AI 分析日志 | inspiration_id, model_name, processing_time_ms |
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

## API 概览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/inspirations` | 上传素材 |
| GET | `/api/inspirations` | 素材列表 |
| GET | `/api/inspirations/:id` | 素材详情 |
| DELETE | `/api/inspirations/:id` | 删除素材 |
| GET | `/api/search` | 多维度搜索 |
| GET | `/api/search/similar/:id` | 以图搜图 |
| GET | `/api/tags` | 标签列表（分组） |
| POST | `/api/tags` | 创建标签 |
| POST | `/api/tags/merge` | 合并标签 |
| GET | `/api/ai/status` | AI 模型状态 |
| POST | `/api/ai/analyze/:id` | 触发 AI 分析 |
| POST | `/api/scraper/tasks` | 创建采集任务 |
| GET | `/api/files/{path}` | 静态文件访问 |
| WS | `/ws` | 实时推送 |

## 环境要求

| 软件 | 用途 | 必须？ |
|------|------|:---:|
| Python 3.12+ | 后端 | ✅ |
| Node.js 20+ | Web + Mobile 前端 | ✅ |
| Ollama | AI 视觉推理 | ✅ |
| MiniCPM-V:8b | 穿搭标签识别 | ✅ |
| ffmpeg | 视频关键帧提取 | ❌ Phase 4+ |
| Playwright Chromium | 网页爬虫 | ❌ Phase 4+ |
