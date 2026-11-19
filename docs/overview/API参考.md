# API 参考（路由清单）

> **状态**：自动生成（2026-11-19，共 24 个模块 / 341 个端点）。
> 由 `scripts/data/gen_docs_from_code.py` 从 `backend/app/routers/*.py` 抽取，**不要手改**——
> 改了 router 就重跑脚本（`--check` 可校验是否过期）。
> 参数与响应结构以代码和 `/docs`（FastAPI 自动 OpenAPI，后端在跑时 `http://127.0.0.1:18888/docs`）为准。

## 模块总览

| 模块 | 前缀 | 端点数 |
| --- | --- | ---: |
| [admin](#admin) | `/api/admin` | 20 |
| [ai_analysis](#ai_analysis) | `/api/ai` | 20 |
| [ai_dashboard](#ai_dashboard) | `/api/ai` | 5 |
| [ai_models](#ai_models) | `/api/ai` | 12 |
| [ai_outfit](#ai_outfit) | `/api/ai` | 1 |
| [ai_quality](#ai_quality) | `/api/ai` | 12 |
| [ai_reset](#ai_reset) | `/api/ai` | 1 |
| [ai_settings](#ai_settings) | `/api/ai` | 15 |
| [bloggers](#bloggers) | `/api/bloggers` | 43 |
| [collections](#collections) | `/api/collections` | 12 |
| [face_scan](#face_scan) | `/api/face-match、/api/face-scan` | 15 |
| [favorites](#favorites) | `/api/favorites` | 5 |
| [files](#files) | `/api/files` | 2 |
| [generate](#generate) | `/api/generate` | 17 |
| [health](#health) | `/api/health` | 1 |
| [inspirations](#inspirations) | `/api/inspirations` | 31 |
| [models](#models) | `/api/models` | 19 |
| [pose_gallery](#pose_gallery) | `/api/pose-gallery` | 5 |
| [quality_learner](#quality_learner) | `/api/ai/quality-learner` | 3 |
| [scraper](#scraper) | `/api/scraper` | 44 |
| [search](#search) | `/api/search` | 6 |
| [tags](#tags) | `/api/tags` | 43 |
| [tasks](#tasks) | `/api/tasks` | 8 |
| [ws](#ws) | `/ws` | 1 |

## admin

前缀：`/api/admin`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/admin/stats` | `admin_stats` | 素材总览仪表盘数据（聚合逻辑在 app.services.admin_stats_service）。 |
| `GET` | `/api/admin/backup/status` | `backup_status` | 数据备份状态（只读）：开关/最近成功备份/历史/运行锁/日志尾部。 |
| `GET` | `/api/admin/largest-files` | `largest_files` | 列出占用空间最大的前 N 个文件；扫描在 ``admin_stats_service``。 |
| `GET` | `/api/admin/integrity-check` | `integrity_check` | 数据完整性检查（缺文件 / 孤立文件）；扫描在 ``admin_stats_service``。 |
| `POST` | `/api/admin/cleanup-orphans` | `cleanup_orphan_files` | 删除所有孤立文件（磁盘上有但数据库无记录的文件）。 |
| `POST` | `/api/admin/batch-delete` | `batch_delete` | 批量删除素材（已改造为任务队列，创建任务后返回 task_id）。 |
| `POST` | `/api/admin/batch-unmark-ai` | `batch_unmark_ai` | 批量将疑似 AI 素材重新标记为非 AI（人工复核翻案）。 |
| `GET` | `/api/admin/check-duplicate` | `check_duplicate` | 检查指定 SHA-256 的文件是否已存在（上传前去重）；查询在 ``inspiration_service``。 |
| `GET` | `/api/admin/find-duplicates` | `find_duplicates` | 检测完全重复的素材（SHA-256 相同）；查询在 ``admin_stats_service``。 |
| `POST` | `/api/admin/deduplicate` | `deduplicate_files` | 智能去重（已改造为任务队列，创建任务后返回 task_id）。 |
| `GET` | `/api/admin/export` | `export_inspirations` | 导出全部未删除素材为 CSV（UTF-8 BOM，浏览器直接下载）；内容在 ``admin_stats_service``。 |
| `GET` | `/api/admin/trend` | `inspiration_trend` | 按天统计新增素材数量（近 N 天）；查询在 ``admin_stats_service`` 里。 |
| `GET` | `/api/admin/person-frequency` | `person_frequency` | 按关联素材数量降序返回人物（博主+模特合并）；查询在 ``admin_stats_service``。 |
| `GET` | `/api/admin/audit-logs` | `audit_logs` | 按时间倒序返回破坏性操作审计日志；查询在 ``audit_service``。 |
| `POST` | `/api/admin/near-duplicates` | `near_duplicates` | 检测视觉近似重复的图片素材（感知哈希分组），仅返回候选、不删除。 |
| `POST` | `/api/admin/phash-backfill` | `phash_backfill` | 创建「感知哈希缓存补齐」后台任务（近似重复扫描的前置）。 |
| `GET` | `/api/admin/vector-stats` | `vector_stats` | 向量化状态统计；查询在 ``admin_stats_service``。 |
| `POST` | `/api/admin/vector-backfill` | `vector_backfill` | 一键为缺失向量的素材创建向量回填任务（异步，由 worker 执行）。 |
| `POST` | `/api/admin/crop-phone-screenshots/scan` | `crop_phone_screenshots_scan` | 扫描手机图剪裁候选（只读）：手动上传竖屏截图清单 + 逐张裁剪信息，供人工确认。 |
| `POST` | `/api/admin/crop-phone-screenshots/apply` | `crop_phone_screenshots_apply` | 按勾选确认的素材执行手机图剪裁：备份原图、裁剪替换、重建缩略图/哈希/主色调并入队向量回填。 |

## ai_analysis

前缀：`/api/ai`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/api/ai/analyze/{inspiration_id}` | `analyze_inspiration` | 触发单个素材的 AI 分析（走 worker 任务队列 + GPU 互斥）。 |
| `POST` | `/api/ai/batch-analyze` | `batch_analyze` | 批量触发多个素材的 AI 分析。 |
| `GET` | `/api/ai/queue` | `analysis_queue` | 获取分析队列状态：待分析/分析中/已完成/失败统计。 |
| `GET` | `/api/ai/unanalyzed-ids` | `unanalyzed_ids` | 获取所有未分析过的图片素材 ID 列表（暂不分析视频）。 |
| `GET` | `/api/ai/history` | `analysis_history` | 获取分析历史记录列表（仅标签分析，排除质量审核日志）。 |
| `GET` | `/api/ai/history/export` | `export_analysis_history_csv` | 导出分析历史为 CSV（当前筛选条件，上限 10000 条）。 |
| `POST` | `/api/ai/retry/{inspiration_id}` | `retry_analysis` | 重试失败的分析（走 worker 队列：Ollama 未连接不阻断入队，视为可恢复错误自动重试）。 |
| `POST` | `/api/ai/retry-all-failed` | `retry_all_failed` | 一键重试所有失败的分析（仅取每个素材最新记录为失败的）。 |
| `POST` | `/api/ai/history/batch-delete` | `batch_delete_logs` | 批量删除分析历史记录。 |
| `POST` | `/api/ai/history/batch-retry` | `batch_retry_logs` | 批量重试分析记录：根据日志 ID 找到对应素材并重新分析（走队列，Ollama 离线不阻断）。 |
| `POST` | `/api/ai/retag/{tag_id}` | `retag_analysis_by_tag` | 按标签重新 AI 分析：对该标签下「由 AI 打标」的全部素材按当前提示词强制重跑。 |
| `GET` | `/api/ai/history/model-names` | `get_history_model_names` | 获取分析历史中出现过的所有模型名称，供前端筛选（排除质量审核日志）。 |
| `GET` | `/api/ai/history/prompt-versions` | `get_history_prompt_versions` | 获取分析历史中出现过的所有提示词版本（内容哈希）及记录数，供前端筛选。 |
| `GET` | `/api/ai/prompt-options` | `get_prompt_options` | 获取组合分析可选的提示词列表（当前默认 + 历史保存版本）。 |
| `DELETE` | `/api/ai/history/failed/all` | `delete_all_failed_logs` | 批量删除所有失败的分析日志。 |
| `GET` | `/api/ai/history/{log_id}` | `get_analysis_detail` | 获取单条分析日志的详细信息，包含原始 AI 响应和关联标签。 |
| `DELETE` | `/api/ai/history/{log_id}` | `delete_analysis_log` | 删除指定分析日志。 |
| `GET` | `/api/ai/compare/{inspiration_id}` | `compare_analyses` | 获取同一素材的所有历史分析结果，用于并排对比。 |
| `POST` | `/api/ai/compare-batch` | `compare_analyses_batch` | 按日志 ID 批量对比多条分析记录（需为同一素材，至少 2 条）。 |
| `POST` | `/api/ai/history/{log_id}/apply` | `apply_analysis_log_to_material` | 把某条分析记录提取的标签应用到素材（覆盖 AI 标签，保留手动标签）。 |

## ai_dashboard

前缀：`/api/ai`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/ai/quality-dashboard` | `quality_dashboard` | 分析质量总览：每日趋势、问题素材、标签覆盖率（聚合在 ai_dashboard_service）。 |
| `GET` | `/api/ai/prompt-quality` | `prompt_quality` | 提示词版本质量对比：成功率 / 平均标签数 / 纠错率（可选裸词率）。 |
| `GET` | `/api/ai/tag-corrections` | `list_tag_corrections` | 纠错反馈记录列表（分页，按时间倒序）。 |
| `GET` | `/api/ai/tag-corrections/stats` | `tag_correction_stats` | 纠错统计：按原因 / 动作 / 提示词版本聚合（数据洞察页消费）。 |
| `POST` | `/api/ai/test-analyze` | `test_analyze` | 单图即时测试：使用当前模型和参数分析图片，SSE 流式返回结果。 |

## ai_models

前缀：`/api/ai`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/ai/models` | `list_models` | 列出所有已安装的 Ollama 模型，含大小和修改时间，并标注视觉/嵌入角色。 |
| `GET` | `/api/ai/status` | `ai_status` | AI 服务状态：Ollama 连接、版本号、运行时长、活跃视觉模型与文本嵌入模型。 |
| `POST` | `/api/ai/models/pull` | `pull_model` | 拉取新模型（SSE 流式返回下载进度）。 |
| `GET` | `/api/ai/models/{model_name:path}/detail` | `model_detail` | 获取模型完整元信息（调用 Ollama /api/show）。 |
| `POST` | `/api/ai/models/{model_name:path}/update` | `update_model` | 更新已安装模型到最新版（对同 tag 执行 pull，SSE 流式返回进度）。 |
| `POST` | `/api/ai/models/copy` | `copy_model` | 复制模型（调用 Ollama /api/copy）。 |
| `DELETE` | `/api/ai/models/{model_name:path}` | `delete_model` | 删除指定模型。 |
| `PUT` | `/api/ai/models/active` | `set_active_model` | 切换活跃视觉模型。 |
| `PUT` | `/api/ai/models/embedding-active` | `set_embedding_model` | 切换文本嵌入模型（向量检索文本侧使用，持久化到 .env）。 |
| `GET` | `/api/ai/gpu-stats` | `gpu_stats` | 获取 GPU 显存占用和已加载模型信息（聚合逻辑在 app.services.gpu_service）。 |
| `POST` | `/api/ai/unload-model` | `unload_model` | 卸载指定模型释放显存（通知 Ollama 不再 keep alive）。 |
| `GET` | `/api/ai/model-stats` | `model_stats` | 获取按模型聚合的分析统计：每个模型的分析次数、成功率、平均耗时、平均标签数。 |

## ai_outfit

前缀：`/api/ai`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/api/ai/outfit-tags/suggest` | `suggest_outfit_tags` | 根据素材的小标签，AI 建议穿搭大标签（只建议，不入库）。 |

## ai_quality

前缀：`/api/ai`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/ai/manual-upload-auto-approve` | `get_manual_upload_auto_approve` | 获取手动上传素材是否默认免审核的配置。 |
| `PUT` | `/api/ai/manual-upload-auto-approve` | `set_manual_upload_auto_approve` | 设置手动上传素材是否默认免审核。 |
| `POST` | `/api/ai/quality-check` | `batch_quality_check` | 批量审核图片素材。 |
| `POST` | `/api/ai/quality-recheck` | `recheck_quality` | 重新审核所有已通过（approved）的图片素材。 |
| `GET` | `/api/ai/quality-stats-by-source` | `quality_stats_by_source` | 按素材来源统计审核通过率，用于发现「哪个采集源/平台质量差」。 |
| `POST` | `/api/ai/quality-corrections` | `create_quality_correction` | 提交一次「AI 判错了」的反馈（审查 P0 误判反馈闭环）。 |
| `GET` | `/api/ai/quality-corrections` | `list_quality_corrections` | 分页列出误判反馈（按时间倒序）。 |
| `GET` | `/api/ai/quality-corrections/stats` | `quality_correction_stats` | 误判反馈统计：按错误类型与「原判定→纠正」方向分组。 |
| `GET` | `/api/ai/quality-export.csv` | `export_quality_csv` | 导出质量审核结果 CSV（上限 10000 条，按审核时间倒序）。 |
| `GET` | `/api/ai/quality-prompt-ab` | `quality_prompt_ab` | 按 prompt 指纹对比审核结果，回答「换 prompt 后通过率是不是变了」。 |
| `GET` | `/api/ai/quality-gray` | `quality_gray` | 列出「灰区」素材供**人工优先复审**：初筛器最没把握的那批。 |
| `GET` | `/api/ai/quality-stats` | `quality_stats` | 质量审核统计：待审核/已通过/已拒绝数量及通过率（图片 + 视频；视频按首帧审核）。 |

## ai_reset

前缀：`/api/ai`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `DELETE` | `/api/ai/reset` | `reset_all_data` | 重置所有数据：清空数据库所有表 + 删除存储文件。 |

## ai_settings

前缀：`/api/ai`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/ai/prompt/versions` | `prompt_versions` | 获取当前模型的 prompt 版本历史列表。 |
| `POST` | `/api/ai/prompt/rollback` | `rollback_prompt` | 回滚当前模型的 prompt 到指定版本。请求体: {"index": 0} 其中 index 0 = 最新版本 |
| `POST` | `/api/ai/prompt/save-version` | `save_prompt_version` | 将当前模型的 prompt 保存为一个版本（用于回滚和对比）。 |
| `GET` | `/api/ai/prompt` | `get_prompt` | 获取当前模型 AI 分析使用的 prompt 文本。 |
| `PUT` | `/api/ai/prompt` | `update_prompt` | 更新当前模型的 AI 分析 prompt（按模型持久化到 prompt_configs.json）。 |
| `GET` | `/api/ai/settings` | `get_ai_settings` | 获取当前 AI 参数配置（超时按当前模型独立），附全局默认值供前端「恢复默认」。 |
| `PUT` | `/api/ai/settings` | `update_ai_settings` | 更新 AI 参数。 |
| `GET` | `/api/ai/sampling-params` | `get_sampling_params` | 获取当前模型的 AI 采样参数（temperature, top_p, top_k, num_predict, num_ctx, think）。 |
| `PUT` | `/api/ai/sampling-params` | `update_sampling_params` | 更新当前模型的 AI 采样参数（按模型独立持久化）。 |
| `DELETE` | `/api/ai/model-config` | `reset_model_config_endpoint` | 清除当前活跃模型的自定义配置（model_configs.json 中的覆盖项）。 |
| `GET` | `/api/ai/model-config/overview` | `model_config_overview` | 返回每模型的参数/Prompt 自定义配置总览（哪些模型有覆盖、覆盖哪些字段）。 |
| `POST` | `/api/ai/model-config/copy` | `copy_model_config_endpoint` | 把某模型的参数与 Prompt 复制到另一模型。请求体: {"source": ..., "destination": ...} |
| `GET` | `/api/ai/person-bio-prompt` | `get_person_bio_prompt_endpoint` | 获取人物简介生成 Prompt 与默认值（供设置页编辑与「恢复默认」）。 |
| `PUT` | `/api/ai/person-bio-prompt` | `update_person_bio_prompt_endpoint` | 更新人物简介生成 Prompt（持久化到 person_bio_prompt.json）。 |
| `DELETE` | `/api/ai/person-bio-prompt` | `reset_person_bio_prompt_endpoint` | 删除自定义 Prompt，回退到默认模板。 |

## bloggers

前缀：`/api/bloggers`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/bloggers` | `list_bloggers` | 分页获取博主列表，支持名称搜索、平台筛选与人脸检测约束。 |
| `POST` | `/api/bloggers` | `create_blogger` | 创建穿搭博主。 |
| `POST` | `/api/bloggers/import-csv` | `import_bloggers_csv` | 上传 CSV 批量导入博主（按 xhs_id upsert，昵称/小红书号必填）。 |
| `POST` | `/api/bloggers/enrich-missing-profile` | `enrich_missing_profile` | 创建「博主资料补全」异步任务（人物管理页一键补全）。 |
| `POST` | `/api/bloggers/avatars/fill-missing` | `fill_missing_avatars` | 创建「博主头像批量补全」异步任务（人物管理页一键补头像）。 |
| `GET` | `/api/bloggers/missing-profile` | `missing_profile_bloggers` | 资料有缺口的博主列表（人物管理页补全功能用）。 |
| `POST` | `/api/bloggers/enrich-skip` | `enrich_skip` | 手动跳过补全（确定性无法获取信息的博主，从缺失列表移除）。 |
| `POST` | `/api/bloggers/enrich-unskip` | `enrich_unskip` | 解除跳过（博主重新纳入补全范围）。 |
| `GET` | `/api/bloggers/enrich-skips` | `enrich_skips` | 已跳过补全的博主列表（含原因，供前端管理与解除）。 |
| `GET` | `/api/bloggers/stats` | `blogger_stats` | 博主数量统计：总数 + 各平台分布（穿搭博主页顶部统计卡片）。 |
| `GET` | `/api/bloggers/ip-stats` | `blogger_ip_stats` | 按 IP 属地分组统计博主数量（空属地归入「未知」）。 |
| `GET` | `/api/bloggers/top` | `top_bloggers` | 按素材数倒序返回热门博主排行。 |
| `GET` | `/api/bloggers/suggestions` | `suggest_bloggers` | 按名称模糊匹配博主（用于前端选择去重）。 |
| `POST` | `/api/bloggers/groups/link` | `link_bloggers_group_api` | 把两个博主绑定为同一人（新建组），或把博主并入已有组。 |
| `POST` | `/api/bloggers/groups/unlink` | `unlink_blogger_group_api` | 把博主移出人物组（变独立账号）；组内仅剩 1 个账号时自动清理组。 |
| `POST` | `/api/bloggers/groups/{group_id}/set-primary` | `set_primary_blogger_api` | 手动指定组内主账号（列表折叠展示位）。 |
| `GET` | `/api/bloggers/groups/{group_id}` | `group_info_api` | 查询人物组完整信息（组内各账号 + 主账号）。 |
| `POST` | `/api/bloggers/batch/promote` | `batch_promote_api` | 批量把「自动登记」的博主纳入追踪（source 改回 manual）。 |
| `POST` | `/api/bloggers/batch/delete` | `batch_delete_api` | 批量删除博主（**逐条校验**：有素材关联的一律拒绝并给出原因）。 |
| `GET` | `/api/bloggers/avatars/coverage` | `avatar_coverage_api` | 头像覆盖率：``{"total", "with_avatar", "fillable"}``（人物管理页统计卡片用）。 |
| `POST` | `/api/bloggers/avatars/clear` | `clear_avatars_api` | 批量清除头像。**必须明确指定范围**：``{"ids": [...]}`` 或 ``{"all": true}``。 |
| `GET` | `/api/bloggers/duplicate-names` | `duplicate_names_api` | 同名博主建议（供合并/删除）：归一化昵称相同的记录分组返回。 |
| `GET` | `/api/bloggers/rating-priority` | `blogger_rating_priority` | 人物优先评分队列：按「已评占比升序」返回还有未评分图片素材的博主。 |
| `GET` | `/api/bloggers/{blogger_id}` | `get_blogger` | 获取博主详情（含素材数与风格画像）。 |
| `PATCH` | `/api/bloggers/{blogger_id}` | `update_blogger` | 更新博主信息（部分更新；显式传 null 的字段会被清空）。 |
| `POST` | `/api/bloggers/{blogger_id}/resolve-douyin-profile` | `resolve_douyin_profile_api` | 解析抖音博主的主页标识（sec_user_id + 主页链接），供「下载所有作品」直接可用。 |
| `GET` | `/api/bloggers/{blogger_id}/face-candidates` | `blogger_face_candidates` | 列出该博主的「人脸注册候选」：已匹配素材 + 未匹配候选脸聚类。 |
| `POST` | `/api/bloggers/{blogger_id}/enrich-profile` | `enrich_blogger_profile_api` | 按 f2 用户库补齐单个博主的资料卡（离线：IP 属地 + 粉丝/关注/作品/获赞快照）。 |
| `POST` | `/api/bloggers/{blogger_id}/promote` | `promote_blogger` | 把「自动登记」的博主纳入追踪（source 改回 manual）。 |
| `POST` | `/api/bloggers/{blogger_id}/avatar` | `set_blogger_avatar_api` | 设置博主头像（覆盖旧的）：从该博主的素材里选一张，或直接上传一张照片。 |
| `DELETE` | `/api/bloggers/{blogger_id}/avatar` | `clear_blogger_avatar_api` | 清除头像（avatar_path 置空，前端回退首字占位），同时删除头像文件。 |
| `DELETE` | `/api/bloggers/{blogger_id}` | `delete_blogger` | 删除博主：仅当该博主无关联素材时允许删除，否则返回 400。 |
| `GET` | `/api/bloggers/{blogger_id}/inspirations` | `blogger_inspirations` | 获取该博主的素材列表（分页 + 排序 + 筛选，排除软删除素材）。 |
| `GET` | `/api/bloggers/{blogger_id}/random` | `blogger_random_inspirations` | 随机取该博主的若干素材（详情页「随机浏览」）。 |
| `GET` | `/api/bloggers/{blogger_id}/works` | `blogger_works` | 按「作品」分组分页（同一抖音作品的 6~9 张图算一组）。 |
| `GET` | `/api/bloggers/{blogger_id}/insights` | `blogger_insights` | 人物洞察：近 12 个月新增、质量构成、媒体构成、首末素材时间。 |
| `GET` | `/api/bloggers/{blogger_id}/similar` | `blogger_similar` | 风格相近的博主（标签集合余弦相似度）+ 疑似重复账号（归一化同名）。 |
| `POST` | `/api/bloggers/{blogger_id}/generate-bio` | `generate_blogger_bio` | 根据该博主的标签画像调用本地大模型生成一段简介（不自动入库，由前端确认后保存）。 |
| `POST` | `/api/bloggers/{blogger_id}/face` | `register_blogger_face_api` | 注册/重新注册博主人脸：上传照片与/或选择已关联素材（合计 1~5 张）。 |
| `GET` | `/api/bloggers/{blogger_id}/face` | `blogger_face_status_api` | 查询博主人脸注册状态。 |
| `DELETE` | `/api/bloggers/{blogger_id}/face` | `unregister_blogger_face_api` | 注销博主人脸：删除人脸特征、回退所有人脸匹配记录、解除全部素材归属。 |
| `POST` | `/api/bloggers/{blogger_id}/unbind-inspirations` | `unbind_blogger_inspirations_api` | 解除博主与素材的归属（保留人脸特征）。 |
| `POST` | `/api/bloggers/{blogger_id}/bind-face/{inspiration_id}` | `bind_uploaded_face_api` | 上传选了博主后，后台检测素材人脸并把「主脸」直接绑定到该博主（异步）。 |

## collections

前缀：`/api/collections`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/collections` | `list_collections` | 获取合集列表（一级按 position 升序，二级紧随其父）。 |
| `POST` | `/api/collections` | `create_collection` | 创建合集（同层重名 409）；带 query_json 时创建智能合集。 |
| `PATCH` | `/api/collections/order` | `reorder_collections` | 提交合集列表顺序（拖拽排序）：按 ordered_ids 顺序重排 position。 |
| `PATCH` | `/api/collections/{collection_id}` | `update_collection` | 更新合集基础信息（部分更新）。 |
| `DELETE` | `/api/collections/{collection_id}` | `delete_collection` | 删除合集（不动素材：手动合集级联删成员关系，智能合集仅删自身）。 |
| `GET` | `/api/collections/{collection_id}/inspirations` | `list_collection_inspirations` | 分页获取合集内容（支持搜索 / 筛选 / 排序）。 |
| `POST` | `/api/collections/{collection_id}/inspirations` | `add_inspirations` | 批量加入素材到手动合集（请求内去重，已加入/不存在的素材跳过）。 |
| `POST` | `/api/collections/{source_id}/inspirations/transfer` | `transfer_inspirations` | 批量移动/复制合集成员到多个目标合集（整单事务）。 |
| `POST` | `/api/collections/{target_id}/merge` | `merge_collections` | 把多个源合集合并进目标合集，成功后删除源合集（整单事务）。 |
| `DELETE` | `/api/collections/{collection_id}/inspirations` | `remove_inspirations` | 批量移出素材（不在合集内的 ID 跳过）；智能合集返回 400。 |
| `PATCH` | `/api/collections/{collection_id}/items/order` | `reorder_collection_items` | 提交合集内成员展示顺序（拖拽编排）：按 ordered_ids 重排 position。 |
| `POST` | `/api/collections/{collection_id}/solidify` | `solidify_collection` | 智能合集转手动：把当前匹配素材按当前位置固化并清空 query_json。 |

## face_scan

前缀：`/api/face-match、/api/face-scan`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/api/face-scan/start` | `start_scan` | 创建人脸库扫描任务（增量/全量），返回 task_id。 |
| `POST` | `/api/face-match/run` | `run_match` | 创建全库候选匹配任务（可限定人物范围），返回 task_id。 |
| `GET` | `/api/face-scan/service-status` | `face_service_status` | 人脸识别子服务可用性（供前端**事前**提示，而不是点了才报错）。 |
| `GET` | `/api/face-scan/task` | `scan_task` | 最近一次扫描/匹配任务状态（供扫描页轮询）。 |
| `GET` | `/api/face-scan/results` | `scan_results` | 查询候选/已确认结果。 |
| `GET` | `/api/face-scan/detections/{detection_id}/face` | `detection_face_crop` | 按检测框裁出人脸小图（候选审核时与来源素材并排查看）。 |
| `POST` | `/api/face-scan/register-plan` | `register_plan` | 批量注册候选计划（只读）：未注册博主 + 未匹配单脸簇 + 建议注册素材。 |
| `POST` | `/api/face-scan/register-batch` | `register_batch` | 批量注册博主人脸（串行，最多 20 位/批）；子服务不可用时快速失败。 |
| `POST` | `/api/face-scan/confirm` | `confirm` | 审核确认 / 驳回 / 撤销候选。 |
| `POST` | `/api/face-scan/cluster/run` | `cluster_run` | 创建人脸聚合聚类任务（异步执行），返回 task_id。 |
| `GET` | `/api/face-scan/cluster/task` | `cluster_task` | 最近一次人脸聚合聚类任务状态（供扫描页轮询进度与结果）。 |
| `GET` | `/api/face-scan/cluster/groups` | `cluster_groups` | 分页返回人脸聚合分组（来自最近一次成功的聚类任务结果）。 |
| `POST` | `/api/face-scan/cluster/groups/{group_id}/ignore` | `cluster_group_ignore` | 整组忽略：把组内未匹配人脸标记为「不匹配」，永久退出后续匹配。 |
| `POST` | `/api/face-scan/cluster/groups/{group_id}/create-person` | `cluster_group_create_person` | 按聚类分组新建博主，并用组内最高质量的人脸注册（随后整组确认关联）。 |
| `GET` | `/api/face-scan/cluster/groups/{group_id}/detections` | `cluster_group_detections` | 某聚合组的人脸明细分页（含素材路径/缩略图，供展开网格展示与勾选指派）。 |

## favorites

前缀：`/api/favorites`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/favorites/views` | `favorites_views` | 收藏智能视图列表（四个固定视图 + 实时计数）。 |
| `GET` | `/api/favorites/views/{key}` | `favorites_view_items` | 分页获取某个收藏智能视图的素材（按收藏时间倒序、id 兜底）。 |
| `GET` | `/api/favorites/cleanup` | `favorites_cleanup` | 收藏整理工作台数据（P1，2026-11-19）。 |
| `GET` | `/api/favorites/overview` | `favorites_overview` | 收藏中心概览：总数 / 近 7 天新增 / 未评分 / 未整理 / 最近收藏。 |
| `GET` | `/api/favorites/history` | `favorites_history` | 最近收藏/取消事件流水（按事件时间倒序，最多 ``limit`` 条）。 |

## files

前缀：`/api/files`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/files/keyframes/{inspiration_id}` | `list_keyframes` | 返回视频素材的关键帧 URL 列表（按时间序）。 |
| `GET` | `/api/files/{file_path:path}` | `serve_file` | 提供存储文件的访问。例如 /api/files/images/2026-08/abc123.jpg |

## generate

前缀：`/api/generate`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/generate/status` | `generate_status` | 就绪状态：ComfyUI 是否可用、哪些模型就位、Ollama 占了显存。 |
| `GET` | `/api/generate/models` | `generate_models` | 可用模型档位与默认参数（前端填默认值、显示预期耗时）。 |
| `POST` | `/api/generate/runs` | `create_run` | 提交出图批次（立即返回，任务在 worker 里执行）。 |
| `GET` | `/api/generate/runs` | `list_runs` | 生成历史：分页 + 筛选 + 列表增强（缩略图条 / 已入库张数 / 目录是否已清理）。 |
| `PATCH` | `/api/generate/runs/{run_id}` | `patch_run` | 更新批次的用户标注：收藏 / 命名 / 备注（收藏的批次不参与过期清理）。 |
| `POST` | `/api/generate/runs/{run_id}/auto-title` | `auto_title` | 用本地模型给这批起一个 ≤20 字的中文标题（返回建议，不写库）。 |
| `GET` | `/api/generate/runs/export` | `export_runs` | 导出生成历史（CSV / JSON），筛选参数与列表接口一致。 |
| `GET` | `/api/generate/runs/stats` | `run_stats` | 近 N 天出图统计（历史区顶部数字卡）：批次/张数/入库率/平均耗时/失败率/档位分布。 |
| `GET` | `/api/generate/runs/{run_id}` | `get_run` | 批次详情 + 候选图列表（含是否已入库）。 |
| `GET` | `/api/generate/runs/{run_id}/candidates/{filename}` | `get_candidate` | 读取候选图文件（原图或缩略图）。 |
| `POST` | `/api/generate/runs/{run_id}/confirm` | `confirm_import` | 把选中的候选图入库（生成图来源=generated，标记疑似 AI 生成，默认待审核）。 |
| `POST` | `/api/generate/runs/{run_id}/retry` | `retry_run` | 同参数再生成（新建批次，历史保留）。 |
| `POST` | `/api/generate/runs/{run_id}/regenerate` | `regenerate_candidate` | 单张换 seed 重出（新建只含 1 张的批次，不动原批次）。 |
| `POST` | `/api/generate/runs/cleanup` | `cleanup_runs` | 清理过期候选目录（不动已入库素材与历史记录）。 |
| `POST` | `/api/generate/runs/{run_id}/cancel` | `cancel_run` | 取消批次：把队列任务标记为 cancelled（已出的图保留在候选区）。 |
| `DELETE` | `/api/generate/runs/{run_id}` | `discard_run` | 丢弃批次：删除候选目录（不影响已入库素材）。 |
| `POST` | `/api/generate/prompt/preview` | `preview_prompt` | 从素材的标签/正文/主色反推出图 prompt（分段返回，前端可逐段删改）。 |

## health

前缀：`/api/health`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/health/services` | `services_health` | 返回各服务（后端/前端/worker）健康状态、资源占用与告警列表。 |

## inspirations

前缀：`/api/inspirations`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/inspirations/check-platform-id` | `check_platform_id` | 查询平台 ID 是否已有未删除素材（浏览器插件上传前查重）。 |
| `POST` | `/api/inspirations/{inspiration_id}/face-detect` | `face_detect_api` | 检测素材中的人脸并与博主特征库匹配（重新检测覆盖旧结果）。 |
| `GET` | `/api/inspirations/{inspiration_id}/face-detections` | `face_detections_api` | 素材人脸检测结果列表（含匹配博主）。 |
| `PUT` | `/api/inspirations/{inspiration_id}/face-detections/{detection_id}` | `face_detection_update_api` | 手动指定/解除人脸检测的人物关联（兼容两种 body 格式）。 |
| `DELETE` | `/api/inspirations/{inspiration_id}/face-detections/{detection_id}` | `face_detection_delete_api` | 删除单条人脸检测记录。 |
| `POST` | `/api/inspirations` | `create_inspiration` | 上传图片并创建灵感素材。AI 分析在后台异步执行。 |
| `POST` | `/api/inspirations/from-url` | `create_from_url` | 从 URL 下载图片并创建素材。 |
| `GET` | `/api/inspirations` | `list_inspirations` | 分页获取灵感列表，支持多维筛选和排序。 |
| `GET` | `/api/inspirations/dominant-colors` | `dominant_colors` | 返回库内实际出现的主色调（hex）及出现次数，供颜色筛选下拉展示。 |
| `DELETE` | `/api/inspirations/quality-rejected` | `delete_rejected_inspirations` | 将全部质量审核被拒绝（rejected）的素材批量移入垃圾桶（软删除，可恢复）。 |
| `GET` | `/api/inspirations/trash` | `list_trash` | 分页获取垃圾桶中的素材（软删除，可恢复）。 |
| `DELETE` | `/api/inspirations/trash` | `empty_trash` | 彻底清空垃圾桶（物理删除文件与数据库记录）。 |
| `POST` | `/api/inspirations/{inspiration_id}/trash` | `move_to_trash` | 将素材移入垃圾桶（软删除），文件移入 storage/trash/，可恢复。 |
| `POST` | `/api/inspirations/{inspiration_id}/restore` | `restore_inspiration` | 从垃圾桶恢复素材（移回媒体目录，清除软删除标记）。 |
| `GET` | `/api/inspirations/{inspiration_id}/favorite-history` | `favorite_history` | 获取素材的收藏/取消收藏历史（按时间倒序，最多 limit 条）。 |
| `GET` | `/api/inspirations/{inspiration_id}` | `get_inspiration` | 获取单个灵感详情（包含完整标签和分析日志）。 |
| `PATCH` | `/api/inspirations/{inspiration_id}` | `update_inspiration` | 更新灵感（收藏状态、作者等部分字段）。 |
| `POST` | `/api/inspirations/{inspiration_id}/crop` | `crop_inspiration` | 手动裁剪素材图片（保留中间区域，裁掉上下部分），就地替换原图。 |
| `POST` | `/api/inspirations/batch-tags` | `batch_add_tags` | 批量给多个素材关联标签（按名称查找或创建，已关联的自动跳过）。 |
| `POST` | `/api/inspirations/batch-favorite` | `batch_favorite` | 批量收藏/取消收藏素材（仅作用于未删除素材）。 |
| `POST` | `/api/inspirations/batch-trash` | `batch_trash` | 批量将素材移入垃圾桶（软删除，可恢复）。 |
| `POST` | `/api/inspirations/batch-update` | `batch_update` | 批量编辑素材元数据（来源/收藏/审核状态/疑似 AI 标记/评分），仅更新显式字段。 |
| `POST` | `/api/inspirations/{inspiration_id}/tags` | `add_inspiration_tags` | 手动给素材关联标签（按名称查找或创建，已关联的自动跳过）。 |
| `DELETE` | `/api/inspirations/{inspiration_id}/tags/{tag_id}` | `remove_inspiration_tag` | 解除素材与某个标签的关联（不删除标签本身）。 |
| `POST` | `/api/inspirations/{inspiration_id}/tag-corrections` | `record_tag_correction` | 记录 AI 打标纠错反馈（素材详情页「标错了」）。 |
| `POST` | `/api/inspirations/batch-bloggers` | `batch_link_bloggers` | 批量给多个素材关联穿搭博主（素材库批量选择场景）。 |
| `POST` | `/api/inspirations/{inspiration_id}/bloggers` | `link_inspiration_bloggers` | 给素材批量关联穿搭博主（幂等，已关联自动跳过）。 |
| `DELETE` | `/api/inspirations/{inspiration_id}/bloggers/{blogger_id}` | `unlink_inspiration_blogger` | 解除素材与某位博主的关联（不删除博主本身）。 |
| `POST` | `/api/inspirations/{inspiration_id}/models` | `link_inspiration_models` | 给素材批量关联职业模特（幂等，已关联自动跳过）。 |
| `DELETE` | `/api/inspirations/{inspiration_id}/models/{model_id}` | `unlink_inspiration_model` | 解除素材与某位模特的关联（不删除模特本身）。 |
| `DELETE` | `/api/inspirations/{inspiration_id}` | `delete_inspiration` | 彻底删除灵感素材（物理删除文件与数据库记录，不可恢复）。 |

## models

前缀：`/api/models`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/models` | `list_models` | 分页获取模特列表，支持名称搜索、平台筛选。 |
| `POST` | `/api/models` | `create_model` | 创建职业模特。 |
| `GET` | `/api/models/ip-stats` | `model_ip_stats` | 按 IP 属地分组统计模特数量（空属地归入「未知」），与博主统计对称。 |
| `GET` | `/api/models/top` | `top_models` | 按素材数倒序返回热门模特排行。 |
| `GET` | `/api/models/suggestions` | `suggest_models` | 按名称模糊匹配模特（用于前端选择去重）。 |
| `GET` | `/api/models/{model_id}` | `get_model` | 获取模特详情（含素材数与风格画像）。 |
| `PATCH` | `/api/models/{model_id}` | `update_model` | 更新模特信息（部分更新；显式传 null 的字段会被清空）。 |
| `DELETE` | `/api/models/{model_id}` | `delete_model` | 删除模特：仅当该模特无关联素材时允许删除，否则返回 400。 |
| `GET` | `/api/models/{model_id}/inspirations` | `model_inspirations` | 获取该模特的素材列表（分页 + 排序，排除软删除素材）。 |
| `POST` | `/api/models/{model_id}/generate-bio` | `generate_model_bio` | 根据该模特的标签画像调用本地大模型生成一段简介（不自动入库，由前端确认后保存）。 |
| `GET` | `/api/models/{model_id}/photo-sets` | `list_photo_sets` | 分页获取模特照片组（含照片数与封面）。 |
| `POST` | `/api/models/{model_id}/photo-sets` | `create_photo_set` | 创建模特照片组（组名缺省回退「未命名照片组」）。 |
| `GET` | `/api/models/{model_id}/photo-sets/{set_id}` | `get_photo_set` | 获取照片组详情（含分页照片列表）。 |
| `PATCH` | `/api/models/{model_id}/photo-sets/{set_id}` | `update_photo_set` | 更新照片组名称。 |
| `DELETE` | `/api/models/{model_id}/photo-sets/{set_id}` | `delete_photo_set` | 删除照片组（级联删除照片与物理文件）。 |
| `POST` | `/api/models/{model_id}/photo-sets/{set_id}/photos` | `add_photo_to_set` | 上传一张照片到照片组（组内内容去重）。 |
| `DELETE` | `/api/models/{model_id}/photo-sets/{set_id}/photos/{photo_id}` | `delete_photo` | 删除照片组内的单张照片（校验照片归属 set_id，防跨组误删）。 |
| `POST` | `/api/models/{model_id}/face` | `register_face` | 从模特写真照片组注册/重新注册人脸特征（默认 Top-5 平均池化）。 |
| `GET` | `/api/models/{model_id}/face` | `face_status` | 查询模特人脸特征注册状态。 |

## pose_gallery

前缀：`/api/pose-gallery`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/pose-gallery/meta` | `pose_gallery_meta` | 姿势库元信息：总数与体系/分类/角度筛选项（前端下拉用）。 |
| `GET` | `/api/pose-gallery` | `list_poses` | 分页查询姿势（换姿势选择器用）。 |
| `GET` | `/api/pose-gallery/random` | `random_pose` | 按当前筛选随机返回一个姿势（选择器「🎲 随机」按钮用）。 |
| `GET` | `/api/pose-gallery/{pose_id}/prompt` | `get_pose_prompt` | 取该姿势已反推的提示词（`pose_prompts` 表；图生图自动使用）。 |
| `GET` | `/api/pose-gallery/{pose_id}` | `get_pose` | 按 id 取一条姿势（生成历史「带入表单」恢复已选姿势用）。 |

## quality_learner

前缀：`/api/ai/quality-learner`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/ai/quality-learner/status` | `learner_status` | 返回初筛器状态（是否已训练、指标、阈值）与当前正负样本统计。 |
| `POST` | `/api/ai/quality-learner/train` | `learner_train` | 用当前正负样本训练/重训初筛器，返回训练指标与样本统计。 |
| `POST` | `/api/ai/quality-learner/reset` | `learner_reset` | 删除已训练模型，回滚到纯 VLM 审核（指标变差时使用）。 |

## scraper

前缀：`/api/scraper`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/scraper/sources` | `scraper_sources` | 列出所有可用的采集源及其状态。 |
| `POST` | `/api/scraper/f2-import` | `create_f2_import` | 一键获取素材：调 f2 增量下载抖音作品 → 去重 → 入库。 |
| `GET` | `/api/scraper/f2-preview` | `f2_preview` | 入库前预览：返回「新增 N 个文件 / M 个作品、跳过明细（含原因）」，**不写库**。 |
| `GET` | `/api/scraper/f2-status` | `f2_status` | 「一键获取素材」状态：可用性 + 每日自动获取的配置与到期信息。 |
| `GET` | `/api/scraper/f2-collects` | `f2_collects` | **先扫描**：列出抖音收藏夹（夹名 / 夹 ID / 夹内作品数），只读、不下载任何媒体。 |
| `GET` | `/api/scraper/f2-authors` | `f2_authors` | 列出 f2 用户库里的账号，并标出哪些在「一键获取素材」的下载白名单里。 |
| `GET` | `/api/scraper/f2-tasks/{task_id}/results` | `f2_task_results` | 浏览某次「一键获取素材」任务产出的素材（供采集管理页的结果与审查面板）。 |
| `GET` | `/api/scraper/f2-tasks/{task_id}/rollback/preview` | `f2_task_rollback_preview` | 预览「回滚本批」（只读）：返回可删/保留数量与保留原因，**不删任何数据**。 |
| `POST` | `/api/scraper/f2-tasks/{task_id}/rollback` | `f2_task_rollback` | 执行「回滚本批」：**物理删除**本批素材（库行 + 文件 + 缩略图 + 关键帧）。 |
| `POST` | `/api/scraper/f2-tasks/{task_id}/results/trash` | `f2_task_results_trash` | 把本批素材移入垃圾桶（软删除，可在垃圾桶恢复；同时作为负样本）。 |
| `POST` | `/api/scraper/f2-tasks/{task_id}/results/restore` | `f2_task_results_restore` | 还原本批已在垃圾桶的素材（逐条复用单条恢复逻辑，单条失败不影响其余）。 |
| `POST` | `/api/scraper/f2-tasks/{task_id}/results/delete` | `f2_task_results_delete` | 彻底删除本批素材（不可恢复）：创建 batch_delete 任务，由 worker 执行。 |
| `POST` | `/api/scraper/f2-tasks/{task_id}/results/register-bloggers` | `f2_task_results_register_bloggers` | 把本批未绑定博主的来源作者补登记为抖音博主并绑定素材（幂等，可重复点）。 |
| `PUT` | `/api/scraper/f2-auto` | `set_f2_auto` | 开关 f2 每日自动获取素材（f2 增量下载 → 去重 → 入库）。 |
| `PUT` | `/api/scraper/f2-like-user` | `set_f2_like_user` | 保存「我的喜欢」用的主页链接。 |
| `PUT` | `/api/scraper/f2-like-max-counts` | `set_f2_like_max_counts` | 保存「我的喜欢」每次最多翻多少条点赞作品。 |
| `GET` | `/api/scraper/hashtags` | `scraper_hashtags` | 采集话题标签存档：详情页提取的 #话题 全局去重统计。 |
| `GET` | `/api/scraper/stats` | `scraper_stats` | 采集任务统计看板：近 N 天的总量/成功率/按平台与按日分布。 |
| `GET` | `/api/scraper/collection-roi` | `collection_roi` | 采集 ROI 漏斗：按关键词 / 博主拆解「采集量 → 入库量 → 质量合格率」。 |
| `GET` | `/api/scraper/cdp-check/{port}` | `check_cdp_endpoint` | 检查指定端口的 Chrome 调试连接是否就绪。 |
| `POST` | `/api/scraper/chrome/start` | `chrome_start` | 由后端拉起采集专用 Chrome（调试模式）。 |
| `POST` | `/api/scraper/chrome/stop` | `chrome_stop` | 停止由后端拉起的采集专用 Chrome（含 taskkill 与等待，走线程池）。 |
| `GET` | `/api/scraper/chrome/status` | `chrome_status` | 查询采集专用 Chrome 的连接状态（端口探测含 socket 超时，走线程池）。 |
| `GET` | `/api/scraper/cookie-status` | `cookie_status` | 检查指定平台的 Cookie 文件状态。 |
| `POST` | `/api/scraper/cookie-verify/{platform}` | `cookie_verify` | 真实校验平台 Cookie 登录态（携带 Cookie 请求平台轻量登录态接口）。 |
| `POST` | `/api/scraper/cookie-import` | `cookie_import` | 导入平台 Cookie（JSON 格式，自动校验平台合法性）。 |
| `DELETE` | `/api/scraper/cookie/{platform}` | `delete_cookie` | 删除指定平台的 Cookie 文件。 |
| `GET` | `/api/scraper/schedules` | `list_schedules` | 列出全部定时采集计划。 |
| `POST` | `/api/scraper/schedules` | `create_schedule` | 创建定时采集计划。 |
| `PATCH` | `/api/scraper/schedules/{schedule_id}` | `update_schedule` | 更新定时采集计划（启用/停用/改间隔/改关键词等）。 |
| `DELETE` | `/api/scraper/schedules/{schedule_id}` | `delete_schedule` | 删除定时采集计划。 |
| `POST` | `/api/scraper/schedules/{schedule_id}/run` | `run_schedule_now` | 立即执行一次定时采集计划。 |
| `POST` | `/api/scraper/extension-tasks` | `create_extension_task` | 浏览器插件采集会话开始：创建任务记录并返回 task_id。 |
| `POST` | `/api/scraper/extension-tasks/{task_id}/complete` | `complete_extension_task` | 浏览器插件采集会话结束：汇总发现/入库数量并标记任务完成。 |
| `GET` | `/api/scraper/tasks/{task_id}/log` | `task_log` | 获取采集任务的日志内容（最近 200 行）。 |
| `POST` | `/api/scraper/tasks/{task_id}/cancel` | `cancel_task` | 取消运行中或等待中的采集任务（发送终止信号给子进程）。 |
| `POST` | `/api/scraper/tasks` | `create_scraper_task` | 创建并启动一个新的采集任务。 |
| `GET` | `/api/scraper/tasks` | `list_scraper_tasks` | 获取采集任务列表，支持筛选、排序与分页（返回 items + total + stats）。 |
| `DELETE` | `/api/scraper/tasks/{task_id}` | `delete_single_task` | 物理删除单条采集任务（素材的 scraper_task_id 自动置 NULL，不删除素材）。 |
| `DELETE` | `/api/scraper/tasks` | `clear_all_scraper_tasks` | 物理删除所有采集任务历史记录。 |
| `POST` | `/api/scraper/tasks/retry-failed` | `retry_failed_scraper_tasks` | 重试所有失败的采集任务，使用相同配置重新创建任务。 |
| `POST` | `/api/scraper/tasks/{task_id}/retry` | `retry_single_task` | 重试单个失败任务，沿用断点续采（不重复采集已处理内容）。 |
| `GET` | `/api/scraper/tasks/{task_id}/results` | `task_results` | 获取指定采集任务产出的素材列表（缩略图网格）。 |
| `POST` | `/api/scraper/tasks/{task_id}/results/batch-delete` | `task_results_batch_delete` | 批量将采集任务产出的指定素材移入垃圾桶（软删除，可恢复）。 |

## search

前缀：`/api/search`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/search` | `search_inspirations` | 按多个维度搜索素材，支持关键词、标签、颜色、日期、来源等组合筛选。 |
| `POST` | `/api/search/vector` | `vector_search` | 语义搜索 / 以图搜图：接受文本或图片，返回 TopK 相似素材。 |
| `GET` | `/api/search/vector/status` | `vector_search_status` | 查询向量检索能力状态（LanceDB / 文本向量 / 图像向量 / 存量向量数量）。 |
| `GET` | `/api/search/similar/{inspiration_id}` | `similar_inspirations` | 根据图像向量 + 标签匹配加权排序寻找相似素材。 |
| `GET` | `/api/search/suggestions` | `search_suggestions` | 根据输入前缀返回匹配的标签名建议；查询在 ``search_suggest_service``。 |
| `GET` | `/api/search/tag-cooccurrence` | `tag_cooccurrence` | 与指定标签常一起出现的其他标签；查询在 ``search_suggest_service``。 |

## tags

前缀：`/api/tags`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/tags` | `list_tags` | 获取所有标签，按类别分组。 |
| `POST` | `/api/tags` | `create_tag` | 手动创建自定义标签。 |
| `PATCH` | `/api/tags/batch-category` | `batch_change_category` | 批量修改标签类别。请求体: {"tag_ids": [1,2,3], "category": "style"} |
| `PATCH` | `/api/tags/batch-rename` | `batch_rename_tags` | 批量重命名标签（查找替换）。请求体: {"tag_ids": [1,2], "find": "白色", "replace": "纯白"} |
| `PATCH` | `/api/tags/{tag_id}` | `update_tag` | 更新标签的名称、类别、置顶、排序或备注。 |
| `DELETE` | `/api/tags/unused` | `delete_unused_tags` | 删除所有使用次数为 0 的标签。 |
| `POST` | `/api/tags/batch-delete` | `batch_delete_tags` | 批量删除标签及其所有关联。 |
| `POST` | `/api/tags/merge` | `merge_tags_endpoint` | 将源标签合并到目标标签，删除源标签。 |
| `POST` | `/api/tags/merge-batch` | `merge_tags_batch` | 把多个标签合并到同一个目标标签（长尾治理用）。 |
| `GET` | `/api/tags/suggestions/{name}` | `tag_suggestions` | 查找与给定名称相似的已有标签（用于去重建议）。 |
| `GET` | `/api/tags/stats` | `tag_stats` | 获取标签统计数据。 |
| `GET` | `/api/tags/duplicates` | `find_duplicate_tags` | 扫描所有标签，找出名称相似度 >= threshold 的标签对。 |
| `POST` | `/api/tags/{tag_id}/inspirations/batch-remove` | `batch_remove_tag_inspirations` | 批量解除标签与多个素材的关联。 |
| `GET` | `/api/tags/{tag_id}/inspirations` | `tag_inspirations` | 获取使用指定标签的素材列表。 |
| `GET` | `/api/tags/export` | `export_tags` | 导出所有标签为 JSON（含类别、来源、使用次数）。 |
| `POST` | `/api/tags/import` | `import_tags` | 批量导入标签（跳过已存在的标签）。 |
| `POST` | `/api/tags/reorder` | `reorder_tags` | 批量更新标签自定义排序权重（sort_order 越小越靠前）。 |
| `GET` | `/api/tags/aliases` | `list_aliases` | 获取所有标签别名（含所属标签名）。 |
| `POST` | `/api/tags/{tag_id}/aliases` | `create_alias` | 为标签添加别名（将别名归一化到该标签）。 |
| `DELETE` | `/api/tags/aliases/{alias_id}` | `delete_alias` | 删除标签别名。 |
| `GET` | `/api/tags/history` | `tag_history_list` | 分页查询标签操作历史（按时间倒序），支持按操作类型 / 标签 / 批次过滤。 |
| `POST` | `/api/tags/history/{history_id}/rollback` | `rollback_tag_history` | 回滚一条标签操作（单条操作级；标签已被后续修改时返回 409 冲突）。 |
| `POST` | `/api/tags/health/scan` | `tag_health_scan` | 提交标签健康度扫描任务（异步执行，返回 task_id 供轮询进度）。 |
| `GET` | `/api/tags/health/{issue_type}` | `tag_health_issues` | 获取最近一次健康度扫描的问题明细（分页）。 |
| `POST` | `/api/tags/clusters/scan` | `tag_clusters_scan` | 提交自动聚类扫描任务（异步执行，返回 task_id 供轮询进度）。 |
| `POST` | `/api/tags/clusters/apply` | `tag_clusters_apply` | 应用选中的候选组：组内合并（可选保留源名为别名），全部写操作历史。 |
| `POST` | `/api/tags/network/analyze` | `tag_network_analyze` | 提交网络图分析任务（异步执行，返回 task_id 供轮询进度）。 |
| `POST` | `/api/tags/batch-edit` | `tag_batch_edit` | 批量高级编辑：正则查找替换 / 前后缀增删 / 格式归一化 / 正则批量合并。 |
| `POST` | `/api/tags/color-strip/dry-run` | `tag_color_strip_dry_run` | 单品标签颜色剥离 dry-run：返回统计与样例，不写库。 |
| `POST` | `/api/tags/color-strip/apply` | `tag_color_strip_apply` | 单品标签颜色剥离执行：剥离颜色前缀重命名/合并并补建颜色关联（单事务）。 |
| `GET` | `/api/tags/tree` | `tag_tree` | 获取层级树某一层的节点（懒加载）；parent_id 缺省/null 表示根节点。 |
| `POST` | `/api/tags/move` | `tag_move` | 批量移动标签层级。请求体: {"moves": [{"tag_id": 5, "parent_id": 12}, |
| `GET` | `/api/tags/effect/trending` | `tag_effect_trending` | 热度升降榜：对比最近 days 天与前一 days 天的素材关联数。 |
| `GET` | `/api/tags/effect/combinations` | `tag_effect_combinations` | 标签组合排行：活跃标签子集内两两共现计数（按次数降序）。 |
| `GET` | `/api/tags/effect/coverage` | `tag_effect_coverage` | 覆盖度统计：素材带标签比例、单素材平均标签数、按类别覆盖率。 |
| `GET` | `/api/tags/effect/source_dist` | `tag_effect_source_dist` | 标签来源分布：每来源标签数/总使用/平均使用 + Top 低效 AI 标签。 |
| `GET` | `/api/tags/cooccurrence-network` | `cooccurrence_network` | 返回使用次数 top-N 标签之间的共现网络（节点 + 加权边）。 |
| `GET` | `/api/tags/alias-suggestions` | `alias_suggestions` | 别名建议：名称相似度落在 ``[min, max)`` 的标签对（「像但不够像重复」的那一段）。 |
| `GET` | `/api/tags/watchlist` | `tag_watchlist` | AI 新建且还没用起来的标签（新标签观察名单）。 |
| `POST` | `/api/tags/watchlist/keep` | `tag_watchlist_keep` | 把观察名单里的标签「确认保留」：source 改为 manual（不会再出现在观察名单）。 |
| `GET` | `/api/tags/top` | `top_tags` | 返回使用次数最多的标签排行。 |
| `GET` | `/api/tags/{tag_id}/cooccurrence` | `tag_cooccurrence` | 某标签「常一起出现的标签」（详情小卡用；只统计未删除素材）。 |
| `GET` | `/api/tags/{tag_id}/trend` | `tag_trend` | 获取标签的使用趋势（按素材创建时间分桶统计）。 |

## tasks

前缀：`/api/tasks`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/tasks` | `list_tasks` | 分页查询任务列表，可按状态 / 类型筛选。 |
| `GET` | `/api/tasks/{task_id}/result` | `get_task_result` | 返回任务完整 result（大字段按需加载；列表/详情默认不再返回）。 |
| `GET` | `/api/tasks/{task_id}` | `get_task` | 查询单个任务状态（供前端轮询进度）。 |
| `GET` | `/api/tasks/{task_id}/runs` | `list_task_runs` | 查询任务的运行台账：每次执行尝试一行（含重试），按开始时间升序。 |
| `POST` | `/api/tasks/{task_id}/cancel` | `cancel_task` | 取消任务： |
| `POST` | `/api/tasks/{task_id}/pause` | `pause_task` | 暂停任务（tag_network_analyze / batch_analyze / multi_analyze / quality_check / |
| `POST` | `/api/tasks/{task_id}/resume` | `resume_task` | 恢复任务（tag_network_analyze / batch_analyze / multi_analyze / quality_check / |
| `DELETE` | `/api/tasks/{task_id}` | `delete_task` | 删除任务记录：终态（success/failed/cancelled）、暂停（paused）与「僵尸 running」可物理删除。 |

## ws

前缀：`/ws`

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| `WS` | `/ws` | `websocket_endpoint` | WebSocket 连接端点：实时推送分析进度和采集状态。 |
