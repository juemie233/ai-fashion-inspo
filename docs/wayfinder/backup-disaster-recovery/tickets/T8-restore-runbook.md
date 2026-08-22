# [TICKET · task · AFK] T8 恢复脚本与恢复文档（restore）

- **Map**：[MAP](../MAP.md)
- **类型**：task（AFK：写 restore 脚本与 runbook；但恢复目标分级需用户确认）
- **状态**：✅ resolved
- **Blockers**：~~T2（恢复级别）、T3（备份范围）、T4（差距分析）~~
- **Blocks**：无（destination 终态规划之一）

## Question

当前只有 backup，没有 restore。需要一份"反向操作"脚本 + 一份 runbook，让新机器上的人能照着恢复。

需要规划的内容：

1. **restore 脚本职责**：
   - 停止后端/worker（避免恢复中写入）——给出手动命令，不自动停服务（项目约定）。
   - 用 `sqlite3.backup` 的反向：把备份 DB 拷回（或用 `.restore`），处理 WAL 文件。
   - 把 storage 各目录 robocopy/xcopy 回原位。
   - 还原 `.env` 等配置。
   - 若 T3 决定"向量/缩略图不备份、恢复时重建"，脚本末尾触发：缩略图重建任务 + 向量回填任务（项目已有 task_queue 能力）。
2. **幂等与安全**：restore 到一个空目录最安全；覆盖已有数据前必须二次确认（避免把好数据覆盖成坏备份）。
3. **环境重建 runbook**（L2 级别）：Python 3.12、`pip install -r requirements.txt`、`alembic upgrade head`、Ollama 与模型 pull、face-service 环境、目录创建、ffmpeg。给成 checklist，不自动装系统软件。
4. **验证恢复成功**：启动后 `GET /api/health`、素材条数、随机抽查文件存在。

## 验收

一份 restore 脚本的规格（输入参数、步骤、安全确认、重建触发）+ runbook 大纲。写入 resolution。实际编码在 map 完成后 hand off。

## Resolution（2026-08-22）

### A. 恢复脚本 `scripts/restore_data.sh`（+ `restore_task.bat` 包装）

**用法**：`bash scripts/restore_data.sh <备份目录> [--force] [--allow-overwrite]`

**执行步骤（顺序固定，每步打印进度）：**

1. **前置检查（不改动任何数据）**
   - 确认 `<备份目录>` 存在且含 `SUCCESS` 标记（T7）；无标记则拒绝，除非传 `--force`。
   - 检查磁盘剩余空间 ≥ 备份解压后大小 ×1.1。
   - 检测后端/worker 是否在运行（查端口 18888 / 进程）；若在运行，**打印停止命令并退出**，要求用户先手动停服务（遵守项目"服务启停由用户手动执行"约定，脚本不自行 kill）。
   - 若目标位置已有数据且未传 `--allow-overwrite`，拒绝并提示：现有数据会被覆盖，建议先做一次当前数据的备份。
2. **安全网：恢复前自动快照当前状态**
   - 若目标已存在 `fashion_inspo.db` 或 storage 数据，先把它们移动到 `backend/storage/_pre_restore_snapshot/<时间戳>/`（与 T5 reset 快照同构），保留 7 天。这样即使选错备份也能回退。
3. **还原数据库**
   - 优先用备份的 `fashion_inspo.db`：删除现有 `fashion_inspo.db` / `-wal` / `-shm`，复制快照到位（保留文件权限）。
   - 提供 `--from-sql` 选项：当 `.db` 损坏时，用 `fashion_inspo.sql` 在全新库上 `executescript` 重建（T7 已保证该 SQL 可导入）。
4. **还原 storage 文件**
   - 用 robocopy 把备份的 `storage/` 各必备份目录（T3 清单）镜像回 `backend/storage/`。
   - `_crop_backup/`、`logs/`、`tmp/` 等被排除目录在恢复后不存在是**预期状态**，不告警。
5. **还原运行时配置**
   - 把 `.env`、`prompt_configs.json`、`model_configs.json`、`prompt_versions.json`、`prompt.txt`、`web/.env.local` 拷回原位（已存在则被步骤 2 的快照兜住）。
6. **恢复后校验（复用 T7）**
   - 跑 `PRAGMA integrity_check`（必须 ok）。
   - 关键表 count 与 `manifest.json` 比对（inspirations、tags、inspiration_tags、models 等）。
   - 抽查若干素材文件实际存在于磁盘（对比 DB 里的 file_path）。
   - 打印校验报告；任何不一致 → 非零退出码 + 明确提示，但**不自动回滚**（数据已落盘，回滚可能更危险，交由人工按步骤 2 的快照决定）。
7. **不自动启动服务**：结尾打印"恢复完成，请手动启动后端/worker"的命令（`bash scripts/ensure-services.sh` 或对应方式由用户执行）。

**幂等/安全**：步骤 1–2 保证不覆盖未保护的数据；脚本可重复执行（每次覆盖前都留快照）。

### B. 环境重建 runbook `docs/backup-restore.md`

新机器从零恢复的 checklist（L2 级，只给命令、不自动装系统软件）：

1. **安装基础环境**：Python 3.12、Node 20+、Git、ffmpeg、Git Bash（Windows）。
2. **取代码**：`git clone` 仓库并 `checkout` 到备份记录的 git HEAD（`manifest.json`/`git_head.txt` 里有，避免新版本代码不兼容旧数据）。
3. **Python 依赖**：`cd backend && python -m venv .venv && pip install -r requirements.txt`。
4. **放置备份并恢复**：把 E 盘（或异地）备份拷到本机，运行 `restore_data.sh <备份目录>`。
5. **数据库迁移**：`alembic upgrade head`（备份库通常已是最新版，但换代码版本后保险跑一次）。
6. **Ollama 与模型**：安装 Ollama → `ollama pull` 视觉模型 `qwen3-vl:8b-instruct` 与 embedding 模型 `all-minilm`（模型文件不入备份，按 README 重拉）。
7. **face-service（可选）**：如需人脸识别，按 README 在独立 Python 3.10 环境部署 insightface 子服务。
8. **前端**：`cd web && npm install && npm run build`（或开发模式 `npm run dev`）。
9. **配置核对**：检查 `.env`（API Key、Ollama 地址、路径在新机器是否需要调整）。
10. **启动与验证**：手动启服务 → `GET /api/health` → 后台看素材数/抽查图片/搜索标签 → 确认 `audit_logs` 等历史在。
11. **恢复自动备份**：重新执行 T6 的 `schtasks /Create` 注册命令（新机器计划任务不会自动跟来）。
12. **定期演练**：建议每季度用一份备份在临时目录跑一次 restore + 校验，确认备份真的可用（T7 不做自动试恢复，靠人工演练兜底）。

### C. 明确不做

- 恢复脚本不自动停/起服务（项目约定）。
- 不恢复 Ollama 模型、Python 虚拟环境、Chrome profile（MAP out-of-scope，runbook 给重建步骤）。
- 不做跨大版本自动迁移假设；以备份记录的 git HEAD 对齐代码为准。
- 不自动重新生成缩略图/向量（T2 已定它们已在备份内，恢复即用）；仅当用户从仅含原图的旧备份恢复时，runbook 附注可手动触发向量回填/缩略图任务。

## 备注

T8 解开后，destination 的核心判据——"拔掉当前盘，仅凭一份备份+一份说明能在新机器跑起来且数据齐全"——有了完整实现路径。T6/T7/T8 加上 T5 防呆，构成 hand off 给执行阶段的完整规格。
