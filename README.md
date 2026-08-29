# AI 穿搭素材库

> **[English](README.en.md) | 中文**（英文文档为翻译产物，中文为源，可用 `python scripts/translate_docs.py` 重新生成）

专为个人打造的 AI 穿搭素材管理工具，通过自动化采集与视觉识别，将碎片化的穿搭内容转化为可智能检索的专属素材资产。

## 前置条件

以下软件和环境为 **必须安装**，否则核心功能无法运行：

| 软件 | 版本要求 | 用途 | 安装指引 |
| ------ | ---------- | ------ | ---------- |
| Python | 3.12+ | 后端运行时 | [python.org](https://www.python.org/downloads/) |
| Node.js | 20+ | Web 前端构建 | [nodejs.org](https://nodejs.org/en/download) |
| Ollama | latest | AI 视觉推理引擎 | [ollama.com](https://ollama.com/download/windows) |
| Qwen3-VL:8B-Instruct | — | 穿搭标签识别模型 | `ollama pull qwen3-vl:8b-instruct` |

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
| Web 前端 | Vue 3 + Vite + TypeScript + Pinia + Arco Design（@arco-design/web-vue，已全量替换 Naive UI） |
| 移动端 | React Native (Expo) + Zustand |
| 浏览器插件 | Chrome Extension Manifest V3 |
| AI 推理 | Ollama + Qwen3-VL:8B-Instruct（本地 GPU） |
| 采集引擎 | Playwright + CDP 连接真实 Chrome（零检测采集） |

## 功能概览

| 模块 | 功能 |
| ------ | ------ |
| **素材库** | 瀑布流浏览、多维筛选（来源/媒体/状态/标签/主色调）、排序（含随机/标签数）、密度调节、分页加载、批量多选操作（收藏/移垃圾桶/加标签/编辑元数据）、浏览模式/密度/每页数量持久化 |
| **高级搜索** | 关键词搜索、标签筛选(AND/OR)、共现推荐、高级筛选(来源/媒体/日期)、排序(匹配优先)、搜索历史、分页、密度调节、语义搜索（文本）、以图搜图（图片上传）、`/` 聚焦与 Esc 退出、复制搜索链接、筛选状态持久化 |
| **上传素材** | 拖拽/粘贴/URL导入、预览队列（视频可预览）、上传进度与速度、快速标签、元数据预设、去重检测、文件夹批量、队列管理（清空二次确认）、偏好设置持久化、500 上限校验 |
| **素材详情** | 大图预览（灯箱左右切换/缩放）、标签展示、穿搭大标签（手动选择/新建 + AI 建议一键入库）、相似素材推荐（可收藏/删除）、重新分析、**视频关键帧条带**（视频素材懒提取缩略图，失败静默隐藏）、下载原图、复制原始链接、标签点击跳搜索、移入垃圾桶（可选择删除原因：质量差/重复/不喜欢/隐私/其他/AI生成）、五星评分（与收藏并列，列表可筛选/排序）、**人脸识别（博主特征库匹配）**（检测并匹配 → 自动关联穿搭博主 / 疑似未知人脸 → 手动指定或解除关联，需先在博主详情页注册人脸） |
| **采集管理** | 小红书 CDP 零检测采集；**抖音 CDP 完整采集**（搜索三层策略——直连搜索页 / 搜索接口响应 / 首页搜索框兜底，反风控入口轮换自适应；搜索与按博主双模式；详情页图集多图 / 视频 / 正文 / 话题标签提取，视频下载入库，素材自动关联博主；登录 / 机器人验证感知，人工解决等待 + 诚实报错；任务结束后自动关闭采集新开的浏览器标签页）、**按博主采集**（选博主 → 进其主页逐篇打开笔记详情页，提取轮播多图/视频/正文 caption/话题标签，视频下载入库，素材自动关联博主）、任务分页/平台与状态筛选/排序、取消/续采（断点）/复制重采、日志查看、漏斗可视化（含跳过原因统计）、结果预览（批量删除/加载更多/跳详情）、Cookie 管理（状态/时效/导入/删除/**真实有效性校验**：探测平台登录态接口，失效在管理页显式标记，新建任务前置拦截已失效 Cookie）、Chrome 生命周期管理、定时采集（计划 CRUD/启停/立即执行）、**话题标签存档**（笔记话题自动入库：全局去重 + 累计出现次数 + 来源追溯，新建/编辑采集任务可从话题库点击复用为关键词）、统计看板（平台分布/每日趋势）、URL 墓碑表 + 内容 MD5 去重、筛选/排序/页签持久化 |
| **标签管理** | 分组浏览/搜索/筛选、置顶 + 自定义拖拽排序、别名归一化（AI 识别同义词自动归并）、批量改类别/重命名/合并/删除（二次确认）、重复扫描、拖拽改类、批量打标、标签备注、共现关系图 + 使用趋势、导入导出、素材关联预览、分栏宽度持久化 |
| **标签高级管理** | 独立路由页（侧边栏「标签高级管理」入口）：**健康度分析**（孤儿/低频/低质命名/疑似重复扫描 + 健康评分 + 一键修复）、**自动聚类**（名称相似 + 共现加成产出候选合并组，人工确认后合并/建别名）、**网络图分析**（Top-N 共现子图 + 社区发现 + 中心度 + 桥接节点高亮，点击节点看趋势）、**批量高级编辑**（正则查找替换/前后缀增删/格式归一化/正则批量合并，dry-run 预览 + 撞名自动合并）、**标签层级树**（parent_id 任意深度树，拖拽移动子树 + 循环检测）、**操作历史与回滚**（全部标签写操作快照，单条回滚 + 冲突检测）、**使用效果分析**（热度升降榜/标签组合/覆盖度/来源分布） |
| **AI 模型管理** | 模型列表/下载/切换、文本嵌入模型管理（标注/一键下载/切换）、GPU 显存监控、批量分析（异步任务队列）、历史分页、多选批量操作、分析结果对比、**多模型 × 多提示词组合批量分析**（组合计划批量执行 + 批次对比视图）、**视频多帧融合分析**（逐帧分析后同名标签按最高置信度融合，帧数可配）、队列可视化、参数调优（按模型隔离 + 默认值恢复 + 清除覆盖）、数据重置、质量审核（合格/不合格二分类 + 重新审核，异步）、负样本初筛器（状态/指标/训练/回滚）、快捷键（回车下载/Ctrl+S 保存） |
| **素材管理** | 按小菜单分区的管理后台（子页面状态经 URL 持久化，刷新保持）：概览（统计/分布/最大文件）、疑似 AI 复核（勾选后批量删除或重新标记为非 AI，悬停卡片点 👁 浏览详情）、批量清理（无标签/分析失败）、数据完整性检查、重复文件检测与去重、近似重复检测（感知哈希分组 + 全库随机抽样 + 并排预览 + 人工确认删除，哈希缓存渐进补齐后秒级扫描）、向量化回填（一键补全缺失图像向量）、垃圾桶（软删除素材的恢复/彻底删除/清空，默认不自动回收）、数据洞察（CSV 导出/新增趋势图/人物频次排行/操作审计日志）、**手机图剪裁**（扫描手动上传竖屏截图 → 人工勾选确认 → 一键裁剪状态栏/底部导航栏区域：auto 黑边自动检测 / 固定比例双模式 + 截图特征置信度分级；原图自动备份 + 向量回填；跳过素材支持在素材库中精确定位跳转；裁剪结果与库中素材内容重复时左右对比展示，由用户决定保留哪一张——可物理删除重复素材） |
| **人物管理** | **穿搭博主 / 职业模特双 Tab 独立管理**（两类已物理拆分为独立表与 API，业务逻辑各自演进）：列表（名称搜索/平台筛选/排序）、新建/编辑/删除（仅无关联素材时可删）、热门排行、风格画像（高频标签/类别分布/趋势）、**IP 属地统计**（按属地聚合博主数/素材数）、素材关联（详情页按博主/模特分区块搜索添加/解除）、**博主 CSV 导入**（按小红书号 upsert）、**模特照片组**（选择文件夹整组导入到选定模特、照片组浏览/灯箱/删除、组内 SHA-256 去重）、**博主 人脸特征注册**（上传正脸照片 与/或 从已关联素材中选图，两种来源合计 1~5 张，注册/重新注册，素材人脸自动匹配依赖此特征库；职业模特无此人脸能力）、**人物组（博主跨平台绑定）**（同一现实人物在抖音/小红书各有账号时绑定为同一人——如小红书「Fox_」与「多多」：列表同组折叠为一条主账号（素材数最多者，可手动指定默认展示位）并带多平台徽标，展开可见组内各账号及其素材，详情页可绑定/解绑/切主，账号记录全部保留、按平台采集不受影响） |
| **任务管理** | 聚合任务队列与采集任务统一查看：分页/状态与类型筛选（类型中文映射 + 图标 + 颜色区分：批量分析/质量审核/批量删除/近似重复检测删除/采集/向量回填）、进度条与完成统计（向量/删除/审核明细）、取消排队任务、失败采集一键重试、**任务进度 WebSocket 实时推送**（断线自动重连 + 轮询降级兜底，侧边栏展示连接状态）、预计剩余时间、**数据备份状态卡片**（自动补备开关 / 备份进行中（双通道运行锁）/ 最近成功备份 / 历史记录（成功失败标记）/ 备份日志尾部，只读实时展示） |
| **浏览器插件** | 小红书/抖音页面一键提取穿搭图片（弹窗批量 + 任意网页右键单图采集，通知/角标反馈）；上传前按平台 ID 预查重并支持「跳过已采集的图片」开关（服务端 `check-platform-id` 只读接口，垃圾桶素材释放平台 ID 允许重采）；「上传后自动分析」开启时自动触发 AI 打标；每次采集会话自动生成任务记录，采集管理页可查看插件采集历史、结果与漏斗 |

## 快速启动

### 1. 安装依赖

```bash
# Python 后端
cd backend
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
# 可选：运行自动化测试时的测试依赖
pip install -r requirements-dev.txt -i https://mirrors.aliyun.com/pypi/simple/

# Node.js 前端
cd ../web
npm install
```

### 2. 安装 AI 模型

```bash
# 安装 Ollama（从 ollama.com 下载 Windows 版）

# 推荐：Qwen3-VL:8B-Instruct（官方维护、256K 上下文、32 语言 OCR）
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

# 人脸识别子服务 face-service（独立 Python 3.10 环境跑 InsightFace，端口 18889；
# 提供博主 人脸注册 / 素材人脸匹配，未启动时人脸相关功能降级不可用）
cd ../face-service
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 18889
```

浏览器打开 `http://localhost:17777`

> 一键重启：`bash scripts/restart.sh` 会自动停旧进程并同时拉起后端 + 前端 + worker + 人脸识别子服务，并校验就绪。
>
> 自动拉起：`bash scripts/ensure-services.sh` 做「健康检查 + 只启动缺失服务」，幂等且带锁。服务启停由用户手动执行（项目规则），需要重启时手动运行上述脚本即可。

**自定义端口：**

```bash
# 后端 .env
PORT=18888                   # 后端监听端口
TAG_NAME_MAX_LENGTH=12       # 标签名最大字数（超过即判定为「低质命名-过长」，标签健康扫描与 AI 打标共用）
WORKER_CONCURRENCY=1         # 任务 worker 并发数（默认 1 保持串行）
ANALYZE_CONCURRENCY=1        # 批量分析任务内并发数（默认 1）
VIDEO_ANALYSIS_MAX_FRAMES=3  # 视频分析采样帧数（均匀采样覆盖全片，设 1 退回单帧）

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

> **按博主采集：** 采集方式选「按博主采集」，下拉选择目标博主（也可先在「人物管理」中新建博主），设置笔记数上限（默认 30）后开始。采集器进入博主主页逐个打开笔记详情页，提取轮播多图/视频/正文 caption/话题标签；视频下载入库（ffmpeg 提取首帧缩略图），同一笔记的图片与视频自动关联该博主。详情页访问随机间隔 2~4 秒且失败跳过，请控制采集节奏避免触发风控。

> **抖音采集：** 抖音任务推荐在调试 Chrome 中登录抖音后走 CDP 完整通道（搜索三层策略 + 详情页图集多图/视频/正文/话题标签提取）；未配置 CDP 端口时回退独立 Playwright 浏览器降级采集（仅封面图，反爬严格、结果可能为空，页面已提示推荐浏览器插件）。Cookie 管理页可对小红书/抖音 Cookie 做真实登录态校验，已失效 Cookie 会被任务创建前置校验拦截。
>
> **排序说明：** 「最新/最热」排序仅小红书搜索模式生效；抖音网页版固定综合排序。
>
> **定时采集：** 「采集管理 → 定时采集」页签可创建按间隔（1 小时 ~ 每周）自动执行的计划，由后端调度循环每 30 秒检查触发；小红书定时任务依赖调试 Chrome 保持运行（可在任务表单点击「启动 Chrome」由后端拉起）。新建/编辑任务时可在「话题库」区点击历史采集到的高频话题直接加入关键词。

### 5. 安装浏览器插件

1. Chrome 打开 `chrome://extensions`
2. 开启「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择 `browser-extension/` 目录

> **浏览器兼容性：** 插件为标准 Chrome MV3 扩展（依赖 Chromium 92+ 的 `chrome.scripting` 等 API）。「360 极速浏览器 X」（Chromium 122 内核）可按同样方式安装使用；老版 360 极速浏览器（Chromium 86 一代内核）不支持 MV3，无法使用。注意：插件与 CDP 采集引擎相互独立——360 浏览器不能做 CDP 采集（协议被阉割），但插件直连本地后端不受影响。

**插件功能说明：**

| 功能 | 说明 |
| ------ | ------ |
| 弹窗一键采集 | 在小红书/抖音页面点击插件图标 →「开始采集」，自动提取页面穿搭图片（懒加载兼容、头像/图标过滤、跨尺寸去重），并携带作者昵称、平台、笔记 ID、页面 URL 入库 |
| 右键单图采集 | 任意网页对图片右键 →「采集此图片到素材库」，结果经系统通知 + 工具栏角标（✓ / !）提示 |
| 上传前查重 | 提取后先按笔记 ID 预查后端是否已入库：已采集图片打「已采集」徽标并默认禁止勾选；接口异常时静默降级为正常采集 |
| 自动 AI 分析 | 「上传后自动分析」开启（默认开）时，图片素材入库后自动触发后端 AI 打标（触发后不等结果，失败不影响上传） |
| 采集会话任务 | 每次采集自动生成任务记录，可在「采集管理」页查看插件采集历史、结果与漏斗 |

> **使用前提：** 后端已启动（默认 `http://localhost:18888`，弹窗设置面板可改 API 地址）；目标页面为小红书或抖音（内容脚本只匹配这两个域名）。
>
> **已知边界：** 暂不支持其他网站与视频提取；元数据仅抓作者（不含标题/正文/话题标签）；标签与博主关联需到 Web 端补。


### 6. 启动移动端（可选）

```bash
cd mobile
npx expo start
```

### 7. 同步代码到 OpenViking 索引（可选）

项目代码 / 文档 / 数据库结构可一键同步到本地 OpenViking 索引（`viking://resources/fashion-inspo/`），
供语义检索（memfind / memsearch / memgrep）：

```bat
scripts\sync_openviking.bat   # 双击一键同步（需本地 OpenViking 服务 + Python 3.12+）
```

- 同步范围：`backend/app`、`backend/alembic`、`backend/tests`、`web/src`、`mobile`、
  `browser-extension`、`shared`、`scripts`、`docs`、根文档等文本文件，自动排除
  `node_modules` / `dist` / `storage` / `backups` 等；
- 数据库结构：实时从 `backend/fashion_inspo.db` 与 `face-service/face_service.db`
  导出完整表结构（列/索引/外键/行数）写入 `database/`；
- 幂等（upsert），可重复执行；向量与语义摘要在后台异步生成。

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
│   │   ├── exceptions.py         # 业务异常体系（AppException 子类 → HTTP 状态码全局映射）
│   │   ├── worker.py             # 任务队列 worker（python -m app.worker）
│   │   ├── models/               # 数据模型
│   │   │   ├── inspiration.py    # 穿搭素材 + AI分析日志
│   │   │   ├── tag.py            # 标签 + 别名（含 source 来源标识）
│   │   │   ├── tag_history.py    # 标签操作历史（before/after 快照，支持回滚）
│   │   │   ├── person.py         # 人物模型（Blogger/Model 两表 + 素材关联 + 模特照片组/照片 + 人脸特征关系）
│   │   │   ├── face.py           # 人脸特征库（博主人脸特征 + 素材人脸检测）
│   │   │   ├── scraper.py        # 采集任务 + 定时采集计划 + 话题标签存档（scraper_hashtags）
│   │   │   ├── task.py           # 异步任务队列
│   │   │   └── audit.py          # 操作审计日志
│   │   ├── schemas/              # Pydantic 请求/响应
│   │   ├── routers/              # API 路由
│   │   │   ├── inspirations.py   # 素材 CRUD
│   │   │   ├── tags.py           # 标签管理 + 批量/统计/扫描/排序/别名/共现/导入导出 + 高级管理（健康度/聚类/网络图/批量编辑/层级树/历史/效果分析）
│   │   │   ├── search.py         # 多维度搜索 + 相似素材
│   │   │   ├── bloggers.py       # 穿搭博主管理（含 CSV 导入与博主人脸注册）
│   │   │   ├── models.py         # 职业模特管理 + 照片组（模特写真）
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
│   │   ├── services/             # 业务逻辑（按领域拆分，主服务为兼容转发层）
│   │   │   ├── ai_service/       # AI 编排（analyze 分析 / quality 审核 / outfit_summary 大标签 / common）
│   │   │   ├── ai_parser.py      # AI 响应解析/修复（畸形处理）
│   │   │   ├── ai_tag_saver.py   # 标签标准化/保存/关联
│   │   │   ├── ai_analysis_service.py  # 分析/队列/历史业务逻辑
│   │   │   ├── inspiration_service.py  # 素材转发层 → inspiration_create/trash/query/update/dedupe/tags/state.py
│   │   │   ├── tag_service.py    # 标签转发层 → tag_crud/tag_alias/tag_inspirations/tag_query + tag_health/tag_cluster/tag_graph/tag_effect/tag_history/tag_batch_edit.py
│   │   │   ├── person_service.py # 人物转发层 → person/（base 基类 + services + csv_import + photo_sets）
│   │   │   ├── scraper_service.py     # 采集转发层 → scraper/（tasks 任务 + process 编排 + cookies/schedules/extension/results）
│   │   │   ├── blogger_face.py   # 博主人脸注册（平均池化）+ 素材人脸检测匹配
│   │   │   ├── face_thumbnail.py # 人脸缩略图裁剪（从已关联素材选图注册用）
│   │   │   ├── face_client.py    # 人脸识别子服务 HTTP 客户端（face-service）
│   │   │   ├── file_service.py   # 文件管理
│   │   │   ├── audit_service.py  # 操作审计日志写入
│   │   │   ├── near_duplicate_service.py  # 近似重复检测（感知哈希分组）
│   │   │   ├── crop_service.py   # 手机图剪裁
│   │   │   ├── admin_stats_service.py    # 管理后台统计
│   │   │   ├── chrome_manager.py # 采集专用 Chrome 生命周期
│   │   │   ├── gpu_service.py / model_config.py / model_prompt.py  # AI 模型/GPU/参数
│   │   │   ├── quality_learner.py       # 负样本初筛器（sklearn 逻辑回归）
│   │   │   ├── scraper_seen_service.py  # URL 墓碑表读写
│   │   │   ├── task_runner.py   # 异步任务执行框架
│   │   │   ├── task_runners/     # 异步任务执行器（batch_analyze / quality_check / batch_delete / deduplicate / vector_backfill / tag_health_scan / tag_cluster_scan / tag_network_analyze）
│   │   │   ├── vector/           # 向量检索（embedding / store / similarity）
│   │   │   ├── embedding_service.py  # 薄壳 → vector.embedding
│   │   │   ├── vector_service.py     # 薄壳 → vector.similarity
│   │   │   └── vector_store.py       # 薄壳 → vector.store
│   │   ├── scrapers/             # 平台爬虫
│   │   │   ├── base.py           # 抽象基类
│   │   │   ├── xiaohongshu.py    # 小红书
│   │   │   └── douyin.py         # 抖音
│   │   └── utils/                # 工具函数
│   │       ├── auth.py           # API Key 认证中间件
│   │       ├── file_hash.py      # 文件 MD5/SHA-256 哈希
│   │       ├── image_hash.py     # 感知哈希（dHash，近似重复检测）
│   │       ├── image_utils.py    # 缩略图/颜色提取
│   │       ├── performance.py    # 性能工具（耗时监控装饰器/BatchProcessor 并发批处理/FileCache/MemoryMonitor）
│   │       ├── time.py           # 统一 UTC 时间与 ISO 序列化
│   │       └── tag_normalizer.py # 标签标准化 + 同义词/别名映射
│   ├── scripts/                  # 维护脚本
│   │   ├── run_scraper.py         # 采集执行脚本（小红书 CDP / 抖音独立浏览器，断点续采）
│   │   ├── cleanup_tags.py        # 数据库脏标签清洗
│   │   ├── validate_tags.py       # 标签合法性校验
│   │   └── diagnose_scraper.py    # 采集诊断工具
│   └── storage/                  # 本地文件存储 (gitignore)
│       ├── images/
│       ├── thumbnails/
│       ├── videos/
│       ├── keyframes/            # 视频关键帧（按素材 ID 分目录，不入库按需列目录）
│       ├── trash/                # 垃圾桶（软删除文件移入此目录）
│       ├── person_photos/        # 人物照片（模特写真，与素材库 images/ 分离）
│       ├── person_thumbnails/    # 人物照片缩略图
│       ├── _crop_backup/         # 手机图剪裁原图备份（按时间戳分目录）
│       ├── _crop_dups/           # 剪裁内容重复对比预览（按批次分目录，决策期间暂存）
│       └── lancedb/              # 向量库（文本/图像向量）
│
├── face-service/                 # 独立人脸识别子服务（Python 3.10 + InsightFace，端口 18889）
│   ├── app/                      # main / router / face 引擎 / storage
│   ├── models/                   # buffalo_l 模型（RetinaFace 检测 + ArcFace 识别）
│   └── face_service.db           # 已注册人脸存储（SQLite，与主库隔离）
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
│   │   │   ├── tagAdvanced.ts    # 标签高级管理 API（健康度/聚类/网络图/批量编辑/层级树/历史/效果）
│   │   │   ├── search.ts         # 搜索 API
│   │   │   └── admin.ts          # 管理后台 API（导出/趋势/人物频次/审计/近似重复）
│   │   ├── stores/               # Pinia 状态
│   │   │   ├── inspirations.ts   # 素材状态
│   │   │   ├── tags.ts           # 标签筛选状态
│   │   │   ├── aiModels.ts       # AI 模型共享状态
│   │   │   └── ui.ts             # UI 状态
│   │   ├── views/                # 页面组件
│   │   │   ├── HomeView.vue      # 首页画廊
│   │   │   ├── UploadView.vue    # 上传素材
│   │   │   ├── ModelPhotoUploadView.vue # 添加模特照片（文件夹整组导入）
│   │   │   ├── SearchView.vue    # 高级搜索
│   │   │   ├── DetailView.vue    # 素材详情
│   │   │   ├── ScraperView.vue   # 采集管理
│   │   │   ├── TagManageView.vue # 标签管理（全功能）
│   │   │   ├── TagAdvancedManageView.vue # 标签高级管理（健康度/聚类/网络图/效果/层级树/历史 6 面板）
│   │   │   ├── PersonView.vue    # 人物管理（穿搭博主/职业模特双 Tab）
│   │   │   ├── PersonDetailView.vue # 人物详情（按类型适配：风格画像 + 模特照片组）
│   │   │   ├── ModelManageView.vue # AI 模型管理（全功能）
│   │   │   ├── AdminView.vue     # 素材管理（小菜单子页面：概览/疑似AI/批量清理/完整性/重复文件）
│   │   │   └── TaskManageView.vue # 任务管理（异步任务队列列表/详情/取消）
│   │   ├── components/           # 通用组件（按域分目录）
│   │   │   ├── layout/AppLayout.vue
│   │   │   ├── inspiration/      # MasonryGrid, InspirationCard, ImageLightbox, OutfitTagSection, SimilarSection
│   │   │   ├── model/            # ModelListPanel, AnalysisPanel(+子组件), SettingsPanel, QualityPanel, ReviewPanel
│   │   │   ├── admin/            # 统计/任务/疑似AI复核/重复/近似重复/完整性检查/导出/趋势/人物频次/审计日志子组件
│   │   │   ├── scraper/          # 采集任务表单/表格/日志/漏斗/结果/源配置/定时采集/统计看板子组件
│   │   │   ├── search/           # SearchBar, TagFilter + 搜索面板子组件
│   │   │   ├── tag/              # 标签列表/工具栏/弹窗子组件 + advanced/ 高级管理面板（健康度/聚类/网络图/效果/层级树/历史/批量编辑抽屉/规则行）
│   │   │   ├── person/           # PersonTypeTag, PersonFormModal, PersonLinkSection
│   │   │   └── upload/           # 上传拖拽/队列/选项子组件
│   │   ├── types/                # 跨组件复用的 TS 类型（admin/analysis/scraper/upload/tagAdvanced）
│   │   ├── utils/                # 工具函数
│   │   │   ├── sourceLabel.ts    # 来源类型中文映射
│   │   │   ├── tagHistoryDiff.ts # 标签操作历史 before/after 差异展示
│   │   │   └── format.ts         # 字节/耗时/日期格式化
│   │   └── composables/          # Vue composables（useWebSocket / useSearch / useOutfitTags / useTagManage / useAdminTask / useTagAdvanced / useTaskPolling 等）
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
│   ├── utils/sourceLabel.ts      # 来源类型中文映射
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
│   ├── person.ts
│   └── api.ts
│
└── scripts/                      # 工具脚本
    ├── seed_tags.py              # 预设标签导入
    ├── batch_import.py           # 批量导入本地图片
    ├── backfill_vectors.py       # 存量素材向量回填
    ├── restart.sh                # 一键重启前后端 + worker
    ├── ensure-services.sh        # 幂等确保服务运行（锁 + 健康检查，手动执行）
    └── generate_icons.py         # 生成插件图标
```

## 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                     Local PC (Windows 11)                    │
│                                                              │
│  ┌───────────────┐  ┌──────────────────┐  ┌──────────────┐   │
│  │ Vue 3 Web App │  │ Browser Extension│  │ React Native │   │
│  │(Desktop浏览器)│  │ (一键采集)       │  │ (手机端)      │   │
│  └───────┬───────┘  └────────┬─────────┘  └──────┬───────┘   │
│          │                   │                    │          │
│          └───────────────────┼────────────────────┘          │
│                              │ HTTP/WebSocket                │
│                              ▼                               │
│  ┌─────────────────────────────────────────────────────┐     │
│  │               FastAPI Backend (:18888)              │     │
│  │                                                     │     │
│  │  REST API │ WebSocket │ Background Tasks            │     │
│  │  ─────────────────────────────────────────────      │     │
│  │  Scraper Mgr │ AI Service │ File Service            │     │
│  │  Tag Service │ Auth Middleware                      │     │
│  │  定时采集调度 │ 垃圾桶自动清理                       │     │
│  └──────────────────────────┬──────────────────────────┘     │
│                             │                                │
│  ┌──────────────────────────┼──────────────────────────┐     │
│  │   SQLite (元数据)        │  Storage/ (图片文件)      │    │
│  └──────────────────────────┴──────────────────────────┘     │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐     │
│  │          Ollama (GPU: RTX 5060Ti 16GB)              │     │
│  │  Qwen3-VL:8B-Instruct — 穿搭视觉分析与标签提取       │     │
│  └─────────────────────────────────────────────────────┘     │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐     │
│  │     Chrome CDP (:9222) — 采集引擎连接真实浏览器      │     │
│  │     小红书/抖音零检测搜索 → 图片自动下载入库         │     │
│  └─────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────┘
```

## 数据模型

| 表 | 说明 | 关键字段 |
| ---- | ------ | ---------- |
| `inspirations` | 穿搭素材 | id, source_type, file_path, media_type, dominant_colors, rating（用户评分 0~5）, caption（笔记正文，按博主采集写入）, quality_status, quality_reason, is_ai_generated, deleted_at, trash_reason |
| `tags` | 标签 | id, name, category, source (seed/ai_generated/manual), pinned, sort_order, description, parent_id（层级父标签，null=根，与 category 正交）, updated_at（回滚冲突检测） |
| `tag_aliases` | 标签别名 | id, tag_id, alias — 同义词归一化（AI 识别到别名自动归为主标签） |
| `tag_history` | 标签操作历史 | id, batch_id（批次分组）, operation（create/rename/category_change/update/move/merge/alias_add/alias_remove/batch_edit/delete）, tag_ids, before_snapshot, after_snapshot, meta, created_at — 标签写操作 before/after 快照，支持单条回滚与冲突检测 |
| `inspiration_tags` | 素材-标签关联 | inspiration_id, tag_id, confidence |
| `ai_analysis_log` | AI 分析日志 | inspiration_id, model_name, log_type, raw_response, processing_time_ms, error |
| `scraper_tasks` | 采集任务 | platform, status, items_found/added, diagnostics（采集漏斗日志）, resume_token（断点续采进度） |
| `scraper_seen_urls` | URL 墓碑表 | source_url (PK), created_at — 删除后防止重复采集 |
| `scraper_schedules` | 定时采集计划 | platform, keywords, max_count, sort_mode, enabled, interval_minutes, next_run_at, last_task_id, run_count |
| `scraper_hashtags` | 采集话题标签存档 | id, name（全局唯一）, seen_count（累计出现次数，跨任务累加）, first_seen_at/last_seen_at, source_kind（blogger/search）, source_id, note_url, source_meta（最近来源明细 JSON） |
| `task_queue` | 异步任务队列 | type（batch_analyze/quality_check/batch_delete/deduplicate/vector_backfill/tag_health_scan/tag_cluster_scan/tag_network_analyze）, status（pending/running/success/failed/cancelled）, priority（越大越先被 worker 认领，批量清理类固定 -5）, progress, total/done, result, error, retry_count, next_retry_at |
| `pending_vector_backfills` | 向量回填攒批待处理表 | inspiration_id, type（image/text）, status, attempts — 素材上传/标签变更入队，worker 攒批重建向量（避免每素材创建小任务） |
| `audit_logs` | 操作审计日志 | id, action（batch_delete/delete_rejected/cleanup_orphans/empty_trash/batch_trash）, target_type, count, freed_bytes, detail, created_at — 破坏性批量操作留痕 |
| `bloggers` | 穿搭博主 | id, name, platform, platform_user_id, xhs_id（唯一）, ip_location, profile_url, avatar_path, bio, source, created_at, updated_at |
| `models` | 职业模特 | 同 bloggers（无 person_type 字段——表即类型） |
| `inspiration_bloggers` | 素材-博主多对多关联 | inspiration_id, blogger_id, confidence — 记录 AI 识别「图里是谁」的置信度 |
| `inspiration_models` | 素材-模特多对多关联 | inspiration_id, model_id, confidence |
| `model_photo_sets` | 模特照片组 | id, model_id, name（组名，默认取文件夹名）, created_at, updated_at |
| `model_photos` | 模特照片 | id, set_id, file_path, thumbnail_path, content_hash（组内 SHA-256 去重）, sort_order, created_at |
| `blogger_face_embeddings` | 博主人脸特征库 | id, blogger_id（唯一）, embedding（512 维 float32，平均池化）, updated_at — 素材人脸自动匹配依赖此特征库（职业模特无） |
| `inspiration_face_detections` | 素材人脸检测 | id, inspiration_id, face_index（图内序号）, embedding, matched_blogger_id（命中博主，空为疑似未知人脸）, confidence（余弦相似度）, created_at |

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

### 数据库迁移（Alembic）

数据库 schema 由 Alembic 管理（`backend/alembic/`）。后端启动时自动调用 `run_migrations()`：全新空库执行 baseline 建表，历史库自动 `stamp` 到 baseline，已管理库执行增量升级；worker 不跑 Alembic（并发启动会竞争 SQLite 写锁），仅做 create_all + `ensure_schema()`（手写补列）兜底。

**新增字段/表时**（不再往 `db_migrations.py` 的 `_SCHEMA_COLUMNS` 手写追加）：

```bash
cd backend
# 1. 修改 ORM 模型后生成迁移脚本（对比模型与库的差异）
alembic revision --autogenerate -m "描述"

# 2. 应用到数据库（或重启后端自动执行）
alembic upgrade head
```

> 生成 baseline / 测试时可用环境变量 `ALEMBIC_DB_URL` 指向临时库，避免触碰真实数据。

## API 概览

### 素材管理

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/api/inspirations` | 素材列表（分页；支持来源/媒体/状态/质量/标签/主色调/日期筛选，`ids` 逗号分隔精确定位，排序含 `tag_count`） |
| `POST` | `/api/inspirations` | 上传素材 |
| `POST` | `/api/inspirations/from-url` | 从 URL 导入素材（浏览器插件采集主通道，服务端下载规避跨域；支持 `source_platform_id`/`scraper_task_id`） |
| `GET` | `/api/inspirations/{id}` | 素材详情 |
| `PATCH` | `/api/inspirations/{id}` | 更新素材 |
| `POST` | `/api/inspirations/{id}/trash` | 移入垃圾桶（软删除，`reason` 可选：质量差/重复/不喜欢/隐私/其他/AI生成） |
| `POST` | `/api/inspirations/{id}/restore` | 从垃圾桶恢复 |
| `GET` | `/api/inspirations/trash` | 垃圾桶素材列表（分页，可按 `reason` 筛选） |
| `DELETE` | `/api/inspirations/trash` | 清空垃圾桶（`only_expired=true` 仅清理过期素材） |
| `DELETE` | `/api/inspirations/{id}` | 彻底删除（物理，不可恢复；普通删除请用 `/trash`） |
| `POST` | `/api/inspirations/{id}/tags` | 手动给素材关联标签（按名查找/创建，如穿搭大标签） |
| `DELETE` | `/api/inspirations/{id}/tags/{tag_id}` | 解除素材与标签的关联 |
| `POST` | `/api/inspirations/batch-tags` | 批量给多个素材关联标签（按名查找/创建） |
| `POST` | `/api/inspirations/batch-favorite` | 批量收藏/取消收藏素材 |
| `POST` | `/api/inspirations/batch-trash` | 批量移入垃圾桶（软删除） |
| `POST` | `/api/inspirations/batch-update` | 批量编辑元数据（来源/收藏/审核状态/疑似 AI 标记） |
| `GET` | `/api/inspirations/dominant-colors` | 库内主色调列表（hex + 计数，供颜色筛选） |
| `POST` | `/api/inspirations/{id}/face-detect` | 人脸检测并匹配博主特征库（重新检测覆盖旧结果；需 face-service 运行中） |
| `GET` | `/api/inspirations/{id}/face-detections` | 素材人脸检测列表（含匹配博主与置信度） |
| `PUT` | `/api/inspirations/{id}/face-detections/{det_id}` | 手动指定/解除人脸检测的博主关联（body: `{"blogger_id": 5}` 或 `{"blogger_id": null}`） |
| `DELETE` | `/api/inspirations/{id}/face-detections/{det_id}` | 删除单条人脸检测记录 |

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

### 标签高级管理（独立页面 `/tags/advanced`）

> 重型分析（健康度 / 聚类 / 网络图）走异步任务：`POST` 提交秒回 `task_id`，前端轮询 `GET /api/tasks/{id}` 读取结果。

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `POST` | `/api/tags/health/scan` | 提交健康度扫描任务（可选 `duplicate_threshold`） |
| `GET` | `/api/tags/health/{issue_type}` | 健康度问题明细（issue_type ∈ orphan/low_frequency/low_quality_name/duplicate，分页） |
| `POST` | `/api/tags/clusters/scan` | 提交自动聚类扫描任务（threshold/use_cooccurrence_boost/min_group_size） |
| `POST` | `/api/tags/clusters/apply` | 应用候选组（`group_id` 或显式 `target_tag_id`+`source_tag_ids`；`keep_as_alias` 保留源名为别名） |
| `POST` | `/api/tags/network/analyze` | 提交网络图分析任务（limit/min_count/category/with_communities/with_centrality） |
| `POST` | `/api/tags/batch-edit` | 批量高级编辑（regex_replace/affix/normalize/regex_merge 四类规则，`dry_run` 预览或执行） |
| `GET` | `/api/tags/tree` | 层级树懒加载（`parent_id` 缺省/null 表示根，含 `has_children`/`usage_count`） |
| `POST` | `/api/tags/move` | 批量移动层级（循环检测，错误汇总在 `errors`） |
| `GET` | `/api/tags/history` | 操作历史（分页 + operation/tag_id/batch_id 过滤） |
| `POST` | `/api/tags/history/{id}/rollback` | 回滚单条操作（冲突返回 409） |
| `GET` | `/api/tags/effect/trending` | 热度升降榜（days/top） |
| `GET` | `/api/tags/effect/combinations` | 标签组合排行（limit/min_count） |
| `GET` | `/api/tags/effect/coverage` | 覆盖度统计（带标签比例/平均标签数/按类别覆盖率） |
| `GET` | `/api/tags/effect/source_dist` | 来源分布 + 低效 AI 标签（使用 ≤1 次） |

### 人物管理（穿搭博主 / 职业模特已拆分）

> **拆分说明：** 原单表 `persons`（person_type 区分）已物理拆分为 `bloggers` 与 `models` 两张独立表与 API，素材关联同步拆为 `inspiration_bloggers` / `inspiration_models`，模特写真组归 `model_photo_sets`（见「数据模型」）。前端人物管理页为「穿搭博主 / 职业模特」双 Tab。关联一律使用 ID（人物名不唯一，规避同名歧义）。

#### 穿搭博主 `/api/bloggers`

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/api/bloggers` | 博主列表（分页 / 名称搜索 / 平台筛选 / 排序） |
| `POST` | `/api/bloggers` | 创建穿搭博主 |
| `GET` | `/api/bloggers/{id}` | 博主详情（含素材数与风格画像：高频标签 / 类别分布 / 趋势） |
| `PATCH` | `/api/bloggers/{id}` | 更新博主（显式传 `null` 可清空可空字段） |
| `DELETE` | `/api/bloggers/{id}` | 删除博主（需 API Key；仅无关联素材时可删） |
| `GET` | `/api/bloggers/{id}/inspirations` | 该博主的素材列表（分页 + 排序） |
| `GET` | `/api/bloggers/top` | 热门博主排行（按素材数） |
| `GET` | `/api/bloggers/ip-stats?limit=` | 博主 IP 属地统计（按属地聚合博主数，空属地归「未知」，返回 total + items） |
| `GET` | `/api/bloggers/suggestions` | 按名称建议博主（用于选择去重） |
| `POST` | `/api/bloggers/import-csv` | 上传 CSV 批量导入博主（按 xhs_id upsert，昵称/小红书号必填） |
| `POST` | `/api/bloggers/{id}/face` | 注册/重新注册博主人脸（上传照片 与/或 从已关联素材选图，合计 1~5 张，重复注册覆盖旧特征；需 face-service 运行中） |
| `GET` | `/api/bloggers/{id}/face` | 查询博主人脸注册状态 |
| `POST` | `/api/inspirations/{id}/bloggers` | 给素材批量关联博主（幂等） |
| `DELETE` | `/api/inspirations/{id}/bloggers/{bid}` | 解除素材与博主关联 |

#### 职业模特 `/api/models`

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/api/models` | 模特列表（分页 / 名称搜索 / 平台筛选 / 排序） |
| `POST` | `/api/models` | 创建职业模特 |
| `GET` | `/api/models/{id}` | 模特详情（含素材数与风格画像） |
| `PATCH` | `/api/models/{id}` | 更新模特（显式传 `null` 可清空可空字段） |
| `DELETE` | `/api/models/{id}` | 删除模特（需 API Key；仅无关联素材时可删） |
| `GET` | `/api/models/{id}/inspirations` | 该模特的素材列表（分页 + 排序） |
| `GET` | `/api/models/top` | 热门模特排行（按素材数） |
| `GET` | `/api/models/ip-stats?limit=` | 模特 IP 属地统计（按属地聚合模特数，空属地归「未知」，返回 total + items） |
| `GET` | `/api/models/suggestions` | 按名称建议模特（用于选择去重） |
| `POST` | `/api/inspirations/{id}/models` | 给素材批量关联模特（幂等） |
| `DELETE` | `/api/inspirations/{id}/models/{mid}` | 解除素材与模特关联 |

### 模特照片组（模特写真）

模特照片组与穿搭素材分离：模特照片是写真资料，不进入素材库、不参与 AI 打标与检索，仅按「模特 → 照片组 → 照片」浏览。文件独立落盘 `person_photos/`，避免被完整性检查误判为孤立文件。

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/api/models/{id}/photo-sets` | 照片组列表（分页，含照片数与封面） |
| `POST` | `/api/models/{id}/photo-sets` | 创建照片组（组名缺省回退「未命名照片组」） |
| `GET` | `/api/models/{id}/photo-sets/{set_id}` | 照片组详情（含分页照片列表） |
| `PATCH` | `/api/models/{id}/photo-sets/{set_id}` | 重命名照片组 |
| `DELETE` | `/api/models/{id}/photo-sets/{set_id}` | 删除照片组（级联删除照片与物理文件） |
| `POST` | `/api/models/{id}/photo-sets/{set_id}/photos` | 上传单张照片到照片组（组内 SHA-256 内容去重） |
| `DELETE` | `/api/models/{id}/photo-sets/{set_id}/photos/{photo_id}` | 删除照片组内单张照片 |

### AI 分析

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/api/ai/status` | AI 服务状态（Ollama 连接/版本/活跃视觉与嵌入模型） |
| `GET` | `/api/ai/models` | 已安装模型列表（标注视觉/文本嵌入角色） |
| `POST` | `/api/ai/models/pull` | 下载模型（SSE 进度） |
| `PUT` | `/api/ai/models/active` | 切换活跃视觉模型 |
| `PUT` | `/api/ai/models/embedding-active` | 切换文本嵌入模型（向量检索文本侧） |
| `DELETE` | `/api/ai/models/{name}` | 删除模型 |
| `POST` | `/api/ai/analyze/{id}` | 触发单个分析（视频素材传 `None` 路径，由后台任务懒解析关键帧） |
| `POST` | `/api/ai/batch-analyze` | 批量分析（创建异步任务，返回 task_id；支持 `models` × `prompt_ids` 多模型多提示词组合批量执行） |
| `POST` | `/api/ai/outfit-tags/suggest` | AI 建议穿搭大标签（只建议不入库） |
| `POST` | `/api/ai/retry/{id}` | 重试失败分析 |
| `GET` | `/api/ai/queue` | 分析队列统计 |
| `GET` | `/api/ai/unanalyzed-ids` | 未分析素材 ID 列表 |
| `GET` | `/api/ai/active-analyses` | 正在分析的任务 |
| `GET` | `/api/ai/history` | 分析历史（分页/筛选） |
| `GET` | `/api/ai/history/{id}` | 分析详情（含标签、结构化快照、质量审核、版本信息） |
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
| `GET` | `/api/ai/compare/{id}` | 分析结果对比（结构化标签差异 + 耗时 + 版本信息） |
| `GET` | `/api/ai/quality-dashboard` | 分析质量仪表盘（覆盖率/趋势/问题素材） |
| `GET` | `/api/ai/model-stats` | 按模型聚合的使用统计（成功率/平均耗时/平均标签数，标签按结构化快照口径） |
| `GET` | `/api/ai/prompt` | 获取当前模型的 Prompt（按模型隔离） |
| `PUT` | `/api/ai/prompt` | 更新当前模型的 Prompt |
| `GET` | `/api/ai/prompt/versions` | Prompt 版本历史 |
| `POST` | `/api/ai/prompt/save-version` | 保存当前 Prompt 为版本 |
| `POST` | `/api/ai/prompt/rollback` | 回滚 Prompt 到指定版本 |
| `POST` | `/api/ai/test-analyze` | 单图测试分析（SSE，不落库） |

> **AI 分析结果结构化存储（多版本对比与追溯）：**
>
> 每次分析/审核的结构化结果落为独立数据表，与素材当前状态（全量标签、`quality_status`）解耦，支撑跨模型/Prompt 版本的历史追溯：
>
> | 表 | 说明 |
> | ------ | ------ |
> | `ai_extracted_tags` | 单次分析「提取了哪些标签」的快照（log_id + tag_id + confidence） |
> | `ai_quality_review` | 单次审核的判定（result / reason / reviewed_at） |
>
> `ai_analysis_log` 新增 `prompt_version`（所用 Prompt 内容哈希前 8 位）与 `model_version` 字段，`GET /api/ai/history/{id}` 返回 `structured_tags` / `quality_reviews` / 版本字段；`GET /api/ai/compare/{id}` 的标签差异基于结构化快照精确计算（存量日志自动回退实时解析）。
>
> 历史数据回填（一次性迁移，从 `raw_response` 解析写入快照与版本字段）：
>
> ```bash
> cd backend
> python scripts/backfill_structured.py            # 预览将处理多少条
> python scripts/backfill_structured.py --apply    # 实际写入
> ```

### 质量审核

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `POST` | `/api/ai/quality-check` | 批量审核所有待审核（pending）图片素材（异步任务，返回 `task_id`） |
| `POST` | `/api/ai/quality-recheck` | 重新审核所有已通过（approved）素材：重置为 pending 后用最新标准重判（异步任务，返回 `task_id`） |
| `GET` | `/api/ai/quality-stats` | 质量审核统计（待审核/已通过/已拒绝/通过率） |
| `GET` | `/api/ai/manual-upload-auto-approve` | 获取「手动上传默认免审核」配置 |
| `PUT` | `/api/ai/manual-upload-auto-approve` | 设置「手动上传默认免审核」（`enabled=true/false`，可选持久化到 .env） |
| `DELETE` | `/api/inspirations/quality-rejected` | 将全部已拒绝（rejected）素材移入垃圾桶（软删除，可恢复） |

> **审核标准：** 判定为「合格」需是能看清整体搭配的完整真人穿搭照片。不合格包括：无人物（平铺图/尺码表/广告/纯文字）、仅单品特写、局部/裁切特写（如只有腿/脚/手臂/领口）、构图裁切过度。

> **手动上传免审核：** 默认开启（配置项 `manual_upload_auto_approve`，对应 .env 的 `MANUAL_UPLOAD_AUTO_APPROVE`）。开启后手动上传的素材直接标记为「已通过」，不进入待审核队列；关闭后恢复为待审核。可在「AI 模型管理 → 质量审核」面板一键切换。

### 负样本初筛器（质量审核前置初筛）

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/api/ai/quality-learner/status` | 初筛器状态 + 当前正负样本统计 |
| `POST` | `/api/ai/quality-learner/train` | 用正负样本训练/重训 sklearn 分类器（返回指标） |
| `POST` | `/api/ai/quality-learner/reset` | 删除模型，回滚到纯 VLM 审核 |

> **说明：** 初筛器用「垃圾桶 `质量差` 负样本 + `rejected` 素材 + `approved` 正样本」的 CLIP 图像向量（512 维，LanceDB）训练轻量逻辑回归，作为质量审核前置初筛：高置信度垃圾直接拒绝，低置信度仍走 VLM 复审（「宁缺毋滥」）。阈值见 `quality_classifier_threshold`，人工翻案机制原样保留。「AI 模型管理 → 质量审核」页有状态/指标/训练/回滚面板，也可用脚本 `python scripts/quality_learner.py status|train|reset` 操作。

### 任务队列

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/api/tasks` | 任务列表（分页，可按 status/type 筛选） |
| `GET` | `/api/tasks/{id}` | 任务详情（前端进度轮询） |
| `POST` | `/api/tasks/{id}/cancel` | 取消排队中的任务（仅 pending 可取消） |

> 以下重型操作均改造为「数据库驱动的异步任务」：接口秒回 `task_id`，由独立 worker 进程（`python -m app.worker`）执行、自动重试（2 次，指数退避）。worker 未启动时任务会一直停留在「排队中」；worker 并发与批内分析并发可用 `WORKER_CONCURRENCY`/`ANALYZE_CONCURRENCY` 配置（默认 1）。任务按 `priority` 降序认领（批量清理类固定 -5 低优先级）；任务生命周期事件（running/progress/success/failed/cancelled）经 WebSocket 广播，前端实时刷新、轮询降级兜底。
>
> | type | 触发接口 | 说明 |
> | ---- | -------- | ---- |
> | `batch_analyze` | `POST /api/ai/batch-analyze` | 批量 AI 分析 |
> | `quality_check` | `POST /api/ai/quality-check` / `quality-recheck` | 批量质量审核 / 重新审核 |
> | `batch_delete` | `POST /api/admin/batch-delete` | 批量删除素材 |
> | `deduplicate` | `POST /api/admin/deduplicate` | 智能去重删除 |
> | `vector_backfill` | 素材上传/标签变更自动入队 | 向量回填（攒批机制：`pending_vector_backfills` 表聚合，worker 批量重建，不再每素材创建小任务） |
> | `tag_health_scan` | `POST /api/tags/health/scan` | 标签健康度扫描（评分 + 四类问题 ID 列表） |
> | `tag_cluster_scan` | `POST /api/tags/clusters/scan` | 自动聚类扫描（候选合并组） |
> | `tag_network_analyze` | `POST /api/tags/network/analyze` | 网络图分析（社区/中心度/桥接） |

### AI 参数调优

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/api/ai/settings` | 获取分析参数（附全局默认值） |
| `PUT` | `/api/ai/settings` | 更新参数（置信度阈值全局持久化到 .env；超时按模型持久化） |
| `GET` | `/api/ai/sampling-params` | 获取采样参数（附全局默认值） |
| `PUT` | `/api/ai/sampling-params` | 更新采样参数（按模型独立持久化到 model_configs.json） |
| `DELETE` | `/api/ai/model-config` | 清除当前模型的自定义配置，回退全局默认值 |
| `POST` | `/api/ai/retry-all-failed` | 重试所有失败（仅图片） |
| `DELETE` | `/api/ai/reset?confirm=yes` | 重置所有数据+文件（破坏性接口，需 API Key） |

> **重置范围说明：** `/api/ai/reset` 清空素材、标签、素材-标签关联、分析日志及其结构化快照/审核结果、采集任务、URL 墓碑表，并删除图片/缩略图/视频与向量库文件。**不含**「人物（穿搭博主 bloggers / 职业模特 models）」「定时采集计划（scraper_schedules）」「任务队列（task_queue）」「操作审计日志（audit_logs）」——这些管理类数据在重置后保留。
>
> **注：** 视频素材已支持 AI 分析：按 `VIDEO_ANALYSIS_MAX_FRAMES`（默认 3）均匀采样关键帧逐帧识别，同名标签按最高置信度融合、主色调取首个产出帧，整视频产出单条分析日志（按帧留痕）。WebP 图片会自动转为 JPEG 以兼容 Qwen3-VL 模型。

### 采集管理

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/api/scraper/sources` | 可用采集源、状态与墓碑表计数 |
| `GET` | `/api/scraper/stats?days=30` | 采集统计（总量/成功率/平台分布/每日趋势） |
| `GET` | `/api/scraper/cdp-check/{port}` | 检测 Chrome 调试端口是否就绪 |
| `POST` | `/api/scraper/chrome/start` | 由后端拉起采集专用 Chrome（调试模式） |
| `POST` | `/api/scraper/chrome/stop` | 停止采集专用 Chrome |
| `GET` | `/api/scraper/chrome/status` | 采集专用 Chrome 连接状态 |
| `GET` | `/api/scraper/cookie-status?platform=` | Cookie 状态（存在性/时效/是否有效，附带最近一次真实登录态校验结果 `verify`） |
| `POST` | `/api/scraper/cookie-verify/{platform}` | 真实校验 Cookie 登录态（探测平台登录态接口，force 跳缓存；仅确定性证据判 `invalid`，网络/风控返回 `unknown` 不误判） |
| `POST` | `/api/scraper/cookie-import` | 导入平台 Cookie（JSON 数组） |
| `DELETE` | `/api/scraper/cookie/{platform}` | 删除平台 Cookie |
| `POST` | `/api/scraper/tasks` | 创建采集任务（搜索模式：`keywords`/`max_count`/`sort_mode`；按博主采集：`collect_mode=user` + `blogger_id`（或 `profile_url`）+ `max_notes`，小红书 CDP 模式预检 Chrome 连接） |
| `GET` | `/api/scraper/tasks` | 任务列表（`platform`/`status` 筛选 + `sort` 排序 + `page`/`size` 分页，返回 `items`/`total`/`stats`） |
| `DELETE` | `/api/scraper/tasks` | 清空所有采集任务记录 |
| `DELETE` | `/api/scraper/tasks/{id}` | 删除单条任务记录（素材保留，关联置空） |
| `POST` | `/api/scraper/tasks/{id}/cancel` | 取消运行中任务 |
| `POST` | `/api/scraper/tasks/{id}/retry` | 单任务续采（断点续采） |
| `POST` | `/api/scraper/tasks/retry-failed` | 重试所有失败任务 |
| `GET` | `/api/scraper/tasks/{id}/log` | 任务日志（最近 200 行） |
| `GET` | `/api/scraper/tasks/{id}/results` | 任务产出素材列表（分页） |
| `POST` | `/api/scraper/tasks/{id}/results/batch-delete` | 批量删除任务产出素材 |
| `POST` | `/api/scraper/extension-tasks` | 插件采集会话开始（创建任务记录，返回 `task_id`） |
| `POST` | `/api/scraper/extension-tasks/{id}/complete` | 插件会话结束（汇总发现/入库数量并标记完成） |
| `GET` | `/api/scraper/schedules` | 定时采集计划列表 |
| `POST` | `/api/scraper/schedules` | 创建定时计划（平台/关键词/数量/排序/间隔/启用） |
| `PATCH` | `/api/scraper/schedules/{id}` | 更新计划（启停/改间隔/改关键词等） |
| `DELETE` | `/api/scraper/schedules/{id}` | 删除定时计划 |
| `POST` | `/api/scraper/schedules/{id}/run` | 立即执行一次计划 |
| `GET` | `/api/scraper/hashtags` | 采集话题库（`sort=count|recent`、`min_count`、`limit`，返回话题 + 累计出现次数 + 来源博主名，供新建/编辑采集任务时复用为关键词） |

> **断点续采：** 失败任务可「续采」，沿用 `resume_token` 中的执行计划（关键词 × 排序）从未完成处继续，已入库图片不重复采集。
>
> **排序生效范围：** `sort_mode`（`general`/`latest`/`popular`）仅小红书搜索模式生效；抖音网页版固定综合排序。
>
> **定时采集：** 后端调度循环每 30 秒检查一次到期计划并创建任务；停用或改间隔会重算 `next_run_at`，执行失败照常推进并可从任务记录排查。
>
> **话题标签存档：** 按博主采集时从笔记详情页提取正文话题标签（`#早秋穿搭` 等）自动入库：按词全局去重、累计出现次数、记录最近来源博主/笔记（每篇笔记最多 20 个、单任务最多 10 个防脏数据）。采集任务新建/编辑表单提供「话题库」区，点击话题标签即加入关键词。
>
> **插件任务记录：** 浏览器插件上传素材时可携带 `scraper_task_id` 表单字段（`POST /api/inspirations`），将素材关联到插件采集任务，供结果预览与统计；插件会话通过 `extension-tasks` 两端点创建/汇总任务记录。

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
| `POST` | `/api/admin/batch-unmark-ai` | 批量将疑似 AI 素材重新标记为非 AI（按 ID 列表，同步返回 `updated`） |
| `POST` | `/api/admin/deduplicate` | 智能去重删除（异步任务，返回 `task_id`） |
| `GET` | `/api/admin/vector-stats` | 向量化状态统计（素材总数/已有图像与文本向量/缺失数/LanceDB 可用性） |
| `POST` | `/api/admin/vector-backfill` | 一键为缺失向量的素材创建回填任务（异步，返回 `task_id`；无缺失时返回 `count=0`） |
| `POST` | `/api/admin/crop-phone-screenshots/scan` | 手机图剪裁：扫描候选（只读，手动上传竖屏截图 + 黑边/截图特征检测 + 置信度分级） |
| `POST` | `/api/admin/crop-phone-screenshots/apply` | 手机图剪裁：按勾选 ID 执行（原图备份/裁剪替换/缩略图与哈希重建/向量回填；内容重复时返回 `duplicates` 对比数据，预览图暂存 `storage/_crop_dups/` 供人工决策） |
| `GET` | `/api/admin/export` | 导出全部素材为 CSV（含标签/关联博主/关联模特/审核状态，触发浏览器下载） |
| `GET` | `/api/admin/trend?days=` | 每日新增素材数量趋势（近 N 天） |
| `GET` | `/api/admin/person-frequency?limit=` | 人物 × 素材数量排行 |
| `GET` | `/api/admin/audit-logs?limit=` | 操作审计日志（按时间倒序） |
| `POST` | `/api/admin/near-duplicates` | 近似重复检测（全库随机抽样 + 感知哈希分组，仅返回候选、不删除；哈希首次计算后缓存到 `inspirations.phash`，单次请求渐进补算缺失哈希） |

### 其他

| 方法 | 路径 | 说明 |
| ------ | ------ | ------ |
| `GET` | `/api/health` | 健康检查（返回 `schema_version`，前端据此校验前后端契约） |
| `GET` | `/api/files/{path}` | 静态文件访问 |
| `WS` | `/ws` | WebSocket 实时推送 |

> **schema 版本握手：** `/api/health` 返回的 `schema_version` 由「数据库结构哈希（`db_migrations.py` 的列/索引清单自动计算）+ API 契约版本（`API_CONTRACT_VERSION` 手动递增）」拼接而成。前端期望值不再手工维护：dev 启动 / build 时由 `scripts/compute_schema_version.py` 调用后端代码自动计算并注入为全局常量 `__SCHEMA_VERSION__`（见 `web/vite.config.ts`）。不一致时在页面顶部弹出提示，避免后端更新未重启导致的「静默失败」；后端改动后重启前端 dev / 重新 build 即自动对齐。

## 安全加固（API 密钥）

后端默认**不启用**认证（开发模式）。若服务暴露在局域网/外网，建议为破坏性接口启用 API Key 保护。

**保护范围**：数据重置、批量删除、清空垃圾桶、去重删除、标签删除/合并、模型卸载、删除博主/模特、删除模特照片组/照片等**不可恢复或批量破坏性**接口；读接口与普通写操作（上传、收藏、移入垃圾桶等）不受影响。

```bash
# 1. 生成密钥并获取启用指引
python scripts/generate_api_key.py

# 2. 将输出的密钥追加到 backend/.env（脚本会打印完整指引）
#    API_KEY=<生成的密钥>

# 3. 重启后端生效
bash scripts/restart.sh
```

**生效后行为**：

- 破坏性接口请求头必须携带 `X-API-Key`，缺失返回 `401`，密钥错误返回 `403`
- 读接口无需密钥，正常访问

**前端接入**：浏览器控制台执行 `localStorage.setItem('apiKey', '<密钥>')` 后刷新页面，前端请求会自动附加 `X-API-Key` 头；或构建时设置 `VITE_API_KEY` 环境变量。

**说明**：`X-API-Key` 为简单共享密钥认证，仅防误操作/未授权调用；不替代 HTTPS/用户体系。破坏性接口清单维护于 `backend/app/utils/auth.py` 的 `DESTRUCTIVE_ROUTES`，新增破坏性接口时在其中追加一行即可。

## 数据备份与恢复

核心数据（SQLite 库、`storage/` 素材、LanceDB 向量、`.env`）均不进 git，需自行备份。项目提供备份/恢复脚本，支持每日自动备份 + 后端启动补备（双通道）。

```bash
# 手动备份到独立物理盘（推荐）
bash scripts/backup_data.sh E:/fashion-inspo-backups

# 从某份备份恢复（同机回滚，会先快照当前数据）
bash scripts/restore_data.sh E:/fashion-inspo-backups/<时间戳目录> --allow-overwrite
```

- **每日 03:00 自动备份**：用 Windows 任务计划程序注册 `scripts/backup_task.bat`（注册命令见 [备份恢复指南](docs/backup-restore.md)）。
- **启动后自动补备**：后端启动 10 分钟后，若距上次成功备份超过 20 小时则自动补跑一次（`.env` 的 `BACKUP_ON_STARTUP=false` 可关闭）。两个通道通过 `backup.lock` 互斥。
- **保留策略**：日备 7 份 + 周日周备 4 份；备份含 DB 一致性快照 + SQL 明文双保险 + 素材/缩略图/向量，备份后自动校验（integrity_check + 文件数/字节数比对）并写 `SUCCESS`/`FAILED` 标记。
- **热写入兼容**：备份期间后台任务（标签分析/向量回填）持续写入时，脚本自动对不一致目录做增量修复重试（最多 5 次），校验以复制完成时刻的「冻结源端清单」为基准（时点快照语义），不再与实时源端比对误报。
- **数据重置（reset）防呆**：执行前自动快照 DB 与素材目录到 `storage/_pre_reset_snapshot/`（保留 7 天），需输入 `DELETE` 二次确认，未配 API Key 时非本机访问直接拒绝。

完整说明（备份内容、自动注册、新机从零恢复 12 步 checklist、常见场景）见 **[docs/backup-restore.md](docs/backup-restore.md)**。

## 自动化测试

核心链路回归防护：后端 `pytest`（集成测试 + 服务单测）+ 前端 `vitest`（纯函数 / composable / store）。

**一键运行全部测试**（后端 + 前端类型检查 + vitest，Git Bash）：

```bash
bash scripts/test.sh          # 常规
bash scripts/test.sh --cov    # 后端额外输出覆盖率报告
```

### 后端（pytest，791 用例）

```bash
# 首次：安装测试依赖
cd backend
pip install -r requirements-dev.txt -i https://mirrors.aliyun.com/pypi/simple/

# 运行全部测试（自动使用临时数据库与临时存储目录，不触碰真实数据）
pytest
```

覆盖范围：

- **集成测试**：健康检查、破坏性接口 API Key 认证（401/403、读接口不受影响）、素材上传/详情/收藏/内容去重（SHA-256）/平台 ID 去重/**软删除过滤**/物理删除、垃圾桶移入/恢复/清空/原因筛选/过期清理/**状态不变量校验**（软删除三字段同真同假，R1/R2/R3 违规检出）、标签创建/冲突/关联/幂等/解除、关键词与标签组合搜索、**标签高级管理**（操作历史快照/单条回滚/merge 回滚恢复关联与别名/冲突检测 409；健康度四类问题识别与评分/任务全链路/分页；聚类候选组生成与 apply 合并+别名+历史同批次/group_id 解析/缺源容错；批量编辑四类规则 dry-run 与执行一致/撞名自动合并/历史回滚；网络图社区/类别过滤/任务全链路 + 图算法纯函数单测；层级树懒加载/循环检测/move 历史；效果分析升降榜分窗口/组合/覆盖度/来源分布）、人物模块（博主/模特拆分后的双套 CRUD/素材关联/风格画像/删除限制/CSV 导入/照片组，以及人物频次合并统计）、**博主人脸**（注册平均池化/重新注册覆盖/无脸拒绝/超 5 张拒绝/博主不存在 404、素材人脸检测命中与未命中/手动指定与解除/删除检测——均 mock face_client）、**批量操作**（批量收藏/移垃圾桶/编辑元数据/标签与主色调筛选）、**管理后台洞察**（CSV 导出/新增趋势/人物频次/审计日志/近似重复检测）、**手机图剪裁**（候选扫描/黑边检测/截图特征置信度/跳过明细/内容重复对比预览/物理删除重复素材后重裁/重新裁剪不清空其他组预览）、**任务执行器**（批量删除任务：删记录+删文件+释放空间；向量回填攒批/质量审核防假成功：全部失败抛任务级异常、部分失败正常完成）、**AI 分析与质量审核**（完整分析保存标签、审核二分类通过/拒绝、大标签建议、质量统计、批量审核/重审任务创建——均模拟 Ollama）、**采集模块**（插件会话任务全流程与结果批量删除、任务列表分页/筛选/排序/统计、定时计划 CRUD/启停/立即执行、Cookie 导入/删除/状态与**真实登录态校验**（前置检查/平台探测解释器/网络异常不误判/缓存/任务创建前置拦截）、**抖音采集脚本单测**（作品 ID 解析/规范 URL/媒体归一化/RENDER_DATA 提取/验证码感知/搜索流程/下载批次）、**小红书采集脚本单测**（详情页轮播视频正文提取/博主页链接收集/搜索提取漏斗/博主管线）、**平台爬虫类单测**（Cookie 归一化/用户搜索解析/抖音搜索解析）、**采集进程管理**（CDP 端口检测/活动任务判定/自动续采与取消感知）、任务结果 API（分页/跨任务隔离/批量删除/墓碑联动）、**按博主采集任务**：collect_mode=user 校验 Blogger 存在（404）、自动补全 profile_url/platform_user_id、缺博主与缺 URL 双 400、**话题库查询接口**：去重/计数/排序/筛选）
- **链路端到端旅程测试**（`test_journeys.py`，验证环节衔接而非单环节内部）：素材全旅程（上传→打标→向量→垃圾桶→恢复→再删→清空，每环节断言不变量零违规与墓碑/审计留痕）、采集旅程（插件会话→from-url 入库→任务完成→删除→墓碑→重采被拒，含恢复后墓碑仍在的防重复闭环）、失败旅程（文件缺失自愈：trash/restore 不产生悬空记录）、崩溃旅程（worker 心跳超时→`_reset_stale_tasks` 重置→重跑成功，不再假成功）
- **服务单测**：`tag_normalizer`（同义词归一化/相似度/名校验）、`ai_parser`（畸形 JSON 修复/标签提取/截断判断）、`quality_learner`（训练/样本不足/回滚，向量以 mock 替代）、`image_hash`（感知哈希近似不变性/区分度/汉明距离/非法文件）、`deduplicate`（去重评分/保留建议/平局/文件缺失兜底/物理删除）、`csv_safety`（CSV 公式注入转义）、`exceptions`（业务异常体系：AppException 基类/资源未找到与字段校验异常携带上下文属性/details 浅拷贝防外泄修改/快捷工厂函数）、`performance`（耗时监控装饰器同步+异步、BatchProcessor 并发批处理含失败隔离与并发上限、FileCache 键生成与命中、内存监控与优化装饰器、端到端组合——日志断言经 mock logger，psutil 相关用例已 mock）、`config_constants`（Settings 存储目录、ConfigConstants 各域常量访问与回退值、类型一致性验证）

> **错误响应契约**：服务层可抛 `app.exceptions` 的领域异常（NotFoundException → 404、ValidationException → 400、认证/授权 → 401/403，其余 AppException → 500），由 `main.py` 注册的全局 exception_handler 统一转换为 `{"detail": "错误描述"}` 格式，前端无需适配。

> **覆盖率度量**：安装 `pytest-cov` 后执行 `pytest --cov --cov-report=term-missing` 可生成行级覆盖率（`backend/.coveragerc` 已配置 `source=app` 并排除样板代码，当前约 52%）。剩余低覆盖盲区集中在：真实爬虫（`scrapers/`，0%，依赖真实浏览器）、`vector/similarity` 深度分支、`ai_analysis_service` 的批量重试/重试全部、`ws.py`（WebSocket）。

### 前端（vitest，161 用例）

```bash
cd web
npm test
```

覆盖范围：`format` / `sourceLabel` / `taskLabel` / `browseQuery` 纯函数、`tagHistoryDiff`（操作历史 before/after 差异与值格式化）、`useSplitResize` 拖拽 / `useBatchSelection` 批量多选 composable、`persons`（博主/模特双 store 实例 + 请求序号防乱序）/ `inspirations` / `tags` store（mock API）、`taskLabel` 任务类型/图标映射断言。

### 约定

- 测试使用**临时数据库与临时存储目录**，不会读写真实 `storage/` 与 `fashion_inspo.db`；如误将测试文件写入真实目录，用 `python scripts/clean_test_files.py --delete` 清理
- 新增破坏性接口或修改核心链路（软删除/垃圾桶/去重/认证）时，请在对应 `backend/tests/` 或 `web/src/**/__tests__/` 补充用例并跑通

## 环境要求

| 软件 | 用途 | 必须？ |
| ------ | ------ | :---: |
| Python 3.12+ | 后端 | ✅ |
| Node.js 20+ | Web + Mobile 前端 | ✅ |
| Ollama | AI 视觉推理 | ✅ |
| Qwen3-VL:8B-Instruct | 穿搭标签识别 | ✅ |
| Google Chrome | CDP 采集宿主浏览器 | ⚠️ 采集时必需 |
| Playwright | 采集引擎驱动 | ⚠️ 采集时必需 |
| ffmpeg | 视频关键帧提取 / 采集视频首帧缩略图 | ✅ 采集视频时使用 |

## 开源许可

本项目基于 **Apache License 2.0** 开源，详见 [LICENSE](./LICENSE)。

- 可以自由使用、修改、分发（含商用），需保留版权声明与本许可证副本
- 修改后的文件需标明变更；衍生作品不强制开源
- 涉及平台采集能力请遵守目标平台的服务条款与法律法规，合理控制采集频率
