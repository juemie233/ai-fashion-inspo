# [TICKET · task · HITL/AFK] T3 备份范围与分层策略

- **Map**：[MAP](../MAP.md)
- **类型**：task（需要核实体量并产出一份"哪些目录必背/哪些可重建"的清单）
- **状态**：✅ resolved（T1+T2 已解）
- **Blockers**：~~T1（目标盘容量）、T2（恢复级别 + RTO）~~
- **Blocks**：T6 自动调度与保留、T7 备份校验、T8 增强备份脚本

## Question

基于 2026-08-22 实测体量，决定每个 storage 子目录的处置：必备份 / 可重建排除 / 可选。

已知体量：

| 目录 | 大小 | 可再生性 | 结论 |
|------|------|----------|------|
| `images/` | 16.5 MB | ❌ 原始素材，不可再生 | **必备份** |
| `person_photos/` | 797 MB | ❌ 模特写真原图，不可再生 | **必备份** |
| `trash/` | 17.7 MB | ❌ 待恢复的软删除文件 | **必备份** |
| `videos/` | 0 | ❌（有内容时）原始视频 | 有内容时必备份 |
| `thumbnails/` | 109 MB | ✅ 可从原图重建 | **必备份**（T2：恢复即用） |
| `lancedb/` | 32 MB | ✅ 可从素材+DB 重建（耗时） | **必备份**（T2：恢复即用） |
| `person_thumbnails/` | 3.2 MB | ✅ 可重建 | **必备份**（小 + 恢复即用） |
| `_crop_backup/` | 881 MB | ⚠️ 裁剪前原图的唯一副本 | **排除（用户决定）** |
| `_crop_dups/` | 0.44 MB | 临时/重复检测 | 排除 |
| `cookies/` `debug/` `tmp/` `logs/` | 小 | ✅ 临时/调试 | 排除 |
| `faces/` | 0.02 MB | ✅ 人脸特征可重扫 | 排除 |

需要产出的决策：

1. 最终的"必背目录"清单（影响 backup_data.sh 的 robocopy `/XD` 排除列表）。
2. **`_crop_backup` 要不要背**：它占 881MB。若排除，需确认它只是裁剪操作的可丢弃兜底，而非唯一原件（核实 crop_service：备份长期保留供手动恢复——但原图本身仍在 images/person_photos，所以 _crop_backup 是"裁剪前版本"，丢失后无法还原裁剪前原图 → 这是个真实风险点，需在 ticket 中核实结论）。
3. **thumbnails / lancedb**：✅ T2 已定**纳入备份**（恢复即用，不在恢复时重建）。本票只需确认 robocopy 确实覆盖到这些目录、排除列表没有误删。
4. 数据库是否需要额外的 SQL 明文导出（除二进制快照外），便于跨版本/损坏时抢救。

## Resolution

- ✅ **必备份**：`images/`、`person_photos/`、`trash/`、`videos/`（有内容时）、`thumbnails/`、`person_thumbnails/`、`lancedb/`。即所有原始素材 + 为"恢复即用"所需的缩略图/向量。
- ✅ **`_crop_backup/` 排除**（用户拍板，省 ~881MB）。代码核实：裁剪流程为 `copy2(full → _crop_backup)` 后 `os.replace(裁剪版 → full)`，因此 `_crop_backup` 是**裁剪前原图的唯一副本**。排除的明确代价：灾难恢复后**无法撤销已做过的裁剪**（裁剪后成品图仍在，不影响浏览/搜索）。用户接受此代价以换取备份体积从 ~1.9GB 降到 ~1GB。
- ✅ **`_crop_dups/`、`cookies/`、`debug/`、`tmp/`、`logs/`、`faces/` 排除**（临时/可重建）。
- ✅ **数据库双重导出**：除 `sqlite3.backup()` 的一致性二进制快照 `fashion_inspo.db` 外，**额外导出一份 SQL 明文**（如 `.sql`/`.db.sql`），用于 DB 文件损坏时的抢救与跨版本迁移。
- 备份单份体积约 **~1.0 GB**（不含 `_crop_backup`），在 E 盘 431GB 上可轻松支撑多份历史。

**对下游票的约束**：
- T6：按 ~1GB/份规划保留策略；robocopy 排除列表必须显式包含 `_crop_backup _crop_dups cookies debug tmp logs faces`（覆盖现有脚本只排除 `logs tmp` 的缺口）。
- T7：manifest 与校验须覆盖上述必背目录 + 两份 DB 产物。
- T8：restore 不重建缩略图/向量（已备份），但 `_crop_backup` 不存在是预期状态，不应告警。
