# [TICKET · research · AFK] T4 现有备份脚本能力盘点与差距分析

- **Map**：[MAP](../MAP.md)
- **类型**：research（AFK，可交给研究子任务，但本项目约定主会话直接做）
- **状态**：✅ resolved（AFK research，主会话直接完成）
- **Blockers**：无
- **Blocks**：T7 备份校验、T8 恢复脚本（T6 调度也引用本结论）

## Question

逐条核对现有 `scripts/backup_data.sh` 相对 destination 的能力缺口，避免重复造轮子。

需要核实的清单：

1. **一致性快照**：已用 Python `sqlite3.backup()`，WAL 下安全。核实后端运行中备份是否真无半截写入（是否需短暂停写 / 设 WAL checkpoint）。
2. **全量 storage**：robocopy `/E /XD logs tmp`——核实它是否漏了 T3 认定"必背"的目录，以及是否把 `_crop_backup`、`lancedb_backup_*` 也背了（浪费空间）。
3. **配置文件**：已备份 `.env`、`prompt_configs.json`、`model_configs.json`、`prompt_versions.json`、`prompt.txt`、`web/.env.local`。核实这些文件名是否都真实存在、是否有新增的运行时配置未覆盖（如 `settings.yaml`？那是 DSH 的，不在本项目）。
4. **恢复能力**：脚本只做 backup，**没有 restore**。列出恢复时需要手动反推的步骤（DB 文件放哪、storage 放回哪、配置放回哪、向量是否需要重建、目录权限/空目录）。
5. **跨平台/调度**：脚本是 bash（Git Bash）。Windows 任务计划程序如何调用它（`bash -c` / Git Bash 路径）？是否需要一个 `.bat` 包装？
6. **可读校验**：当前脚本不验证备份。核实能否低成本加：DB `PRAGMA integrity_check`、文件计数/总字节数、写一个 `manifest.json`（文件清单 + 大小 + 可选哈希）。
7. **历史清理**：当前不清理旧备份。核实是否需要 `--retention N` 参数。
8. **目标路径与同盘警告**：默认 `backups/` 在项目盘内（同盘，不防磁盘损坏）；脚本结尾有口头提醒。核实是否加"目标在同盘时退出码/强提示"。

## 验收

一份"已有 / 缺失 / 建议改法"三列差距表，写入 resolution。它是 T5/T7/T8 的输入，不直接写代码（wayfinder 只做规划）。

## Resolution（2026-08-22 核实）

### 环境事实（已核实）

- DB：`backend/fashion_inspo.db`，**WAL 模式**，`PRAGMA integrity_check = ok`（84.5 MB）。
- Git Bash：`D:\Program Files (x86)\Git\bin\bash.exe`（bash 5.3）；Python：`D:\Program Files (x86)\python\python.exe`（3.12.7）。
- 后端 `main.py` 已有 `lifespan` 异步上下文 + 两个常驻 `asyncio.create_task` 循环（`_sweep_expired_trash`、`_scraper_schedule_loop`，30s tick）——**T6"启动后触发备份"可直接复用此模式**，无需引入看门狗进程或额外服务。
- `backups/`（项目盘 C 盘内）已存在两次历史备份（2026-08-16、08-17）+ 一个手动 `.db`，合计 **5.4 GB**，与代码同盘——这正是 T1 要把目标改到 E 盘的原因。

### 差距表

