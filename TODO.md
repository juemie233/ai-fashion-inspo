# 待完成功能清单

> 本文档为 AI 穿搭素材库项目的待办事项清单，按优先级排序。
> 状态说明：` ` 未开始，`x` 已完成，`~` 进行中

## 中优先级

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

### 视频分析功能（~ 进行中：视频上传存储已支持）

**背景：** 视频上传与存储已支持（inspirations 已含 video 类型，可上传 mp4），但尚未做关键帧提取与 AI 分析；穿搭内容大量以短视频形式存在（小红书/抖音），手动截图效率低。

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

## 备注

- 向量检索与采集引擎自动化可以并行开发。
- API 版本握手实现简单，可与任务队列同步推进，有效避免开发过程中的版本混乱。
- AI 结果结构化存储依赖于任务队列稳定后实施。
- 视频分析功能依赖关键帧提取和 AI 复用，建议在任务队列和 AI 结构化存储完成后进行，以减少重复工作。
- 所有任务需遵循项目编码规范（见 CLAUDE.md）。
