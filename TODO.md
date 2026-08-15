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

### 垃圾桶素材库 + 质量审查负样本学习（方案已评估，推荐分阶段实施）

**背景：** 当前素材删除为全链路物理删除（无恢复机会）；质量审核完全依赖 MiniCPM-V 提示词判断，重复出现的垃圾素材（尺码表、平铺图、纯广告等）每次都要消耗完整 VLM 审核，无特征学习能力。方案评估结论：垃圾桶本身可行；「学习特征」推荐在现有 CLIP 图像向量（512 维，已存储于 LanceDB）上训练轻量分类器做审核前置初筛，直接 LoRA 微调 MiniCPM-V:8b 在个人库数据量下不划算（Ollama 不支持训练，需独立训练栈 + 12~24GB 显存 GPU），列为远期选项。

**目标：** 分阶段实施

- 阶段 0（热身，零新基建）：
  - 用现有 quality_status=rejected 素材 + 人工翻案记录（batch-unmark-ai、人工改状态）攒正负样本数据集
  - 验证「CLIP 向量 + 轻量分类器」在本地数据上的区分度，达标再进入后续阶段
- 阶段 1（垃圾桶，软删除）：
  - inspirations 增加 deleted_at / trash_reason 字段（删除原因枚举：质量差/重复/不喜欢/隐私/其他，学习只用「质量差」子集保证语义纯净）
  - 所有列表/统计/搜索/相似推荐/完整性检查/重复检测查询过滤已删除素材
  - 文件移入 storage/trash/（完整性检查按目录排除，已有先例）
  - 新 API：移入垃圾桶 / 恢复 / 立即清空 / 30 天自动清理任务
  - 前端：管理页新增「垃圾桶」小菜单，卡片删除改为移入垃圾桶，详情页支持恢复
  - 同步修订 CLAUDE.md「软删除：不使用」约定
- 阶段 2（负样本初筛器）：
  - 用垃圾桶「质量差」样本 + rejected 素材训练 sklearn 逻辑回归/SVM（输入为现有 CLIP 图像向量，无需 GPU）
  - 作为质量审核前置筛选：高置信度垃圾直接拒绝，低置信度仍走 VLM 复审
  - 保持「宁缺毋滥」哲学：阈值进 settings 可调，人工翻案机制原样保留
  - 学习-评估-回滚闭环：留存验证集对比误杀率，指标变差可回滚旧分类器
- 阶段 3（远期，数据 ≥3~5 千张再评估）：LoRA 微调 MiniCPM-V:8b（需引入 LLaMA-Factory/SWIFT 训练栈 + 大显存 GPU）

**涉及模块：**

| 模块 | 改动 |
| ------ | ------ |
| `backend/app/models/inspiration.py` | 新增 deleted_at、trash_reason 字段 |
| `backend/app/services/inspiration_service.py` | 列表查询过滤 + 垃圾桶 CRUD 服务 |
| `backend/app/routers/inspirations.py`、`admin.py` | 垃圾桶/恢复/清空接口 |
| `backend/app/services/vector/*` | 垃圾桶素材向量保留策略（建议独立负样本表） |
| `backend/app/services/ai_service/quality.py` | 串联负样本分类器预筛 |
| `backend/app/services/quality_learner.py`（新增） | 分类器训练/评估/回滚 |
| `web/src/views/AdminView.vue` + `components/admin/` | 垃圾桶子页面 |
| `web/src/components/inspiration/` | 卡片删除改为移入垃圾桶 |
| `CLAUDE.md` | 修订软删除约定与来源类型式文档同步 |

**依赖：**

- 阶段 2 依赖阶段 1（负样本语义需「删除原因」标注保证纯净，否则分类器吃噪声）
- 现有 CLIP（sentence-transformers clip-ViT-B-32）与 LanceDB 向量设施直接复用
- 阶段 3 依赖独立训练栈与 GPU，暂不启动

**验收标准：**

- 素材删除后进入垃圾桶，30 天内可恢复，到期自动清理
- 正常库、搜索、相似推荐、统计均不出现垃圾桶素材
- 负样本分类器在留存验证集上误杀率不高于纯 VLM 审核，且审核平均耗时/调用量下降
- 分类器判定可被人工翻案覆盖，阈值可通过 settings 调整

## 备注

- 向量检索与采集引擎自动化可以并行开发。
- API 版本握手实现简单，可与任务队列同步推进，有效避免开发过程中的版本混乱。
- AI 结果结构化存储依赖于任务队列稳定后实施。
- 视频分析功能依赖关键帧提取和 AI 复用，建议在任务队列和 AI 结构化存储完成后进行，以减少重复工作。
- 所有任务需遵循项目编码规范（见 CLAUDE.md）。
- 垃圾桶与负样本学习为同一方案的两个阶段：垃圾桶先行，分类器学习依赖垃圾桶的「删除原因」标注，微调大模型列为远期选项。
