# 待完成功能清单

> 本文档为 AI 穿搭素材库项目的待办事项清单，按优先级排序。
> 状态说明：` ` 未开始，`x` 已完成，`~` 进行中

## 高优先级

### 数据备份与灾难恢复

**背景：** 素材库核心数据——SQLite 数据库（fashion_inspo.db）、素材文件（storage/）、向量库（lancedb/）、环境配置（.env）——均被 .gitignore 排除，不在任何版本控制或备份中。目前 3000+ 张素材、标签体系、图像/文本向量全部依赖本地磁盘；一旦磁盘损坏、误删，或误触发 `/api/ai/reset?confirm=yes`（会清空全部数据与文件），损失不可逆。

**目标：**

- 提供完整导出/导入：素材文件 + 数据库元数据 + 标签 + 向量（向量可重建）
- 定期自动备份脚本（数据库快照 + 文件增量/全量），可接入 Windows 计划任务
- 破坏性操作加防呆：reset、清空垃圾桶、批量删除前二次确认 + 可选「先备份再执行」

**验收标准：**

- 一键导出后可在全新目录/另一台机器完整恢复（素材、标签、收藏、审核状态齐全）
- 备份脚本可定时执行，并保留多份历史快照
- 破坏性接口不再无提示清空

### Alembic 正式迁移，替换手写 db_migrations

**背景：** requirements.txt 已含 alembic==1.14.1，但 backend/alembic/ 从未初始化；当前靠手写 db_migrations.py 的「PRAGMA table_info + ALTER TABLE ADD COLUMN」，只能加列，无法 DROP/RENAME/改约束/改索引，且依赖 aiosqlite 手写逻辑。随 AI 结构化存储、视频关键帧表等演进，手写迁移会越来越难维护、易产生 schema 漂移。

**目标：**

- 初始化 Alembic，生成对应现有 schema 的 baseline 迁移
- 新字段/新表改用 Alembic revision，现有 ensure_schema 保留作兼容/兜底
- 迁移纳入启动流程与文档

**验收标准：**

- `alembic upgrade head` 在全新库上建出与现有 schema 一致的库
- 新增字段走 alembic revision，不再往 _SCHEMA_COLUMNS 手写追加

### 服务守护与监控

**背景：** 后端/前端/worker 依赖 SessionStart hook + 手动脚本拉起；worker 是单点、无进程守护、无日志轮转、无资源告警。近期 --reload 崩溃导致后端静默挂掉，暴露「服务挂了无感知、需人工发现」的问题。

**目标：**

- 服务健康检查仪表盘或轻量探针（后端/前端/worker 状态一目了然）
- worker 崩溃自动拉起 / 心跳租约（替代当前「启动时无条件重置 running 任务」）
- 日志轮转 + 磁盘/内存占用告警

**验收标准：**

- 任一服务异常退出后能自动拉起并记录原因
- 可在 Web 或脚本中一眼看到各服务健康状态

## 低优先级

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

### LoRA 微调 MiniCPM-V:8b（阶段 3，远期，数据 ≥3~5 千张再评估）

**背景：** 「垃圾桶素材库 + 质量审查负样本学习」方案的阶段 0（热身验证）、阶段 1（垃圾桶软删除）、阶段 2（负样本初筛器）已完成。阶段 2 已在现有 CLIP 图像向量（512 维，LanceDB）上训练 sklearn 逻辑回归做质量审核前置初筛；直接 LoRA 微调 MiniCPM-V:8b 在个人库数据量下不划算（Ollama 不支持训练，需独立训练栈 + 12~24GB 显存 GPU）。

**目标：** 当垃圾桶「质量差」负样本 + rejected 样本累计到 3~5 千张以上时，再评估是否引入 LoRA 微调。

- 引入 LLaMA-Factory / SWIFT 训练栈
- 准备正负样本图像数据集（正=approved，负=质量差/rejected）
- 微调 MiniCPM-V:8b 视觉判别能力，替代/增强现有 sklearn 初筛器
- 评估微调模型 vs 现有 sklearn 初筛器 vs 纯 VLM 的误杀率与耗时

**依赖：**

- 独立训练栈（LLaMA-Factory / SWIFT）
- 12~24GB 显存 GPU
- 样本量 ≥3~5 千张

**验收标准：**

- 微调模型在留存验证集上的误杀率与召回率优于或持平现有 sklearn 初筛器
- 可离线导出为 Ollama 可加载模型并接入现有质量审核链路

## 备注

- 向量检索与采集引擎自动化可以并行开发。
- AI 结果结构化存储依赖于任务队列稳定后实施。
- 视频分析功能依赖关键帧提取和 AI 复用，建议在任务队列和 AI 结构化存储完成后进行，以减少重复工作。
- 所有任务需遵循项目编码规范（见 CLAUDE.md）。
