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

## AI 模型管理增强项

> 2025 年模型管理页缺口修复（死开关/嵌入模型/初筛器界面/统计口径）已完成；以下为待实现的增强项。

### 模型详情查看
- 模型列表点击查看详情弹窗：调用 Ollama `/api/show` 展示参数量、量化方式、架构、模板与许可证
- 涉及模块：`backend/app/routers/ai_models.py`、`web/src/components/model/ModelListPanel.vue`
- 验收：任意已安装模型可查看完整元信息

### 模型更新与复制
- 已安装模型支持「更新到最新版」（对同 tag 执行 pull）；支持复制模型（`ollama cp`，如 `qwen3-vl:8b-instruct → qwen3-vl:latest`）
- 涉及模块：`ai_models.py` 新增 update/copy 端点、`ModelListPanel.vue` 操作列
- 验收：模型可一键更新/复制，界面实时刷新列表

### 下载体验增强
- 常用模型下拉选择（官方库热门列表）、多模型排队下载、下载完成自动刷新「模型使用统计」
- 涉及模块：`ModelListPanel.vue`
- 验收：无需手输模型名即可下载；任务完成后统计即时更新

### GPU 显存自动监控
- 显存占用定时轮询（如 5 秒）+ echarts 短时趋势图，替代当前手动刷新
- 涉及模块：`ModelListPanel.vue`、新增 GPU 趋势 composable
- 验收：显存变化自动更新，可回看最近占用曲线

### 分析质量仪表盘升级
- 手写 CSS 柱状图换 echarts；增加按模型成功率对比、错误原因分布、失败素材直达列表
- 涉及模块：`backend/app/services/ai_dashboard_service.py`、`QualityPanel.vue`
- 验收：可切换维度查看质量趋势与失败归因

### 分析历史增强
- 时间范围筛选、按耗时排序、失败原因列、CSV 导出
- 涉及模块：`backend/app/routers/ai_analysis.py`、`useAnalysisHistory.ts`、`AnalysisHistoryCard.vue`
- 验收：可按时间/耗时检索历史，一键导出分析记录

### 每模型配置总览
- 一览 `model_configs.json` / `prompt_configs.json` 中哪些模型有自定义配置；支持把某模型的参数/Prompt 复制到另一模型
- 涉及模块：`model_config.py`、`model_prompt.py`、`SettingsPanel.vue` 新增总览区块
- 验收：配置分布一目了然，可跨模型复制配置

### 单图测试直接传图 + 服务信息
- 单图测试支持直接上传图片（不依赖已有素材 ID）；连接卡片展示 Ollama 版本与运行时长
- 涉及模块：`backend/app/routers/ai_dashboard.py`（test-analyze 支持 multipart）、`SettingsPanel.vue`、`ModelListPanel.vue`
- 验收：任选本地图片即可测试 prompt/参数效果；Ollama 版本可见

## 备注

- 向量检索与采集引擎自动化可以并行开发。
- AI 结果结构化存储依赖于任务队列稳定后实施。
- 视频分析功能依赖关键帧提取和 AI 复用，建议在任务队列和 AI 结构化存储完成后进行，以减少重复工作。
- 所有任务需遵循项目编码规范（见 CLAUDE.md）。
