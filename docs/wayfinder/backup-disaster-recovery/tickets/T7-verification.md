# [TICKET · task · HITL/AFK] T7 备份校验与可恢复性证明

- **Map**：[MAP](../MAP.md)
- **类型**：task（决定校验方案；可 AFK 实现一个 manifest 生成器）
- **状态**：✅ resolved
- **Blockers**：~~T1（目标介质）、T3（备份范围）、T4（现有脚本缺口）~~
- **Blocks**：T6（退出码）、T8（restore 复用校验）、执行阶段编码

## Question

如何证明一份备份是"可恢复的好备份"，而不是"脚本跑完了但文件是坏的/缺的"？

候选校验层级（成本递增）：

1. **基础**：DB `PRAGMA integrity_check` 通过；关键目录存在；写 `manifest.json`（文件清单 + 大小 + mtime）。
2. **中等**：manifest 里带每个文件的 SHA-256；备份后随机抽样比对。
3. **强**：在临时目录做一次"试恢复"（DB 打开成功 + 关键表行数 + 随机文件可读取），跑完删除。代价大但最可信。

需要决定：

- 采用哪一层作为每次备份的默认校验。
- 对 1.9GB 全量做 SHA-256 的耗时是否可接受（originals ~830MB，尚可；若包含 _crop_backup 则 1.9GB）。
- 网盘目标下，校验必须在文件"真正落盘"后进行（robocopy 返回 vs 网盘后台仍在同步）。

## 验收

确定校验层级 + manifest 格式 + 失败时的退出码/日志行为。写入 resolution，作为 T8 增强脚本的规格。

## Resolution（2026-08-22）

采用**"基础完整性 + 抽样哈希"**层级（介于候选 1 和 2 之间），在"能发现坏备份"和"备份耗时"间取平衡：~1GB 全量 SHA-256 每次约十几秒到一分钟，对每日一次可接受，但对后端运行中 spawn 的补备略重；故关键产物全量校验、大体积素材用清单+抽样。

### 备份完成后自动执行的校验（写进备份脚本）

1. **数据库完整性**
   - 对备份出的 `fashion_inspo.db` 跑 `PRAGMA integrity_check;`（必须返回 `ok`）。
   - 校验 SQL 明文 `fashion_inspo.sql`：非空、含 `CREATE TABLE`、能被 sqlite 解析（用 `sqlite3 :memory:`.read 或 Python `executescript` 在内存库试跑，确保可导入）。
2. **必备目录与文件存在性**
   - 检查 T3 认定的必备份目录都存在：`images/`、`person_photos/`、`trash/`、`thumbnails/`、`person_thumbnails/`、`lancedb/`（`videos/` 有内容时）。
   - 检查配置文件：至少 `.env` 存在；其余 prompt/model 配置存在则记录、缺失不致命（告警）。
3. **manifest 清单**
   - 在备份根目录生成 `manifest.json`，记录：备份时间戳、git HEAD、源 DB 行数摘要（关键表 count）、每个必备份目录的**文件数 + 总字节数**、逐文件的相对路径与大小（图片类大文件默认**不存哈希**以省时；可选 `--verify-hash` 开启全量 SHA-256）。
   - 备份脚本用源端统计 vs 目标端统计比对文件数/字节数是否一致（robocopy 自身也有日志，做双保险）。
4. **成功标记**
   - 全部校验通过 → 在备份根写 `SUCCESS` 标记文件（内含时间戳与校验摘要），备份脚本退出码 **0**。
   - 任何一项失败 → 写 `FAILED` 标记 + `backup.log` 里的失败原因，退出码**非 0**；**不删除**该失败备份目录（留证），但 rotation 清理时优先删失败的。
   - T6 的启动补备和"今天是否已成功备份"判断**只认 `SUCCESS` 标记**，不看目录是否存在（避免把半截备份当成成功）。

### 不做（避免过度）

- 不默认对 ~1GB 全部素材做全量 SHA-256（每日一次的成本不必要；文件数+字节数比对能发现绝大多数 robocopy 中断/磁盘写坏）。
- 不在每次备份时做"临时目录试恢复"（候选 3，成本高；交给 T8 的 restore 脚本 + runbook 里的"定期演练"人工触发）。
- 不做网盘后台同步完成探测（T1 已定暂不用网盘）。

### restore 复用

- T8 的恢复脚本在恢复**前**校验所选备份目录含 `SUCCESS` 标记（无标记则警告并要求 `--force` 才继续），恢复**后**跑一次 `PRAGMA integrity_check` + 关键表 count 与 manifest 比对，确认恢复成功。
