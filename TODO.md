# 待完成功能清单

> 本文档为 AI 穿搭素材库项目的待办事项清单，按优先级排序。
> 状态说明：` ` 未开始，`x` 已完成，`~` 进行中

## 高优先级

### 引入任务队列，分离耗时任务与 API 响应

**背景：** 当前 FastAPI 直接处理批量分析、批量删除、采集等耗时操作，阻塞 API 响应，SQLite 并发写锁易导致超时。

**目标：** 将耗时任务异步化，提高 API 稳定性与多任务并发能力。

- 调研并选定任务队列方案（推荐 Arq + Redis 或 Celery + Redis）
- 新增 task_queue 表用于持久化任务状态（task_id, type, status, progress, result, error, created_at, updated_at）
- 将以下 API 改造为异步任务模式：
- POST /api/ai/batch-analyze → 创建任务，返回 task_id
- POST /api/ai/quality-check → 创建任务
- POST /api/ai/quality-recheck → 创建任务
- POST /api/admin/batch-delete → 创建任务
- POST /api/admin/deduplicate → 创建任务
- POST /api/scraper/tasks（采集任务本身已存在，但需改为由 worker 执行）
- 开发独立 worker 进程，处理任务队列中的任务
- WebSocket 推送任务进度更新（/ws 增加 task 进度消息类型）
- SQLite 优化：
- 开启 WAL 模式（PRAGMA journal_mode=WAL）
- 批量写入使用事务，减少锁竞争
- 新增任务查询接口：GET /api/tasks/{task_id} 返回任务状态
- 前端适配：批量操作按钮触发后显示进度，支持取消任务（如适用）

**验收标准：**

- 批量分析 100 张图片时，API 能在 1 秒内返回 task_id，后台任务独立运行
- WebSocket 能实时推送分析进度
- 任务失败可重试，状态持久化

## 中优先级

### 采集引擎自动化与任务持久化，降低使用门槛

**背景：** 目前需手动启动 Chrome 调试模式，且任务失败后无法从断点恢复，用户体验不佳。

**目标：** 后端自动管理 Chrome 生命周期，采集任务支持断点续采。

- 在 scraper_service.py 中实现 ChromeManager：
- 使用 subprocess.Popen 启动 Chrome（读取配置路径、端口、用户数据目录）
- 启动后轮询 <http://127.0.0.1:9222/json/version> 确认就绪
- 监控进程存活，意外退出自动重启（可配置重启上限）
- 支持手动停止和空闲超时自动关闭
- 增强 scraper_tasks 表：
- 添加 current_page、last_error、resume_token 等字段
- 每处理一页更新数据库，记录进度
- 采集任务执行逻辑支持断点续采：
- 任务重启时从 current_page 继续，避免重复采集
- 配合 scraper_seen_urls 表去重
- 前端采集页面：
- 添加"启动 Chrome"按钮，由后端拉起浏览器
- 显示 Chrome 连接状态（已连接/未启动/端口冲突）
- 新增 API：POST /api/scraper/chrome/start、POST /api/scraper/chrome/stop

**验收标准：**

- 用户无需手动打开命令行，点击按钮即可启动采集专用 Chrome
- 浏览器崩溃后，采集任务自动重启并从中断处继续
- 采集任务失败后可手动重试，且不会重复采集已处理内容

### API 版本握手

**背景：** 后端 schema 变更后若未重启，前端会静默降级（如漏斗按钮消失、字段拿不到），用户无从察觉。通过版本握手让前后端在启动时对一次"版本暗号"，不一致则明确提示，把"静默失败"变成"显式提示"。

**目标：** 前端启动时请求 /api/health，比对后端 schema_version 与本地期望值，不一致时弹出明显提示，而不是静默出错。

- 后端暴露版本号：/api/health 返回 schema_version 字段，每次改数据库结构（加列/改字段）时手动 +1
- 前端维护期望版本：前端代码硬编码一个常量，表示前端依赖的 schema 版本，跟着后端一起改
- 启动时校验：前端应用启动时请求 health，比对版本号，不一致则拦截并提示
- 不匹配提示 UI：在全局布局加横幅/弹窗，明确告知用户如何处理
- 可选改进：版本号自动计算（基于 db_migrations.py 字段清单内容哈希），避免忘记手动更新

**涉及模块：**

| 模块 | 改动 |
| ------ | ------ |
| `backend/app/main.py` | /api/health 返回 schema_version |
| `backend/app/db_migrations.py` | 定义 schema_version 常量（每次加字段递增） |
| `web/src/api/client.ts` | 启动时发起版本检查请求 |
| `web/src/App.vue`（或全局布局） | 版本不匹配时的提示 UI |

**验收标准：**

