# 数据备份与恢复指南

> 对应规划：[`docs/wayfinder/backup-disaster-recovery/MAP.md`](./wayfinder/backup-disaster-recovery/MAP.md)
> 恢复级别 **L2**（备份含 DB + 原始素材 + 缩略图/向量 + 运行时配置，恢复即用）；**RPO ≤ 1 天**。

本项目素材库的核心数据——SQLite 数据库、`storage/` 素材文件、LanceDB 向量、`.env` 等运行时配置——均不进 git。本文档说明如何自动/手动备份，以及在误删、磁盘损坏、误触发 `DELETE /api/ai/reset` 后如何恢复。

---

## 1. 备份了什么

每次备份在目标目录下生成 `YYYY-MM-DD_HHMMSS/`：

| 内容 | 说明 |
| ---- | ---- |
| `fashion_inspo.db` | SQLite 一致性快照（Python `sqlite3.backup`，后端运行中可安全备份） |
| `fashion_inspo.sql` | SQL 明文导出（`iterdump`），DB 文件损坏时的抢救双保险 |
| `storage/` | `images/`、`person_photos/`、`trash/`、`videos/`、`thumbnails/`、`person_thumbnails/`、`lancedb/` |
| `backend/.env`、`backend/prompt_configs.json`、`backend/model_configs.json`、`backend/prompt_versions.json`、`backend/prompt.txt`、`web/.env.local` | 不进 git 的运行时配置（按原相对路径保存） |
| `git_head.txt` | 备份对应的代码提交号 |
| `manifest.json` | 校验清单：关键表行数、各目录文件数/字节数、git HEAD、告警 |
| `SUCCESS` / `FAILED` | 备份结果标记；`SUCCESS` 内含时间戳与 git HEAD |

**明确不备份：** `storage/_crop_backup`（裁剪前原图唯一副本，约 880MB，用户拍板排除以省空间；代价是恢复后无法撤销已做过的裁剪，裁剪后成品图仍在）、`logs/`、`tmp/`、`_crop_dups/`、`cookies/`、`debug/`、`faces/`、Ollama 模型文件、Python 虚拟环境、Chrome profile/cookies、代码与 git 历史（已在 Gitee + GitHub 双远程）。

> ⚠️ **安全提示：** 备份含 `.env`（密钥）。请勿放入未加密的网盘/公共位置；可把备份目标指向已加密/受控的网盘挂载目录，由网盘客户端自行同步（脚本本身不做云上传）。

---

## 2. 手动备份

```bash
# 推荐：备份到独立物理盘（E 盘）
bash scripts/backup_data.sh E:/fashion-inspo-backups

# 临时同盘备份（会被强警告，需加 --allow-same-disk）
bash scripts/backup_data.sh backups --allow-same-disk

# 额外对所有素材做全量 SHA-256（默认关，较慢）
bash scripts/backup_data.sh E:/fashion-inspo-backups --verify-hash
```

备份完成后看 `SUCCESS` 标记与退出码：`0` 成功，`2` 备份/校验失败（失败目录写 `FAILED`，保留排障，不自动删除）。日志同时写入备份目录的 `backup.log` 与 `backend/storage/logs/backup.log`。

**保留策略（自动 rotation）：** 每日成功备份保留最近 7 份；更早的、落在周日的备份额外保留 4 份（周备）；失败备份独立保留最近 3 份；被中断的半截备份（无任何标记）立即清理。只删除匹配 `YYYY-MM-DD_HHMMSS` 的目录，手动放入的文件不受影响。

---

## 3. 自动每日备份（注册 Windows 计划任务）

项目约定**不自动注册计划任务**，请在确认目标盘存在后手动执行一次：

```bat
schtasks /Create /SC DAILY /TN "FashionInspo-Backup" ^
  /TR "\"C:\Users\Administrator\Desktop\Claude Code\MMK\fashion-inspo\scripts\backup_task.bat\"" ^
  /ST 03:00 /F
```

- 每日 **03:00** 运行 `scripts/backup_task.bat`，它会调用 Git Bash 执行备份到 `E:\fashion-inspo-backups`。
- 目标盘在 `backup_task.bat` 顶部的 `BACKUP_TARGET` 变量修改；Git Bash 路径脚本会自动探测常见安装位置。

管理命令：

```bat
schtasks /Query  /TN "FashionInspo-Backup"            REM 查看状态/上次结果
schtasks /Run    /TN "FashionInspo-Backup"            REM 立即手动触发一次
schtasks /Delete /TN "FashionInspo-Backup" /F         REM 删除计划任务
```

**双通道补充：** 除每日定时外，后端启动并稳定运行约 10 分钟后会自动补备一次（若距上次成功备份 ≤20 小时则跳过），之后每 6 小时复查。该通道由以下 `.env` 配置控制（均有默认值）：

```ini
BACKUP_ON_STARTUP=true                # 设为 false 关闭启动补备
BACKUP_TARGET_PATH=E:/fashion-inspo-backups
BACKUP_STARTUP_DELAY_MINUTES=10       # 启动后延迟多久再检查
BACKUP_MIN_INTERVAL_HOURS=20          # 距上次成功备份小于此时长则跳过
BACKUP_TICK_HOURS=6                   # 复查周期
```

两个通道（schtasks 凌晨定时 + 后端启动补备）通过 `backup.lock` 目录锁互斥，不会并发；启动补备的日志同样写入 `backend/storage/logs/backup.log`。

---

## 4. 恢复

### 4.1 同机恢复（误删 / 误触发 reset 后）

