# AI 模型管理与分析质量（`/models` 页）

> **状态**：现行（2026-11-15 按代码盘点）。真源：`web/src/views/ModelManageView.vue`（62 行编排）、
> `web/src/components/model/`（15 个组件）、`web/src/stores/aiModels.ts`、`backend/app/routers/ai_models.py`。
> 打标/审核的**业务语义**见 [AI分析与标签治理](AI分析与标签治理.md)；本地出图模型见
> [ComfyUI环境与出图模型](ComfyUI环境与出图模型.md)。本文讲**这个页面能做哪些操作、参数改哪里生效**。

## 1. 一句话

一个页面串起「装模型 → 调参数 → 跑批量分析 → 审被拒素材 → 看分析质量」五件事，对应五个页签。

| 页签 | 组件（行数） | 回答什么问题 |
| --- | --- | --- |
| 模型管理 | `ModelListPanel`(719) | 有哪些模型、装没装、显存够不够、怎么下载/更新/切换 |
| 质量审核 | `ReviewPanel`(522) | 被拒的素材长什么样、要不要翻案、要不要清掉 |
| 标签分析 | `AnalysisPanel`(239) + `AnalysisQueueOverview`(334) + `AnalysisHistoryCard`(772) + 3 个弹窗 + `MultiModelAnalyzeCard`(173) | 批量分析跑得怎么样、单条结果如何、多模型/多批次怎么比 |
| 参数调优 | `SettingsPanel`(722) + `SettingsTestCard`(185) | 置信度、超时、Prompt、采样参数怎么改、改完怎么验 |
| 分析质量 | `QualityPanel`(316) + `QualityLearnerCard`(120) + `QualityInsightsPanel`(193) | 打标质量指标、负样本初筛器的训练/状态/回滚 |

## 2. 模型管理

| 能力 | 说明 |
| --- | --- |
| 连接状态 | Ollama 是否可达；不可达时列表与下载全部禁用并给引导 |
| GPU 显存 | 自动轮询 + 短时趋势折线（「已用显存」），用来判断要不要卸载模型 |
| 模型列表 | 每个模型的详情（大小/参数/量化）、更新、复制（另存一个 tag）、删除 |
| 设为活跃 | 视觉模型与 **embedding 模型分开选**（`PUT /models/active`、`/models/embedding-active`） |
| 下载队列 | 多模型排队（同名去重），进度走 **SSE 流式**而不是轮询 |
| 卸载模型 | 释放显存（`POST /unload-model`），跑 ComfyUI 出图前常用 |
| 使用统计 | `GET /model-stats`：各模型调用次数/耗时等 |

对应端点（`/api/ai` 前缀）：`GET /models`、`GET /status`、`GET /gpu-stats`、`POST /models/pull`、
`POST /models/{name}/update`、`POST /models/copy`、`DELETE /models/{name}`、`PUT /models/active`、
`PUT /models/embedding-active`、`POST /unload-model`、`GET /model-stats`、`GET /models/{name}/detail`。

## 3. 质量审核

- 顶部三态统计：待审核 / 已通过 / 已拒绝；
- 未通过素材网格：**原因筛选下拉里带数量**，一眼看出哪类拒绝最多；
- 单条「标记为通过」（翻案）、批量「通过」、批量「移入垃圾桶」——
  批量移入垃圾桶走软删除且 `trash_source=auto`（负样本仍会计入，见 [素材生命周期与垃圾桶](素材生命周期与垃圾桶.md) §6）；
- 批量删「已拒绝素材」是另一条路径：`DELETE /api/inspirations/quality-rejected`（软删、可恢复）。

## 4. 标签分析

| 组件 | 作用 |
| --- | --- |
| `AnalysisPanel` | 选素材/范围发起批量分析，展示当前批次 |
| `AnalysisQueueOverview` | 队列概览：排队/运行/完成数，运行中可暂停/取消 |
| `AnalysisHistoryCard` | 历史列表（772 行）：按时间/状态筛，点开看单条结果 |
| `AnalysisDetailModal` / `AnalysisCompareModal` / `AnalysisCompareBatchModal` | 单条结果、两条对比、两批对比 |
| `MultiModelAnalyzeCard` | 同一批素材用不同模型跑，横向比标签质量 |

分析任务本身跑在 worker 上（任务类型 `batch_analysis` / `combined_analysis`），本页只是触发与查看。

## 5. 参数调优（改哪里、何时生效）

| 分组 | 参数 | 生效方式 |
| --- | --- | --- |
| 基础参数 | 活跃模型、置信度阈值、分析超时（10~300 s）、Ollama 地址 | 保存即写 `backend/model_configs.json`，下一次分析生效 |
| 分析 Prompt | 打标 prompt 全文（前端可编辑） | 同上；改完建议用「测试卡片」跑一张再批量 |
| 人物简介 Prompt | 博主简介生成用的 prompt | 同上 |
| 采样参数 | Temperature / Top P / Top K / Max Tokens | 同上 |

> **`backend/model_configs.json` 已入库，但运行时会被后端重写**：提交前先 `git diff backend/model_configs.json`，
> 只提交**有意修改的默认值**；界面操作产生的 diff 用 `git checkout -- backend/model_configs.json` 丢掉
> （CLAUDE.md「Git 提交格式」有同款约定）。

## 6. 分析质量

- 质量指标面板：按标签维度看打标质量（哪些标签过用/欠用）；
- 负样本初筛器（`QualityLearnerCard`）：状态、训练、回滚——训练数据来自垃圾桶（排除 `隐私` 与 AI 生成图）；
- 洞察面板：拒绝原因分布、误判纠错的沉淀效果。

阈值 `quality_classifier_threshold`（默认 0.9，宁缺毋滥）在 [配置项参考](../overview/配置项参考.md) 里可查。

## 7. 常见坑

1. **改了采样参数没生效**：模型未重载；`POST /unload-model` 后再跑，或等 Ollama 自然过期。
2. **下载队列卡在 0%**：Ollama 在拉大模型时磁盘/带宽占满；SSE 断开不会中断后端下载，刷新页面可续看。
3. **分析超时**：单条 10~300 s 上限，视频/大图会顶到上限；不是模型坏了，先降并发（`ANALYZE_CONCURRENCY`）。
4. **翻案后列表没变**：质量审核页有分页与筛选，翻案只改单条状态，取消筛选再看。
5. **`model_configs.json` 每次提交都脏**：界面改参数就会重写该文件，按 §5 的约定处理。

## 8. 相关文档

- [AI分析与标签治理](AI分析与标签治理.md) — 打标链路、质量审核判定、负样本初筛、标签体系
- [素材生命周期与垃圾桶](素材生命周期与垃圾桶.md) — 被拒素材进垃圾桶的语义与负样本排除
- [ComfyUI环境与出图模型](ComfyUI环境与出图模型.md) — 本地出图用的模型与显存取舍
- [配置项参考](../overview/配置项参考.md) — `AI_*` / `OLLAMA_*` / `QUALITY_*` 全部默认值