| # | 能力 | 现状 | 缺口 | 建议改法（供 T6/T7/T8） |
|---|------|------|------|------------------------|
| 1 | DB 一致性快照 | ✅ 用 Python `sqlite3.backup()`，WAL 下安全 | 无 | 保留。这是对的做法，勿改成文件 copy。 |
| 2 | 在线备份安全性 | ✅ backup API 自动处理 WAL，后端运行中可备份 | 无需停写 | 保留；可在备份前可选 `PRAGMA wal_checkpoint(PASSIVE)` 收敛 WAL，但非必需。 |
| 3 | SQL 明文导出 | ❌ 只有二进制 `.db` | T3 已决议要 SQL 双保险 | 快照后追加 `sqlite3` 的 iterdump → `fashion_inspo.sql`（用 Python `sqlite3` 的 `iterdump()`，不依赖 sqlite3 CLI）。 |
| 4 | storage 备份范围 | ⚠️ robocopy `/XD logs tmp` 只排除两个目录 | 与 T3 决议不符：未排除 `_crop_backup`（881MB）、`_crop_dups`、`cookies`、`debug`、`faces`，会把它们也背走（≈多 880MB/份） | robocopy `/XD` 扩展为 `logs tmp _crop_backup _crop_dups cookies debug faces`；确认 `images/person_photos/trash/videos/thumbnails/person_thumbnails/lancedb` 都在 `/E` 覆盖内（它们是，无需特殊处理）。 |
| 5 | 配置文件备份 | ✅ `.env`/`prompt_configs.json`/`model_configs.json`/`prompt_versions.json`/`prompt.txt` | `web/.env.local` 在脚本列表里但**当前不存在**（脚本已容错跳过）；后端根 `.env`（664B）在列表内且存在 | 保留现有容错循环；`web/.env.local` 不存在属正常，跳过即可。无需改。 |
| 6 | **恢复脚本** | ❌ **完全没有** | destination 核心判据缺失：没有任何 restore 入口，恢复全靠人工反推 | T8 新建 `scripts/restore_data.sh`（+ 可选 `.bat` 包装）：停服务提示 → 覆盖 DB（处理 WAL/shm）→ robocopy 回 storage → 还原配置 → integrity_check → health 探测。 |
| 7 | 恢复 runbook | ❌ 无 | L2 要求的"环境重建清单"不存在 | T8 写 `docs/backup-restore.md`：Python3.12、`pip install -r requirements.txt`、`alembic upgrade head`、Ollama+模型 pull、face-service 环境、ffmpeg、目录创建、`.env` 还原、验证步骤。 |
| 8 | Windows 调度 | ⚠️ 脚本是 `.sh`，无 `.bat` 包装、无 schtasks 注册 | "每日自动"无法落地；项目约定不自动注册计划任务 | T6 提供 `scripts/backup_task.bat`（调用 Git Bash 跑 backup_data.sh 并传 E 盘目标），并在文档给出用户手动执行的 `schtasks /create ...` 命令行（不自动执行）。 |
| 9 | **启动后触发** | ❌ 无 | T2 要求"后端启动稳定一段时间后自动补备"，脚本是被动 CLI | T6 在后端加一个 `_startup_backup_loop()` asyncio task（仿 `_scraper_schedule_loop`）：启动延迟 N 分钟 → 检查"今天是否已成功备份/距上次成功备份是否 >N 小时" → 异步 spawn 备份脚本 → 防抖。用标志文件/备份目录里的 success 标记判断。 |
| 10 | 备份校验 | ❌ 脚本不验证 | "看起来备份了但可能是坏的"无法发现 | T7：备份后 `PRAGMA integrity_check` + 生成 `manifest.json`（文件清单/大小/可选 SHA-256）+ 关键目录存在性检查；失败写非零退出码与日志。 |
| 11 | 历史清理（rotation） | ❌ 脚本不清理旧备份 | 多次备份撑爆磁盘（当前 backups/ 已 5.4GB） | T6：脚本加 `--retention-daily N --retention-weekly M`，按目录时间戳清理旧备份；默认建议日 7 + 周 4。 |
| 12 | 目标路径/同盘警告 | ⚠️ 默认 `backups/`（项目盘内），结尾仅口头 echo 提醒 | 默认就是同盘，不防磁盘损坏；提醒可被忽略 | T1 已定目标 E 盘。建议：传参显式指定目标；当目标解析为与项目同盘时打印强警告并返回非零退出码（或要求 `--allow-same-disk`）。 |
| 13 | 敏感信息 | ⚠️ 备份含 `.env`（密钥） | T1 已定暂不上云，本地 E 盘风险可控 | 暂不加密；runbook 注明"备份含密钥，勿放入未加密的网盘/公共位置"。未来加网盘时（MAP out-of-scope 已记）需重估。 |
| 14 | 审计日志 | ✅ `audit_logs` 是 DB 内表 | 无 | 随 DB 快照自动备份，reset 也刻意保留该表——无需单独处理。 |
| 15 | 退出码/日志 | ⚠️ robocopy 退出码 0-7 已处理；整体脚本成功/失败无明确最终退出码，无结构化日志 | T6 自动调度需要可靠退出码来判断成败；T9 失败可见性也依赖它 | 脚本结尾根据 DB 快照/robocopy/校验结果汇总退出码（0 成功，非 0 失败），日志固定写到备份目录与 `storage/logs/backup.log`。 |

### 结论

- **可直接保留**：DB 一致性快照（#1/#2/#14）、配置文件备份循环（#5）。
- **必补（destination 硬要求）**：恢复脚本 + runbook（#6/#7，对应 T8）、备份校验（#10，对应 T7）、排除 `_crop_backup`（#4，对应 T3 决议，T6/T8 实现时落地）。
- **需新增（自动化）**：`.bat` 包装 + schtasks 命令（#8）、启动后触发 task（#9）、rotation（#11）、可靠退出码/日志（#15）——全部归入 T6。
- **SQL 导出**（#3）归入 T6/T8 增强脚本。
- **同盘警告**（#12）随 T6 一并处理。

本票为 research，不写代码；上述建议作为 T6/T7/T8 的输入规格。
