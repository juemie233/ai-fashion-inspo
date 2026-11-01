# AI 分析与标签治理说明

> **状态**：现行｜编写日期 2026-11-01｜依据 `backend/app/services/ai_service/`、`ai_tag_saver.py`、`quality_*.py`、`tag_*.py`
> **一句话**：素材入库后由本地 Ollama 视觉模型提取标签并落库；标签通过别名 / 合并 / 批量编辑 / 健康扫描 / 聚类治理；质量审核与负样本初筛器形成「判定 → 纠错 → 学习」闭环。
> **前提**：所有推理在本机完成，素材不出内网；模型与提示词均按模型隔离配置。

---

## 1. 链路总览

```text
素材（图片 / 视频首帧）
  │ POST /api/ai/analyze/{id} 或批量任务 batch_analyze
  ▼
Ollama 视觉模型（默认 qwen3-vl:8b-instruct）
  │ 原始响应（JSON，可能畸形）
  ▼
ai_parser：修复 / 提取 / 校验
  ▼
ai_tag_saver：标签标准化 → 关联素材（置信度）→ 主色写回
  ▼
ai_analysis_log：模型、prompt 版本、耗时、原始输出
  ▼
向量重建（CLIP）→ 可被以图搜图 / 相似推荐使用

质量审核（quality_check）：穿搭二分类 + AI 生成检测 → quality_status
  ▼
误判纠错（quality_corrections）→ 人工判定保护
  ▼
负样本初筛器（quality_learner）：CLIP 向量 + 逻辑回归 → 前置拦截
```

---

## 2. AI 分析

### 2.1 触发方式

| 入口 | 说明 |
| --- | --- |
| `POST /api/ai/analyze/{id}` | 单素材分析；插件入库后可自动触发 |
| `POST /api/ai/batch-analyze` | 批量任务（`batch_analyze`），支持暂停 / 恢复 / 取消 |
| `POST /api/ai/retry/{id}`、`/retry-all-failed` | 重试失败素材 |
| 后台任务 | 批量 / 组合分析均由 worker 执行，见 [异步任务体系](异步任务体系.md) |

**视频**：按配置抽取帧后逐帧分析，同名标签按最高置信度融合（多帧融合）；不分析非图片素材时接口返回 400。

### 2.2 模型与提示词配置

| 配置 | 位置 | 说明 |
| --- | --- | --- |
| 默认视觉模型 | `.env` 的 `OLLAMA_VISION_MODEL` | 默认 `qwen3-vl:8b-instruct` |
| 模型级采样参数 / 超时 | `backend/model_configs.json` | 按模型名隔离（thinking 模型需要更大超时与 `num_predict`），未配置回退全局 |
| 模型级 Prompt | `backend/prompt_configs.json` | 按模型名隔离，未配置回退 `settings.ai_analysis_prompt` |
| 标签名长度上限 | `TAG_NAME_MAX_LENGTH` | 默认 12，超长截断/过滤 |

> 两个 JSON 都是**运行时数据**（本地维护），不入库；分析日志记录 `prompt_version`（提示词内容哈希前 8 位）与 `model_version`，便于按版本对比。

### 2.3 解析与落库

- **容错解析**（`ai_parser.py`）：兼容模型常见的畸形输出——注释、Python set、裸词数组、单引号、截断 JSON；
- **标签标准化**（`ai_tag_saver.py`）：名称归一化（全角→半角、去空白/标点、小写比较）、颜色词映射、裸单品词过滤、按名称查找或创建；
- **关联去重**：同一素材同一标签只保留一条，竞态用 SQLite `INSERT ... ON CONFLICT` 处理；
- **主色**：模型返回的 `dominant_colors` 清洗后写回 `inspirations.dominant_colors`（JSON 数组字符串）；
- **日志**：每次分析写 `ai_analysis_log`（`log_type=analysis`），含 `raw_response`、`prompt_version`、`model_version`、`processing_time_ms`、`error`；关联 `ai_analysis_tag` 快照。

### 2.4 分析队列与历史

- `GET /api/ai/queue`、`/queue/pending`、`/active-analyses`：队列与进行中分析；
- `POST /api/ai/queue/pause` / `resume`：暂停 / 恢复队列消费；
- `GET /api/ai/history`、`/history/{id}`、`/history/export`：历史与导出；
- `GET /api/ai/compare/{id}`、`POST /compare-batch`：多模型 / 多提示词结果对比；
- `POST /api/ai/history/{log_id}/apply`：把某次历史结果应用到素材；
- `GET /prompt-options`、`/history/model-names`、`/history/prompt-versions`：A/B 选项。