```bash
# 先确认后端与 worker 已停止（脚本检测到 18888 端口在监听会拒绝恢复）
# 列出可用备份（选带 SUCCESS 的最新一份）
ls E:/fashion-inspo-backups/

# 执行恢复（目标已有数据时，脚本会先快照当前状态再覆盖）
bash scripts/restore_data.sh E:/fashion-inspo-backups/2026-08-26_102630 --allow-overwrite

# 或用 bat 包装（无参数时自动选最新 SUCCESS 备份）
scripts\restore_task.bat
```

恢复脚本步骤：

1. **前置检查**：备份有 `SUCCESS`（无则拒绝，`--force` 可强跳）；磁盘空间充足；18888 端口空闲（要求你先停服务，脚本不自行 kill）；目标已有数据需 `--allow-overwrite`。
2. **恢复前快照**：把当前 `fashion_inspo.db`（含 WAL/SHM）和 storage 必备目录移到 `backend/storage/_pre_restore_snapshot/<时间戳>/`，保留 7 天——选错备份也能回退。
3. **还原 DB**：默认用 `.db` 快照（先删现有 WAL 三件套）；`--from-sql` 时用 `fashion_inspo.sql` 在全新库 `executescript` 重建（`.db` 损坏时用）。
4. **还原 storage**：robocopy 逐目录镜像回必备目录；`_crop_backup/logs/tmp` 恢复后不存在属预期，不告警。
5. **还原配置**：`.env` 与 prompt/model 配置、`web/.env.local` 拷回原位。
6. **恢复后校验**：`PRAGMA integrity_check` + 关键表 count 与 `manifest.json` 比对 + 随机抽查 20 个素材文件在磁盘存在。不一致非零退出，但**不自动回滚**（数据已落盘，由你按步骤 2 的快照决定）。

恢复完成后脚本会打印后续步骤提示，但不自动启动服务。

### 4.2 新机器从零恢复（磁盘损坏/换机）

按顺序执行（均为手动，脚本不自动安装系统软件）：

1. **基础环境**：安装 Python 3.12、Node.js 20+、Git、Git Bash（Windows）、ffmpeg。
2. **取代码并对齐版本**：
   ```bash
   git clone <仓库地址> fashion-inspo
   cd fashion-inspo
   # 用备份记录的提交号对齐代码版本（见备份目录的 git_head.txt 或 manifest.json）
   git checkout <git_head>
   ```
3. **Python 依赖**：
   ```bash
   cd backend
   python -m venv .venv
   source .venv/Scripts/activate   # Git Bash 下
   pip install -r requirements.txt
   cd ..
   ```
4. **放置备份并恢复**：把 E 盘（或异地）备份拷到本机，运行：
   ```bash
   bash scripts/restore_data.sh <备份目录>
   ```
5. **数据库迁移（保险）**：备份库通常已是最新版，换代码版本后跑一次：
   ```bash
   cd backend && alembic upgrade head && cd ..
   ```
6. **Ollama 与模型**：安装 Ollama 后拉取模型（模型文件不入备份）：
   ```bash
   ollama pull qwen3-vl:8b-instruct   # 视觉分析模型（以实际配置为准）
   ollama pull all-minilm             # 文本嵌入模型
   ```
7. **face-service（可选，需要人脸识别时）**：按 README 在独立 Python 3.10 环境部署 insightface 子服务。
8. **前端构建**：
   ```bash
   cd web && npm install && npm run build   # 或开发模式 npm run dev
   ```
9. **核对 `.env`**：在新机器上检查 API Key、Ollama 地址、存储路径等是否需要调整。
10. **启动并验证**：手动启动后端/前端/worker，然后：
    - 访问 `GET http://127.0.0.1:18888/api/health` 确认健康；
    - 后台查看素材总数、抽查图片可显示、标签搜索正常；
    - 确认 `audit_logs`、收藏、审核状态等历史数据在。
11. **重新注册自动备份**：计划任务不会跨机器迁移，按第 3 节重新执行 `schtasks /Create`。
12. **定期演练**：建议每季度取一份备份在临时目录跑一次 `restore_data.sh` + 校验，确认备份真的可用。

---

## 5. 常见场景

| 场景 | 做法 |
| ---- | ---- |
| 误删了一批素材（已进垃圾桶） | 优先用界面「垃圾桶 → 恢复」；若已清空垃圾桶，用最近备份整库恢复（会回滚到备份时点）。 |
| 误触发 `DELETE /api/ai/reset` | reset 执行前会自动快照 DB 与素材目录到 `storage/_pre_reset_snapshot/`（保留 7 天）；若需整库回退，用最近备份按 4.1 恢复。 |
| 怀疑 DB 文件损坏 | 先用 `.db` 快照恢复；若 `integrity_check` 不过，改用 `--from-sql` 从 `fashion_inspo.sql` 重建。 |
| 备份失败 | 看 `backend/storage/logs/backup.log` 与失败目录下的 `backup.log`/`FAILED`；失败目录保留最近 3 份。 |
| 备份盘空间不够 | rotation 自动保留约 11 份（~17GB）；可手动删除更早的时间戳目录，或调小脚本里的 daily/weekly 保留数。 |
| 备份进行中又触发了一次 | 第二次会因 `backup.lock` 直接跳过并退出 0，不会并发。 |

---

## 6. 文件与脚本索引

| 文件 | 作用 |
| ---- | ---- |
| `scripts/backup_data.sh` | 备份主脚本（一致性快照 + SQL 导出 + robocopy + 校验 + 标记 + rotation + 锁） |
| `scripts/backup_task.bat` | 供 schtasks 调用的每日备份包装 |
| `scripts/restore_data.sh` | 恢复主脚本（前置检查 + 恢复前快照 + 还原 + 校验） |
| `scripts/restore_task.bat` | 恢复包装（无参时自动选最新 SUCCESS 备份） |
| `docs/wayfinder/backup-disaster-recovery/` | 备份与灾难恢复的完整规划记录（MAP + 8 张 ticket） |
