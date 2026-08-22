# [TICKET · task · HITL/AFK] T6 自动调度与保留策略

- **Map**：[MAP](../MAP.md)
- **类型**：task（产出一份可执行的调度方案，不自动注册——项目约定由用户手动执行注册）
- **状态**：✅ resolved
- **Blockers**：~~T1（目标位置）、T3（备份范围/体积）~~
- **Blocks**：T8（restore 脚本与 backup 脚本对称）、执行阶段编码

## Question

备份多久自动跑一次、保留几份、用什么调度，在容量与 RPO（T2）间平衡。

需要决定：

1. **频率**（T2 已定 RPO ≤1 天）：基础节奏为**每日一次**（建议凌晨低峰）；**额外**增加"后端启动并稳定运行一段时间后自动触发一次备份"。需确定启动延迟（建议 5–10 分钟，避开启动期竞争）与**防抖规则**：当天若已成功备份则启动触发跳过，或按"距上次成功备份超过 N 小时才补备"。注意 storage 已含缩略图/向量（~1GB+），但 robocopy `/MIR` 增量很快，日频可接受。
2. **全量 vs 增量**：Windows 上 robocopy `/MIR` 做镜像增量很快；DB 是单文件快照（每份 84MB）。是每次全量快照，还是用 SQLite 增量/只保留最近 N 份 DB？
3. **保留策略（rotation）**：例如"日备保留 7 份 + 周备保留 4 份"，需要一个清理旧备份的逻辑（当前 backup_data.sh 不清理）。
4. **调度载体**：
   - 每日定时 → Windows 任务计划程序（`schtasks /create`），提供一条用户可手动执行的注册命令（项目约定：不自动注册服务/计划任务）。需要 `.bat` 包装让 schtasks 调用 Git Bash 脚本。
   - 启动后触发 → 这是**应用内事件**而非计划任务：后端启动后延迟 N 分钟 spawn 一次备份（需带"当天已备则跳过"判断）。要决定是后端加个轻量调度逻辑，还是用独立看门狗进程。
5. **失败可见性**：备份失败如何让人知道？（写日志到固定位置 / 退出码 / 前端管理页是否要展示上次备份状态——这是 fog，先不展开）。

## 验收

一份调度方案：频率、保留份数与清理规则、`schtasks` 注册命令（供用户手动执行）、失败日志位置。写入 resolution。

## Resolution（2026-08-22）

### 触发方式（双通道）

**通道 A：每日定时（Windows 任务计划程序）**

- 时间：每日 **03:00**（低峰，采集/AI 任务少）。
- 载体：新增 `scripts/backup_task.bat`，内容为用 Git Bash 执行备份脚本并显式传入 E 盘目标，例如：
  ```bat
  @echo off
  "D:\Program Files (x86)\Git\bin\bash.exe" -c "cd '/c/Users/Administrator/Desktop/Claude Code/MMK/fashion-inspo' && bash scripts/backup_data.sh 'E:/fashion-inspo-backups'"
  ```
- 注册命令（**写入 runbook 由用户手动执行**，遵守项目"不自动注册计划任务"约定）：
  ```
  schtasks /Create /SC DAILY /TN "FashionInspo-Backup" /TR "C:\...\scripts\backup_task.bat" /ST 03:00 /F
  ```
- 备份脚本的目标默认从参数取；缺省/同盘时拒绝执行（见 T4 #12）。

**通道 B：后端启动后自动补备**

- 在 `backend/app/main.py` 的 `lifespan` 中新增一个常驻 task `_startup_backup_loop()`，完全仿照现有 `_scraper_schedule_loop` / `_sweep_expired_trash` 的 `while True + asyncio.sleep + try/except` 模式。
- 行为：服务启动后**延迟 10 分钟**再检查（避开启动期迁移/初始化竞争）；通过读取备份目标目录里最新一份成功备份的时间戳（见 T7 的 success 标记），判断"今天是否已成功备份"或"距上次成功备份是否 >20 小时"。
- 满足条件则用 `asyncio.create_subprocess_exec`（项目已有该模式，见 `ai_models.py`/`gpu_service.py`）异步 spawn `bash scripts/backup_data.sh E:/fashion-inspo-backups`，不阻塞服务、不 await 到 HTTP 响应；stdout/stderr 重定向到 `storage/logs/backup.log`。
- **防抖**：当天已成功备份则跳过；进程内用标志位避免重复触发；失败只记日志不重试（下次启动或次日定时兜底）。
- 可通过 `.env`（如 `BACKUP_ON_STARTUP=false`）关闭，默认开。

### 备份内容与体积

- 单份约 **~1.0 GB**（T3 决议：排除 `_crop_backup`，含缩略图/向量），robocopy `/MIR` 增量复制实际传输量远小于此（只有变化文件）。
- DB：二进制一致性快照 `fashion_inspo.db` + 明文 `fashion_inspo.sql`（T3 #3）。

### 保留策略（rotation）

- **每日备份保留 7 份，每周日的备份额外保留 4 份（周备）**，由备份脚本在成功后自检清理（T4 #11）。
- 判定：按备份目录名时间戳 `YYYY-MM-DD_HHMMSS`；最近 7 个日备全留；在这之外、落在周日的留最近 4 个；其余删除。
- 预计占用：7~11 份 × ~1GB ≈ **7~11 GB**，E 盘 431GB 无压力。
- 清理只删备份根目录下符合时间戳命名格式的子目录，避免误删手动放入的文件。

### 失败可见性

- 备份脚本以**退出码**表达成败（T4 #15）：0=全部成功，非 0=有失败项；每一步结果写入 `E:/fashion-inspo-backups/<stamp>/backup.log` 并追加到 `backend/storage/logs/backup.log`。
- 通道 B 失败时 logger.warning；runbook 说明可用 `schtasks /Query /TN "FashionInspo-Backup"` / `/Run` 手动排查。
- T7 的校验失败也算备份失败（非零退出码），启动补备据此判断。

### 明确不做

- 不自动执行 `schtasks /Create`（项目约定，由用户手动跑注册命令）。
- 不引入常驻看门狗/额外服务；通道 B 寄生在后端进程内（与现有定时采集 task 同构）。
- 不做云上传（MAP out-of-scope）。
