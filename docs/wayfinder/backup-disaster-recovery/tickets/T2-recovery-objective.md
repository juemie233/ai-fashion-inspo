# [TICKET · grilling · HITL] T2 恢复目标分级（RTO/RPO 与恢复到什么程度）

- **Map**：[MAP](../MAP.md)
- **类型**：grilling
- **状态**：✅ resolved
- **Blockers**：无
- **Blocks**：T3 备份范围与分层、T8 恢复脚本

## Question

"全新机器恢复"具体恢复到什么程度？这决定恢复脚本要写多重、备份要带什么。

需要与用户确认的分层选项（让用户选，而不是替他假设）：

- **L0 仅数据**：DB + 原始素材文件。恢复后需自己配环境、起服务。
- **L1 数据 + 运行时配置**：再加 `.env`、prompt/model 配置（当前 backup_data.sh 已做到这层）。
- **L2 可一键起服务**：在 L1 基础上提供"恢复脚本 + 环境重建清单"（Python 依赖 `pip install`、Ollama 模型 `pull`、face-service 环境说明、目录创建、DB/文件归位），但不自动装系统级软件。
- **L3 全环境复刻**：连 Ollama 模型文件、face-service 虚拟环境、Chrome profile 一起备份。

**初步建议**（供 grill，不是结论）：目标定 **L2**。L3 把可再生的大体积环境塞进备份，违背"备份不可再生数据"原则，且 MAP 的 out-of-scope 已排除模型/环境热备份。

还需确认：

- **RPO（可容忍丢多少数据）**：能接受丢最近多久的改动？这决定自动备份频率（T6）——每天一份 vs 每小时。
- **RTO（多快要恢复）**：分钟级 / 小时级 / 当天即可？这决定是否值得做"重建缩略图/向量"的耗时步骤，还是干脆把它们也备份进去（T3）。

## 验收

明确选定 L0–L3 中的级别，并给出 RPO/RTO 的可接受范围。写入 resolution 与 Decisions so far。

## Resolution

- ✅ **恢复级别：L2（可一键起服务）**。备份包含 DB + 原始素材 + 运行时配置；另提供 `restore` 脚本把数据/配置归位，并配一份环境重建 runbook（Python 依赖、`alembic upgrade head`、Ollama 模型 pull、face-service 环境、ffmpeg、目录创建）。不备份 Ollama 模型文件与虚拟环境（可重建，见 MAP out-of-scope）。
- ✅ **RPO：≤1 天**。基础节奏为**每天备份一次**；额外要求：**每次后端启动并稳定运行一段时间后，自动触发一次备份**（贴合"每天集中使用、非 7×24 持续写入"的实际节奏，避免开机即备份的冷数据竞争）。
- ✅ **RTO：恢复即用，不接受重建等待**。缩略图（`thumbnails/`、`person_thumbnails/`）与向量库（`lancedb/`）**纳入备份**，恢复后立即可浏览/搜索，不在恢复阶段跑重建任务。备份体积相应增大（~1GB 量级，可接受）。

**对下游票的约束**：
- T3：`thumbnails/`、`person_thumbnails/`、`lancedb/` 归入"必备份"（不依赖重建）；`_crop_backup` 是否纳入仍待 T3 裁决。
- T6：调度需同时支持「每日定时」+「后端启动一段时间后触发」两种触发；后者要防抖（启动后延迟如 5–10 分钟、且当天已备份则跳过）。
- T8：restore 末尾无需触发缩略图/向量重建任务（已在备份内），但应校验其存在。