- 后端 schema 升级但未重启时，前端启动后显示明确提示，不再静默失败
- 版本一致时前端正常运行，无多余提示

## 低优先级

### AI 分析结果结构化存储，支持多版本对比与追溯

**背景：** 目前 AI 分析日志保存原始 JSON，解析逻辑与标签关联可能不统一，质量审核与标签提取关系模糊。

**目标：** 将 AI 分析结果拆分为结构化表，支持历史对比与版本追溯。

- 设计新增表结构：
- ai_extracted_tags：id, log_id, tag_id, confidence, created_at
- ai_quality_review：id, log_id, result (approved/rejected), reason, reviewed_at
- 修改 AI 分析流程：
- 每次分析同时生成标签提取结果和质量审核结果，分别写入上述表
- 保留 ai_analysis_log.raw_response 用于原始记录
- 在 ai_analysis_log 中增加 prompt_version 和 model_version 字段
- 更新相关 API：
- GET /api/ai/history/{id} 返回结构化标签和质量结果
- GET /api/ai/compare/{id} 支持对比不同 log_id 的标签差异
- 质量审核为 rejected 的素材，标签仍保留但标记为"待人工确认"
- 清理现有数据：编写迁移脚本，从 raw_response 中解析历史数据填充新表（如可行）

**验收标准：**

- 可以查询某个素材在不同模型/Prompt 版本下的历史标签
- 质量审核与标签提取互不干扰，可独立管理
- 数据迁移脚本可在现有数据库上成功执行

### 视频分析功能

**背景：** 当前仅支持图片素材，但穿搭内容大量以短视频形式存在（小红书/抖音），手动截图效率低。

**目标：** 支持上传穿搭视频，通过 AI 分析视频中的穿搭造型，提取关键帧并生成标签。

- 后端实现关键帧提取：
- 在 file_service.py 中新增 extract_keyframes() 函数
- 使用 ffmpeg 按固定间隔（如每 3 秒）或场景检测（select=gt(scene,0.3)）提取帧
- 提取的帧保存到 storage/images/ 或独立目录
- 视频文件上传与存储：
- 后端支持视频文件上传（扩展现有上传接口或新增接口）
- 视频文件保存到 storage/videos/
- 数据库 inspirations.media_type 增加 video 类型
- AI 分析复用：
- 对提取的关键帧调用现有 AI 图片分析接口
- 汇总多帧分析结果，生成该视频的标签集合（去重、按置信度合并）
- 前端支持：
- 上传页支持选择视频文件（限制格式、大小）
- 素材详情页增加视频播放器（如 <video> 标签）
- 展示提取的关键帧缩略图列表
- 展示基于关键帧生成的标签
- 数据库调整：
- inspirations 表增加 video_path、keyframes_path 或关联表
- 可能需要新增 inspiration_frames 表存储关键帧信息

**涉及模块：**

| 模块 | 改动 |
| ------ | ------ |
| `backend/app/services/file_service.py` | 关键帧提取 |
| `backend/app/routers/inspirations.py` | 视频上传接口 |
| `backend/app/models/inspiration.py` | 新增字段或表 |
| `web/src/views/UploadView.vue` | 上传视频入口 |
| `web/src/views/DetailView.vue` | 视频播放与关键帧展示 |

**依赖：**

- ffmpeg（系统已安装）
- 后端 file_service.py 需新增 extract_keyframes() 函数
- 前端可能需要引入视频播放组件（如 video.js 或原生播放器）

**验收标准：**

- 上传一个穿搭视频，后端自动提取关键帧并触发 AI 分析
- 前端详情页可播放视频，并展示关键帧及其对应的标签
- 视频素材可被搜索（通过标签或关键词）

### 新上传素材自动触发向量生成

**背景：** 向量检索已上线，但新上传 / 新采集的素材不会自动生成向量，需手动执行回填脚本。

**目标：** 在素材上传或 AI 分析完成后，自动为新素材生成文本 / 图像向量并写入 LanceDB。

- 在上传或分析完成链路中，对新增素材调用向量生成并 upsert 到 LanceDB
- 可复用任务队列异步执行，失败不影响主流程

**验收标准：**

- 新增素材后无需手动回填，即可被语义搜索 / 以图搜图检索到

## 备注

- 任务队列是其他耗时功能（批量分析、视频处理等）的基础，建议优先完成。
- 向量检索与采集引擎自动化可以并行开发。
- API 版本握手实现简单，可与任务队列同步推进，有效避免开发过程中的版本混乱。
- AI 结果结构化存储依赖于任务队列稳定后实施。
- 视频分析功能依赖关键帧提取和 AI 复用，建议在任务队列和 AI 结构化存储完成后进行，以减少重复工作。
- 所有任务需遵循项目编码规范（见 CLAUDE.md）。
