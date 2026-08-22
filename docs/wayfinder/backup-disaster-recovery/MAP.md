# [MAP] 数据备份与灾难恢复

> Wayfinder map · 类型：planning（只产出决策，不产出交付物）
> 创建：2026-08-22 · 规划收官：2026-08-22
> 状态：✅ **8/8 tickets resolved — 路线已清晰，可 hand off 执行**

## Destination

3000+ 素材库在**误删、磁盘损坏、误触发 `DELETE /api/ai/reset`** 三种灾难下可恢复：
1. 一键备份能在**全新目录 / 另一台机器**完整恢复（素材、缩略图、标签、收藏、审核状态、人物、向量可重建）。
2. 备份**按计划自动产生**，且至少有一份在**系统盘/项目盘之外**（异盘或网盘），保留多份历史。
3. 破坏性操作（reset / 清空垃圾桶 / 批量物理删除）有防呆，并能"先备份再执行"。

成功判据：拔掉当前项目盘，仅凭一份备份 + 一份恢复说明，能在新机器把库跑起来且数据齐全。

## Notes

- **领域**：本地单机 Windows 11 部署，Python 3.12 + SQLite + LanceDB，storage 为本地文件。
- **现有资产（不要重复造）**：
  - `scripts/backup_data.sh` 已实现：SQLite 一致性快照（Python `sqlite3.backup`，后端运行中可安全备份）、robocopy 全量 `storage/`（排除 logs/tmp）、`.env`/`prompt_configs.json`/`model_configs.json`/`prompt_versions.json`/`prompt.txt`/`web/.env.local` 配置、git HEAD 记录。
  - 已存在 `storage/lancedb_backup_20260817_132149/`（一次性的手工 LanceDB 备份，非定期）。
- **数据布局与体量（2026-08-22 实测）**：
  - DB：`backend/fashion_inspo.db` **84.5 MB**。
  - `storage/` 总计 **1.86 GB**，其中：`_crop_backup` 881 MB（裁剪原图，可重建/可裁剪）、`person_photos` 797 MB、`thumbnails` 109 MB、`trash` 17.7 MB、`images` 16.5 MB、`lancedb` 32 MB、`person_thumbnails` 3.2 MB。
  - 关键观察：**真正不可再生的是 originals**（`person_photos`、`images`、`trash` 里待恢复的），约 830 MB；缩略图/向量/裁剪备份可从原图再生，合计约 1 GB。
- **破坏性操作清单**（已有审计留痕）：
  - `DELETE /api/ai/reset?confirm=yes`（已需 API Key + confirm 双保险，但仍会清空 15 张表 + 删除 images/thumbnails/videos + 清空 LanceDB；保留 audit_logs）。
  - `DELETE /api/inspirations/trash` 清空垃圾桶（物理删除，不可恢复）。
  - 批量物理删除、重复素材物理删除、孤立文件清理、裁剪替换（已有 `_crop_backup` 兜底）。
- **运行环境约束**：
  - `.env` 含密钥，备份中会包含敏感信息——备份目标必须考虑加密/访问控制。
  - 备份脚本是 `.sh`（Git Bash），自动调度在 Windows 上用「任务计划程序（schtasks）」最稳妥；不要引入需常驻的服务。
  - 项目约定：服务启停、计划任务注册由用户手动执行，脚本只生成/提供命令，不自动注册。
- **每个 session 应参考的技能/规范**：wayfinder 方法论、项目 `CLAUDE.md`、`scripts/` 工具脚本约定。

## Tickets（frontier = open + unblocked + unclaimed）

| ID | Ticket | 类型 | 状态 | 依赖 |
|----|--------|------|------|------|
| ~~T1~~ | [备份目标位置与异盘策略](./tickets/T1-backup-target.md) | grilling · HITL | ✅ resolved | — |
| ~~T2~~ | [恢复目标分级（RTO/RPO）](./tickets/T2-recovery-objective.md) | grilling · HITL | ✅ resolved | — |
| ~~T3~~ | [备份范围与分层策略](./tickets/T3-backup-scope.md) | task · HITL/AFK | ✅ resolved | T1, T2 |
| ~~T4~~ | [现有备份脚本差距分析](./tickets/T4-gap-analysis.md) | research · AFK | ✅ resolved | — |
| ~~T5~~ | [破坏性操作防呆设计](./tickets/T5-destructive-guardrails.md) | grilling · HITL | ✅ resolved | — |
| ~~T6~~ | [自动调度与保留策略](./tickets/T6-schedule-retention.md) | task · HITL/AFK | ✅ resolved | T1, T3 |
| ~~T7~~ | [备份校验与可恢复性证明](./tickets/T7-verification.md) | task · HITL/AFK | ✅ resolved | T1, T3, T4 |
| ~~T8~~ | [恢复脚本与 runbook](./tickets/T8-restore-runbook.md) | task · AFK | ✅ resolved | T2, T3, T4 |

> 🎉 **8/8 票全部 resolved。** 路线清晰，无遗留 blocker，可进入执行阶段。
> 每个 execution session 仍建议一次只做一个可交付块（见下方"执行清单"）。

## Decisions so far

<!-- 每张已关闭 ticket 一行：[ticket 名](link) — 一句话结论 -->