---

## 3. 质量审核

### 3.1 判定模型

`quality_check` 任务对每条素材做两次轻量调用（`ai_service/quality.py`）：

1. **穿搭二分类**：是否穿搭内容；
2. **AI 生成检测**：疑似 AI 生成（只标记，不直接拒绝，与质量状态正交）。

结果写回 `inspirations`：

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `quality_status` | `pending` / `approved` / `rejected` | 当前状态；人工改过会记 `quality_reviewed_by='human'` |
| `quality_reason_code` | `not_outfit` / `single_item` / `partial` / `low_quality` / `ad` / `other`（+ 初筛器 `prefilter_reject`） | 结构化原因，支持筛选 |
| `quality_reason` | 自由文本 | 保留给人看 |
| `is_ai_generated` | 0/1 | 疑似 AI 生成 |
| `quality_reviewed_by` | `ai` / `human` | 人工判定优先，随机复审不覆盖 |

### 3.2 审核日志与复盘

- `ai_analysis_log`（`log_type=quality_check`）记录 `raw_output`（截断 2000 字符）、`prompt_hash`、`model_name`、`confidence`、`processing_time_ms`；
- `GET /api/ai/quality-stats` 提供通过率、原因分布、实测耗时、失败率告警；
- `GET /api/ai/quality-gray`：取 0.5~阈值之间的灰区样本按置信度降序，优先人工复审；
- 被拒素材的 AI 检测按素材 ID 的 SHA-256 **稳定抽样**（`QUALITY_REJECTED_AI_SAMPLE_RATE`，默认 0.1），同一素材结论可复现。

### 3.3 误判纠错闭环

`quality_corrections` 表记录「原判定 / 纠正结果 / 错在哪类 / 备注」；提交纠错即：

- 改正素材的 `quality_status`；
- 置 `quality_reviewed_by='human'`（与翻案同一保护，后续随机复审不覆盖）；
- 保留原 AI 日志，可用于后续 prompt / 模型对比。

### 3.4 负样本初筛器

`quality_learner.py`：用 CLIP 图像向量训练**逻辑回归**，在 VLM 审核前做前置拦截。

| 项 | 规则 |
| --- | --- |
| 正样本 | `quality_status=approved` 且未删除的图片素材 |
| 负样本 | 垃圾桶内图片素材，**但 `隐私` 原因除外**（隐私图不是「穿得不好」，学进去会把露肤/私密场景判成低质） |
| AI 生成图 | `collect_samples` **排除 AI 生成**（废片会教初筛器「AI 特征=垃圾」，误杀日后采集素材） |
| 输入 | LanceDB 中的 512 维 CLIP 图像向量（垃圾桶素材向量保留可复用） |
| 模型 | sklearn 逻辑回归，CPU 秒级训练；落盘 `storage/quality_classifier/` |
| 阈值 | `QUALITY_CLASSIFIER_THRESHOLD`，默认 0.9；高置信度直接拒，低置信度仍走 VLM 复审 |
| 开关 | `QUALITY_PRECHECK_ENABLED`，默认 **false**（会改变既有审核口径，先观望） |

初筛器状态分四态：`hit` / `pass` / `skipped` / `disabled`（后加 `precheck` 规则态）。

---

## 4. 标签体系与治理

### 4.1 数据模型

- `tags`：名称、分类、颜色、排序、别名、使用计数；
- `inspiration_tags`：素材-标签关联 + `confidence`（AI 判定置信度）;
- `tag_history`：写操作前后快照，支持单条回滚；
- `tag_corrections`：AI 打标纠错（多标 / 类别错 / 名称不规范），用于聚合纠错率；
- `tag_alias`：同义词归一化到主标签。

**分类**是自由字符串，前端约定映射为：风格 / 单品 / 颜色 / 穿着方式 / 版型 / 季节 / 属性 / 氛围 / 设计细节 / 表情 / 自定义（真源在 `mobile/constants/labels.ts` 与 Web 对应表）。

### 4.2 治理操作

