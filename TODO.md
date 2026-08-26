# 待完成功能清单

> 本文档为 AI 穿搭素材库项目的待办事项清单，按优先级排序。
> 状态说明：` ` 未开始，`x` 已完成，`~` 进行中

## 高优先级

### 数据备份与灾难恢复

**背景：** 3000+ 素材的 DB、storage/、lancedb/、.env 均在本地磁盘且被 gitignore。现有 `scripts/backup_data.sh` 只做手动全量备份、无恢复脚本、无校验、无自动调度、无破坏性操作兜底。已通过 wayfinder 完成 8 张规划票（见 `docs/wayfinder/backup-disaster-recovery/MAP.md`），目标为 **L2 级恢复、RPO ≤1 天**：主备份落 `E:\fashion-inspo-backups\`（独立 SSD），单份约 ~1GB（排除可重建的 `_crop_backup` 881MB），恢复即用（缩略图/向量一并备份，不在恢复时重建）。

**总体约束：** 脚本只生成/提供命令，**不自动注册计划任务、不自动启停服务**；注释中文；reset 等核心链路须补后端测试；改完提示用户重启。

**现有资产（保留，不重复造）：** `backup_data.sh` 的 DB 一致性快照（Python `sqlite3.backup`，WAL 下在线安全）与运行时配置循环保留逻辑。

---

#### 块 1：增强备份脚本（T3/T4/T6/T7） ✅ 已完成（53abf4e）

改动：重构 `scripts/backup_data.sh`，新增 `scripts/backup_task.bat`。

1. 目标路径参数化 + 同盘拒绝：必须显式传入目标；缺省或解析为与项目同盘时强警告并非零退出，`--allow-same-disk` 可强跳；`.bat` 默认传 `E:/fashion-inspo-backups`。
2. 并发保护：`backup.lock` 目录锁（Git Bash 用 mkdir 原子判定），获取失败说明已有备份在跑，退出 0 并记日志（防 schtasks 凌晨任务与启动补备撞车）。
3. DB 双重导出：保留 `sqlite3.backup()` → `fashion_inspo.db`；追加 Python `iterdump()` → `fashion_inspo.sql`（不依赖 sqlite3 CLI）。
4. storage 范围（T3 决议）：robocopy `/XD` 排除列表从 `logs tmp` 扩展为 `logs tmp _crop_backup _crop_dups cookies debug faces`；`images/person_photos/trash/videos/thumbnails/person_thumbnails/lancedb` 由 `/E` 自然覆盖；记录源端文件数/字节数，备份后与目标端比对。
5. 校验（T7）：备份 `.db` 跑 `PRAGMA integrity_check`（必须 ok）；`.sql` 用内存库 `executescript` 试导确认可导入；必备目录存在性 + `.env` 必须存在（其余 prompt/model 配置缺失只告警）；生成 `manifest.json`（时间戳、git HEAD、关键表 count、各目录文件数+总字节数）；可选 `--verify-hash` 开启全量 SHA-256（默认关）。
6. 成功/失败标记 + 退出码：全过写 `SUCCESS`（含校验摘要）退出 0；任一失败写 `FAILED` + `backup.log` 原因、非零退出、**不删失败备份**（留证）；日志同时写 `备份目录/backup.log` 与 `backend/storage/logs/backup.log`。
7. rotation（T6）：成功后按目录名 `YYYY-MM-DD_HHMMSS` 清理——最近 7 个日备全留，更早的周日备份留最近 4 个（周备），其余删；优先删带 `FAILED` 的；只删匹配时间戳格式的目录，避免误删手动文件。
8. `backup_task.bat`：绝对路径调 Git Bash 执行脚本并传 E 盘目标，供 schtasks 调用。

**验收：** 手动跑一次产物含 `.db`/`.sql`/`storage/`/配置/`manifest.json`/`SUCCESS`；模拟 DB 损坏/缺目录走 FAILED 路径；用伪造时间戳目录验证 rotation。

---

#### 块 2：恢复脚本 + runbook（T8） ✅ 已完成

新增：`scripts/restore_data.sh`、`scripts/restore_task.bat`、`docs/backup-restore.md`。

**`restore_data.sh <备份目录> [--force] [--allow-overwrite] [--from-sql]`**：
1. 前置检查（不改数据）：目录含 `SUCCESS`（无则拒绝，`--force` 可强跳）；磁盘剩余 ≥ 备份大小 ×1.1；检测端口 18888/进程，服务在运行则**打印停止命令并退出**（不自行 kill）；目标已有数据且无 `--allow-overwrite` 则拒绝。
2. 恢复前快照：现有 `fashion_inspo.db`（含 `-wal/-shm`）和 storage 移到 `backend/storage/_pre_restore_snapshot/<时间戳>/`，保留 7 天（选错备份可回退）。
3. 还原 DB：默认用 `.db`（删现有 WAL 三件套后复制到位）；`--from-sql` 时用 `fashion_inspo.sql` 在全新库 `executescript`。
4. 还原 storage：robocopy 镜像回 T3 必背目录；`_crop_backup/logs/tmp` 恢复后不存在属预期，不告警。
5. 还原配置：`.env`、prompt/model 配置、`web/.env.local` 拷回原位。
6. 恢复后校验：`integrity_check` + 关键表 count 与 `manifest.json` 比对 + 抽查 DB 中 `file_path` 对应文件存在；不一致非零退出但**不自动回滚**（人工用步骤 2 快照决定）。
7. 结尾打印手动启动命令，不自动起服务。

**`docs/backup-restore.md`（新机器从零恢复 checklist）：**
Python 3.12/Node 20+/Git/ffmpeg/Git Bash → `git clone` 并 checkout 备份记录的 git HEAD → venv + `pip install -r requirements.txt` → 运行 `restore_data.sh` → `alembic upgrade head` → Ollama 安装 + `ollama pull qwen3-vl:8b-instruct` 与 `all-minilm`（模型不入备份）→ 可选 face-service（独立 Python 3.10 + insightface）→ 前端 `npm install && npm run build` → 核对 `.env`（API Key/路径在新机可能需调整）→ 启动验证 `GET /api/health`/素材数/抽查图片/搜索 → 重新注册 schtasks → 建议每季度用一份备份在临时目录演练一次。注明备份含 `.env` 密钥，勿放未加密网盘。

**验收：** 在临时目录对一份测试备份跑通 restore 全流程并通过恢复后校验；`_crop_backup` 缺失不告警。

---

#### 块 3：后端启动补备 task（T6） ✅ 已完成

改动：`backend/app/main.py`（lifespan）、`backend/app/config.py`（新增配置项），逻辑可抽 `backend/app/services/backup_service.py`。

- 仿现有 `_scraper_schedule_loop` / `_sweep_expired_trash`，在 lifespan 新增 `_startup_backup_loop()` 常驻 task：启动延迟 **10 分钟**（避开迁移/初始化竞争）；读备份目标目录最新一份含 `SUCCESS` 的时间戳，当天已成功 或 距上次成功 ≤20 小时则跳过，否则用 `asyncio.create_subprocess_exec`（项目已有此模式）异步 spawn `bash scripts/backup_data.sh E:/fashion-inspo-backups`，stdout/stderr 追加到 `storage/logs/backup.log`，不阻塞 HTTP；进程内标志位防重复触发；失败只 `logger.warning` 不重试（次日 03:00 定时兜底）。
- `.env` 新增 `BACKUP_ON_STARTUP=true`（默认开）、`BACKUP_TARGET_PATH`（备份目标，默认 E 盘路径）。
- reset 快照与启动清理共用一个 lifespan 清理 task，两套目录各自保留 7 天（见 fog 项）。
- 20 小时为初值，注释标注按实测备份耗时微调。

**验收（补测试，mock subprocess 不真跑备份）：** 当天有 SUCCESS → 跳过；无 SUCCESS 且超 20 小时 → 触发；`BACKUP_ON_STARTUP=false` 时不启动 task。

---

#### 块 4：reset 防呆（T5，核心链路，须补测试） ✅ 已完成

改动：`backend/app/routers/ai_reset.py`、`web/src/components/model/SettingsPanel.vue`、`backend/app/main.py`（清理 task）、`backend/tests/`、`.gitignore`。

**P0 — `DELETE /api/ai/reset` 四重防护：**
1. 执行前自动快照（轻量兜底）：reset 删除前把当前 `fashion_inspo.db`（含 WAL）和 T3 必背 storage 目录移动/复制到 `storage/_pre_reset_snapshot/YYYYMMDD_HHMMSS/`，保留 7 天（用移动+必要复制而非全量 robocopy，避免阻塞 reset）；该快照在 C 盘只防误操作、不防磁盘损坏（定时备份职责）。
2. 确认文字参数：后端在 `confirm=yes` 外新增必须精确匹配的字段（如 `confirm_text=DELETE`），不符返回 400；前端把第二次 popconfirm 改为「输入 `DELETE` 才能启用确认按钮」的输入框。
3. API Key 裸奔兜底：未配 `api_key` 的开发模式下仍强制要求确认文字；绑定非回环地址（非 127.0.0.1/localhost/::1）且无 Key 时直接 **403 拒绝**；其余破坏性接口维持现状。
4. 补审计留痕：写 `audit_logs`（action=`reset`，记录删除的表行数、文件数、快照路径、来源 IP）。

**P1/P2（清空垃圾桶/批量物理删除/单条删除）：** 维持现状（软删除层 + popconfirm + audit_logs），不额外加强。

`.gitignore` 加入 `storage/_pre_reset_snapshot/`、`storage/_pre_restore_snapshot/`。

**必补后端测试：** 确认文字缺失/错误 → 400；非回环无 Key → 403；快照确实生成；`audit_logs` 写入；reset 仍能正确清空 15 张表与 images/thumbnails/videos/LanceDB。

---

#### 块 5：文档与注册命令（收尾） ✅ 已完成

- 在 `docs/backup-restore.md` 给出用户手动执行的 schtasks 命令（不自动注册）：
  `schtasks /Create /SC DAILY /TN "FashionInspo-Backup" /TR "<绝对路径>\scripts\backup_task.bat" /ST 03:00 /F`
  配套：`schtasks /Query /TN "FashionInspo-Backup"`、`/Run`（手动触发）、`/Delete /F`。
- README 增补备份/恢复章节，链接到 `docs/backup-restore.md`。
- 完成后提示用户：手动执行 schtasks 注册、重启后端使块 3/块 4 生效。

---

#### fog 项落地决定

| 迷雾项 | 落地处理 |
| ------ | -------- |
| reset 快照与定时备份清理统一 | 共用一个 lifespan 启动清理 task，两套目录（`_pre_reset_snapshot`/`_pre_restore_snapshot`）各自 7 天保留 |
| 启动补备节流阈值 | 初值「当天已成功跳过 / 距上次 >20 小时」，注释标注按实测微调 |
| 备份并发保护 | `backup.lock` 目录锁，块 1 脚本与块 3 启动补备互斥 |
| 前端展示「上次成功备份时间」 | 本轮不做（非必需增强），留作后续 |

#### 明确排除（out of scope）

- 不备份代码/git 历史（已在 Gitee+GitHub 双远程，仅记录 `git_head.txt`）。
- 不备份 Ollama 模型文件、face-service 独立 Python 环境、Chrome profile/cookies（runbook 给重建步骤）。
- 不做高可用/实时复制/多机容灾；不在脚本内实现云厂商 API 上传（可把目标指向网盘挂载目录由客户端自行同步）。
- 不把 `_crop_backup` 与 `lancedb_backup_*` 纳入常规备份（可重建/临时副本；排除 `_crop_backup` 的明确代价：恢复后无法撤销已做过的裁剪，裁剪后成品图仍在）。

**建议执行顺序：** 块 1（备份脚本基础）→ 块 2（恢复脚本+runbook）→ 块 4（reset 防呆，核心链路单独提交）→ 块 3（启动补备）→ 块 5（文档收尾）。

## 中优先级

### 【任务】扩展“AI 模型管理 → 标签分析”功能，支持多模型多提示词组合分析，并完善历史对比

【背景】
项目为穿搭素材管理工具，前端 Vue 3 + Arco Design，后端 FastAPI + SQLite + Ollama。
目前标签分析功能（在 AI 模型管理页面）每次只能选择一个模型和一个提示词对素材执行一次分析，结果写入 `ai_analysis_log` 并自动合并标签到素材。现需增强为：

1. 支持一次性选择多个模型和多个提示词，生成所有组合进行分析（如 2 个模型 × 3 个提示词 = 6 次分析）。
2. 每次分析结果全部保存为独立历史记录，但**不自动合并/叠加到素材的正式标签**（素材标签保持唯一）。
3. 分析历史页支持对比不同模型/提示词组合的结果（标签差异、置信度、耗时等）。
4. 整个分析过程采用异步任务执行，接口秒回 task_id，由 worker 后台处理。
【需求明细】
5. 多模型多提示词选择

- 在“标签分析”页面的触发区域，允许用户：
- 选择**多个模型**（从已安装的视觉模型中勾选，可多选）。
- 选择**多个提示词**（从已保存的 Prompt 版本列表中选择，或选择“当前默认提示词”+ 已保存的历史版本，可多选）。
- 系统自动生成所有“模型 × 提示词”的组合，作为本次分析的计划。
- 用户还需选择**待分析的素材范围**，沿用现有的批量分析素材选择逻辑（如筛选条件、全部未分析、指定素材等）。

 1. 分析结果不自动合并标签

- 每次分析产生的结构化结果（`ai_extracted_tags` 快照）仅与 `ai_analysis_log` 关联，**不执行将标签自动关联到素材的操作**。
- 即：分析完成后，素材的 `inspiration_tags` 表不变，素材详情页显示的标签不变。
- 分析结果仅在分析历史中可见，供人工查看和对比。
- 保留现有“手动将某次分析结果应用到素材”的能力（如果已有此功能则保留；若没有，需提供入口：在分析历史中针对某条记录可一键“应用到素材”，此时才真正更新素材标签，且覆盖该素材的 AI 标签，保证标签唯一）。

3. 分析历史对比

- 分析历史列表需支持筛选：按素材、按模型、按提示词版本、按时间等。
- 增加“对比”功能：用户可勾选同一素材的**多条分析记录**（不同模型/提示词组合），进入对比视图。
- 对比视图展示：
- 各记录的基本信息（模型名、提示词版本、分析时间、耗时、状态）。
- 标签差异：并排展示每条记录提取的标签集合，高亮差异标签（新增、缺失、相同）。
- 置信度对比（如有）。
- 可复用或扩展现有 `GET /api/ai/compare/{id}` 接口，支持传入多条记录 ID 进行对比，或新增批量对比接口。

4. 异步任务

- 创建分析任务时，接口接收参数：模型列表、提示词列表、素材 ID 列表（或筛选条件），返回 `task_id`。
- 后端在 worker 中按组合顺序逐个执行分析，每个组合的分析结果独立写入 `ai_analysis_log` 和 `ai_extracted_tags`，不修改素材标签。
- 任务进度需反映总体完成情况（如“已完成 3/6”），失败记录跳过并记录错误，不影响其他组合继续执行。
- 任务完成后，用户可在任务管理页或分析历史页查看结果。
【技术要求】
- 后端：
- 修改或新增批量分析接口（如 `POST /api/ai/batch-analyze` 扩展参数，或新增 `POST /api/ai/multi-analyze`），接受 `models: list[str]`, `prompt_ids: list[int]` 或 `prompt_versions: list[str]`, `inspiration_ids: list[int]` 等参数。
- 创建异步任务时，将分析计划（组合列表）持久化到 `task_queue` 的 `result` 或新增字段中，供 worker 读取执行。
- worker 执行器（`task_runners` 中）需支持按组合循环调用现有分析逻辑，每个组合生成独立 `ai_analysis_log` 记录，并写入 `ai_extracted_tags` 快照，**跳过将标签合并到素材的逻辑**（可能需要抽象现有分析流程，增加参数控制是否应用标签）。
- 对比接口：新增 `POST /api/ai/compare-batch` 接受多个 `log_id`，返回结构化对比数据；或扩展 `GET /api/ai/compare/{id}` 为可传入多个 ID（但建议新增独立接口避免破坏现有 API）。
- “应用到素材”接口：新增或确认现有接口，允许将某条分析记录的结构化标签应用到素材（覆盖 AI 标签，保留手动标签，保持唯一）。
- 数据库方面：现有表已满足大部分需求，无需新增表，但可能需要为 `ai_analysis_log` 增加 `prompt_version` 和 `model_name` 的索引以提升筛选性能（如已存在则跳过）。
- 前端：
- “标签分析”页面：
  - 模型选择改为多选（复选框或标签选择器）。
  - 提示词选择改为多选，选项来自历史 Prompt 版本（可显示版本号、修改时间）。
  - 显示已选组合数量（模型数 × 提示词数）。
  - 提交后立即创建任务，显示 task_id 并提供跳转到任务管理页的链接。
- 分析历史页：
  - 增加筛选条件：模型、提示词版本、素材 ID/标题。
  - 列表增加“对比”按钮，点击后进入对比模式，勾选多条记录进行对比。
  - 对比视图按需求实现并排展示和高亮差异。
  - 每条分析记录增加“应用到素材”按钮（可选，根据需求）。
- 注意与现有批量分析功能的兼容，避免破坏原有流程（原有流程仍可使用但不再自动合并标签？或保留旧接口行为不变，新功能使用新接口）。
- 兼容性说明：
- 原有 `POST /api/ai/batch-analyze` 接口行为保持不变（可能仍会自动合并标签），新功能使用新接口 `POST /api/ai/multi-analyze`，或者修改原接口但增加参数 `apply_tags: bool = false` 来控制是否应用标签。推荐后者，保持接口统一。
【实现步骤建议】

1. 后端调研：
    - 定位现有批量分析接口、分析执行逻辑、标签保存/关联代码，确定如何增加“不应用标签”的控制参数。
    - 检查 `task_queue` 模型及 worker 执行器，了解如何传递复杂参数。
    - 查看分析历史与对比相关代码，评估扩展难度。
2. 后端修改：
    - 扩展批量分析接口参数，支持多模型多提示词和 `apply_tags` 开关。
    - 修改分析执行函数，增加 `apply_tags` 参数，当为 false 时只写日志和快照，不改素材标签。
    - 更新 worker 执行器，支持组合任务。
    - 新增对比批量接口。
    - 新增或调整“应用分析结果到素材”接口。
    - 更新 API 文档。
3. 前端修改：
    - 标签分析页：多选模型和提示词，显示组合数，提交调用新接口。
    - 分析历史页：增加筛选器、对比模式和“应用到素材”按钮。
    - 对比视图组件开发。
4. 测试：
    - 后端集成测试：多组合任务执行、结果不自动合并、对比接口正确性、应用接口覆盖标签。
    - 前端手动测试完整流程。
【验收标准】
5. 在标签分析页面可以选择多个模型和多个提示词，系统正确生成所有组合数量。
6. 提交后创建异步任务，任务在后台执行，每个组合产生独立分析记录。
7. 分析完成后，素材详情页的标签不发生任何变化（不自动叠加）。
8. 分析历史页能筛选出不同模型/提示词组合的结果，并可勾选多条记录进行对比，对比视图正确展示标签差异。
9. 对某条分析记录执行“应用到素材”后，该素材的 AI 标签被替换为该次分析结果，且不重复。
10. 原有单模型单提示词分析功能不受影响（或按新行为不再自动合并，需明确且前端提示）。
11. 异步任务进度和错误处理正常，组合中某个失败不影响其他组合。
【注意事项】

- “不自动合并标签”是本需求的核心，务必确保分析流程中无任何隐式标签关联操作。
- “应用到素材”是可选功能，如果当前没有，可先预留，但历史对比必须实现。
- 提示词版本管理已有基础，需确保选择的提示词能正确映射到分析日志的 `prompt_version`。
- 多模型多提示词组合可能导致大量分析，需限制最大组合数（如 10）或提示用户确认。
- 如果采用修改原接口方式，需保证前端调用兼容（默认 `apply_tags=true` 维持旧行为，新功能传 `false`）。

## 低优先级

### 同一人物跨平台素材重复取向（依赖抖音采集，暂缓）

**背景：** 同一现实人物在抖音与小红书都有账号（人物组方案 B 已落地后），用户可能两个平台都关注并采集，导致同一内容被采集两次。抖音采集尚未实现，此问题暂不处理，待抖音采集接入后再评估。

**待决策：** 素材入库时对同组账号的内容如何去重——取向 1（内容完全相同 → 不重复入库，只确认归属）为主 + 取向 2（平台独有内容保留，按组聚合统计）兜底，届时与用户确认。

**依赖：** 人物组（方案 B）落地、抖音采集实现。

### 小红书多图与视频采集（搜索模式，~ 进行中：详情页访问触发风控，已回退为只采封面）

**背景：** 搜索结果卡片通常只渲染 1 张封面图，笔记内的轮播图（3~9 张）与短视频只在详情页才加载，当前采集器因此只采到封面、丢失大部分素材。已尝试「逐个打开详情页提取」，但触发小红书风控：直接 `goto` 详情页被重定向 404（xsec_token 失效）、频繁访问触发「扫码后再手机查看」拦截，已回退为只采封面的稳妥模式。**注：按博主采集（collect_mode=user）已落地详情页提取路径**——进博主主页逐个打开笔记详情页，提取多图/视频/正文 caption，详情页随机间隔 2~4s、失败跳过（提交 de25457）。

**目标：** 在不触发风控的前提下，采集笔记详情页内的多张轮播图与短视频。

- 详情页访问策略：抽样打开（如每 3~5 个笔记只开 1 个）+ 详情页间长间隔（10~20s）+ 检测到「扫码/验证」页面立即停止
- 详情页媒体提取：精确的轮播图选择器（当前 swiper 类名已失效，需用 `diagnose_note_detail.py` 校准）+ 大图过滤排除相关推荐缩略图 + 视频 `<video>` 提取
- 视频入库：下载 mp4 到 `videos/`、ffmpeg 提取缩略图、`media_type=video`（参考已回退的 `_download_videos` 实现）
- 备选：改用小红书官方/合规接口获取笔记媒体，而非抓网页 DOM

**涉及模块：**

| 模块 | 改动 |
| ------ | ------ |
| `backend/scripts/run_scraper.py` | 详情页采集 + 视频下载 |
| `scripts/diagnose_note_detail.py` | 详情页 DOM 诊断（已存在） |

**验收标准：**

- 单次采集能拿到笔记内多张轮播图与视频，且不触发「扫码验证」拦截
- 视频入库为 video 类型，可在素材库按缩略图浏览

**风险与依赖：**

- 小红书风控严格，需先冷却账号、控制采集节奏；被风控后短期内不要重试
- 依赖 ffmpeg（视频缩略图，系统已安装）

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
- 视频分析功能依赖关键帧提取和 AI 复用，建议在任务队列和 AI 结构化存储完成后进行，以减少重复工作。
- 所有任务需遵循项目编码规范（见 CLAUDE.md）。