- [T2 恢复目标分级](./tickets/T2-recovery-objective.md) — **L2 级恢复**：备份含 DB + 原始素材 + 运行时配置，配 restore 脚本与环境重建 runbook；**RPO ≤1 天**（每日备份 + 后端启动稳定后自动补备一次）；**RTO 恢复即用**（缩略图、向量一并备份，不做恢复时重建）。
- [T1 备份目标位置](./tickets/T1-backup-target.md) — 主备份到 `E:\fashion-inspo-backups\`（独立物理 SSD，431GB 可用，防 C 盘损坏）；暂不用网盘，`.env` 密钥只落本地；单盘不防整机被盗/火灾，留作未来扩展。
- [T3 备份范围与分层](./tickets/T3-backup-scope.md) — 备份 images/person_photos/trash/videos/thumbnails/person_thumbnails/lancedb；**排除 `_crop_backup`**（881MB，裁剪前原图唯一副本，接受恢复后无法撤销裁剪的代价，备份降到 ~1GB）；数据库额外导出 SQL 明文双保险。
- [T4 现有脚本差距分析](./tickets/T4-gap-analysis.md) — DB 一致性快照可保留；必补：restore 脚本+runbook、备份校验、robocopy 排除 `_crop_backup`、SQL 导出；需新增：`.bat` 包装+schtasks、后端启动后触发 task（复用 lifespan 循环）、rotation、可靠退出码/日志。
- [T5 破坏性操作防呆](./tickets/T5-destructive-guardrails.md) — 分级防呆：reset 加四重防护（执行前自动快照保留7天 + 要求输入确认文字 + 未配Key裸奔兜底/非回环拒绝 + 补 audit_logs）；清空垃圾桶/批量/单条删除维持现状（已有软删除层+popconfirm+审计）。防护加在 API 层。
- [T6 自动调度与保留](./tickets/T6-schedule-retention.md) — 双通道：每日 03:00 经 schtasks + `backup_task.bat`；后端 lifespan 加启动补备 task（延迟10分钟，当天已成功则跳过，复用 asyncio 循环）。保留日备7份+周备4份（约7~11GB），成功以 SUCCESS 标记为准。
- [T7 备份校验](./tickets/T7-verification.md) — 备份后跑 DB integrity_check + SQL 内存试导 + 必备目录存在性 + 文件数/字节数比对，写 `manifest.json`；通过则落 `SUCCESS` 标记退出0，失败落 `FAILED` 非零退出（不删失败备份）。默认不全量 SHA-256（`--verify-hash` 可选）。
- [T8 恢复脚本与 runbook](./tickets/T8-restore-runbook.md) — 新增 `restore_data.sh`：前置检查（SUCCESS标记/空间/停服务确认/覆盖确认）→ 恢复前快照当前状态 → 还原 DB（支持 --from-sql）→ robocopy 还原 storage → 还原配置 → 恢复后 integrity_check + count 比对；配 `docs/backup-restore.md` 新机器环境重建 checklist。脚本不自动停启服务。

## 执行清单（hand off 给实现阶段）

按依赖排序，每块独立可测：

1. **增强备份脚本**（T3/T4/T6/T7）：改 `scripts/backup_data.sh`——目标路径参数化与同盘拒绝、robocopy 排除 `_crop_backup` 等、新增 SQL 导出、rotation、manifest+校验+SUCCESS/FAILED 标记、退出码与日志；新增 `scripts/backup_task.bat`。
2. **恢复脚本 + runbook**（T8）：新增 `scripts/restore_data.sh`、`restore_task.bat`、`docs/backup-restore.md`。
3. **后端启动补备 task**（T6）：`main.py` lifespan 加 `_startup_backup_loop()`（仿现有循环），新增 `.env` 开关 `BACKUP_ON_STARTUP`。
4. **reset 防呆**（T5）：改 `ai_reset.py`——执行前快照、确认文字参数、未配Key/非回环兜底、写 audit_logs；改前端 `SettingsPanel.vue` 为输入确认；补后端测试。
5. **文档与注册命令**：在 README/runbook 给出用户手动执行的 `schtasks /Create` 命令（不自动注册）。

> 实现时遵循项目 `CLAUDE.md`：注释中文、核心链路补测试、脚本改动不自动启停服务、改完提示用户重启。

## Not yet specified（fog：在 scope 内但还说不清楚，frontier 到达后 graduate 成 ticket）

> 规划收官时，原列迷雾大部分已被 T1–T8 扫清。仅余以下留给执行阶段细化：

- **reset 快照与定时备份的清理任务如何统一**：T5 的 `_pre_reset_snapshot/`（保留7天）与 T6 的备份 rotation 是两套目录，执行阶段决定是否用同一个启动清理逻辑覆盖（倾向：复用 lifespan 启动清理，各自保留期独立）。
- **启动补备的精确节流阈值**：T6 定了"当天已成功则跳过 / 距上次 >20 小时"，执行时按实测备份耗时微调。
- **备份进行中再次触发的并发保护**：同一时刻不应跑两个备份（schtasks 凌晨 + 启动补备可能撞车）。执行阶段用锁文件（`backup.lock`）互斥。
- **前端是否展示"上次成功备份时间"**：T6 提到的失败可见性升级项，非必需，留作后续增强。

## Out of scope（明确排除，永不 graduate）

- **备份代码 / git 历史**：代码已在 Gitee + GitHub 双远程版本控制，不属于数据灾难恢复。备份只记录 `git_head.txt` 用于对齐版本。
- **Ollama 模型文件、face-service 的独立 Python 环境、Chrome 采集 profile / cookies**：属环境配置而非素材数据；模型可重新 `ollama pull`，face-service 环境按 README 重建。恢复文档可给"环境重建清单"但不做热备份。
- **高可用 / 实时复制 / 多机容灾**：单机本地部署，不做 HA、主从、实时同步。
- **备份文件的云端上传自动化**：可以把备份目标指向已挂载的网盘目录（由网盘客户端自行同步），但不在脚本内实现云厂商 API 上传。
- **`storage/_crop_backup` 与 `lancedb_backup_*` 的内容纳入常规备份**：这两类是可再生/临时副本（见 Notes 体量），是否纳入由分层策略 ticket 决定，默认倾向排除以省空间。