| 能力 | 服务 / 接口 | 说明 |
| --- | --- | --- |
| CRUD / 合并 | `tag_crud.py`、`POST /tags/merge`、`/merge-batch` | 合并时重指向关联与别名，预查存在性 |
| 别名 | `tag_alias.py`、`/aliases` | 同义词归一，建议接口 `/alias-suggestions` |
| 批量高级编辑 | `tag_batch_edit.py`、`POST /tags/batch-edit` | 正则替换 / 前后缀 / 归一化 / 正则合并；`dry_run` 与执行同一实现 |
| 历史与回滚 | `tag_history_service.py`、`GET /history`、`POST /history/{id}/rollback` | before/after 快照；回滚做冲突检测 |
| 健康扫描 | `tag_health.py`、`POST /health/scan`、`GET /health/{issue_type}` | 孤儿 / 低频 / 低质命名 / 疑似重复；健康评分 |
| 自动聚类 | `tag_cluster.py`、`POST /clusters/scan`、`/clusters/apply` | 名称相似优先 + 共现辅助；只产建议，人工确认后应用 |
| 网络分析 | `tag_graph.py`、`POST /network/analyze` | 标签传播社区发现、度/介数中心度、桥接节点；支持暂停续算 |
| 效果分析 | `tag_effect.py`、`/effect/*` | 热度升降、组合排行、覆盖度、来源分布 |
| 颜色剥离 | `tag_color_strip.py`、`/color-strip/*` | `item_type` 中的颜色前缀剥离 + 撞名合并 |
| 纠错反馈 | `tag_correction_service.py` | 详情页「标错了 / 漏标了」落库，供质量看板统计 |

### 4.3 标签规模与性能约束

- 标签库 **2 万+**（2.2 万~2.3 万量级），`GET /api/tags` 一次约 **4.3 MB / 8 秒**；
- 移动端与 Web 都走**会话级缓存**，避免重复下载；
- 移动端标签墙必须虚拟列表 + 默认 60 条 + 搜索最多 200 条，否则 JS 线程阻塞导致 ANR；
- 批量合并走 `merge-batch`（一次请求代替 N 次），SQLite `IN(...)` 分片（默认 500）避免变量上限。

---

## 5. 关键配置

| 配置 | 默认 | 作用 |
| --- | --- | --- |
| `OLLAMA_VISION_MODEL` | `qwen3-vl:8b-instruct` | 分析模型 |
| `ANALYZE_CONCURRENCY` | 见配置 | 批内并发；越大显存压力越大 |
| `QUALITY_PRECHECK_ENABLED` | false | 初筛器总开关 |
| `QUALITY_CLASSIFIER_THRESHOLD` | 0.9 | 初筛自动拒绝阈值 |
| `QUALITY_AUTO_TRASH_REJECTED` | false | 审核拒绝后自动入垃圾桶 |
| `QUALITY_REJECTED_AI_SAMPLE_RATE` | 0.1 | 被拒素材 AI 检测抽样率 |
| `TAG_NAME_MAX_LENGTH` | 12 | 标签名长度上限 |

---

## 6. 排障

| 现象 | 排查 |
| --- | --- |
| 分析全部失败 | `curl 127.0.0.1:11434/api/tags` 看 Ollama；模型名是否与配置一致；任务会按可恢复错误重试 2 次 |
| 分析成功但无标签 | 看 `ai_analysis_log.raw_response`：模型返回空 / 格式异常 / 标签被长度与裸词规则过滤 |
| 同名标签重复 | 是否走了 `tag_alias` 归一；检查 `tag_normalizer` 归一化是否覆盖 |
| 标签墙卡死 / ANR | 是否绕过虚拟列表与 200 条上限；是否命中会话缓存 |
| 初筛器误杀 | 看 `quality_status` 与日志 `precheck`；调低阈值或关闭 `QUALITY_PRECHECK_ENABLED` |
| 初筛器学偏 | 检查负样本是否混入 `隐私` 或 AI 生成图；看 `quality_learner` 状态里的分层计数 |
| 审核结果被随机复审覆盖 | 人工判定（`quality_reviewed_by=human`）不应被覆盖；若被覆盖查 `force` 路径 |

---

## 7. 相关文档

- [CLIP编码说明](CLIP编码说明.md)——图像向量与相似检索
- [异步任务体系](异步任务体系.md)——批量分析、质量审核、标签扫描的执行与暂停/取消
- [人物详情页增强方案](人物详情页增强方案.md)——人物侧的标签画像
- [CLAUDE.md](../../CLAUDE.md)——负样本口径与软删除约定
