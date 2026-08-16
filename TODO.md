# 待完成功能清单

> 本文档为 AI 穿搭素材库项目的待办事项清单，按优先级排序。
> 状态说明：` ` 未开始，`x` 已完成，`~` 进行中

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
- API 版本握手实现简单，可与任务队列同步推进，有效避免开发过程中的版本混乱。
- AI 结果结构化存储依赖于任务队列稳定后实施。
- 视频分析功能依赖关键帧提取和 AI 复用，建议在任务队列和 AI 结构化存储完成后进行，以减少重复工作。
- 所有任务需遵循项目编码规范（见 CLAUDE.md）。
