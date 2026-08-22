# [TICKET · grilling · HITL] T5 破坏性操作防呆设计

- **Map**：[MAP](../MAP.md)
- **类型**：grilling（交互设计需用户拍板）
- **状态**：✅ resolved（HITL，用户拍板）
- **Blockers**：无
- **Blocks**：T9 防呆实现（execution，本地图完成后 hand off；不在规划阶段写代码）

## Question

对破坏性操作加什么样的防呆，在"安全"与"不烦人"之间取平衡？

涉及操作：

- `DELETE /api/ai/reset?confirm=yes`（最高危：一键清空 15 张表 + 文件 + 向量）
- `DELETE /api/inspirations/trash`（清空垃圾桶，物理删除）
- 批量物理删除、重复素材物理删除、清理孤立文件

需要 grill 出的设计点：

1. **确认强度分级**：
   - reset 级：是否要求输入一段确认文字（如输入库名/`DELETE`），而非点一下"是"？
   - 清空垃圾桶级：现有 confirm 是否够？
2. **"先备份再执行"**：reset/清空前是否提供"先生成一份备份再继续"的选项？这要求备份可被 API 同步触发（依赖 T8 增强脚本，且要考虑 1.9GB 备份耗时，不能阻塞 HTTP 请求——可能要走任务队列）。
3. **防呆加在哪层**：API 层（任何调用方都受保护，最稳）、前端层（用户体验好但可被绕过）、还是两层都要？
4. **冷却/速率限制**：reset 这类操作是否需要冷却时间或二次 API Key？
5. **软删除兜底**：reset 是否应该先把当前 DB/storage 移到一个带时间戳的"reset 前快照"目录（而非直接 `rmtree`），保留 N 天再清理？这比"强制先备份"轻量。

## 验收

一份分级防呆方案（每个破坏性操作对应：确认方式、是否强制快照、加在哪层），写入 resolution。实现细节留到 map 完成后 hand off 给 execution。

## 现状（2026-08-22 核实）

- 已有 `DESTRUCTIVE_ROUTES` 清单 + `require_api_key` 中间件（`backend/app/utils/auth.py`，33 个破坏性路由）。
- **短板 1**：`settings.api_key` 为空（开发模式）时，`require_api_key` 直接 return，**整个清单认证跳过**；当前绑 `127.0.0.1` 风险有限但仍裸奔。
- **短板 2**：`DELETE /api/ai/reset` 是唯一"一键清空 15 张表 + storage 文件 + LanceDB"的操作，后端只有 `confirm=yes`，前端两次 popconfirm，**无快照兜底、无 audit_logs 记录**（仅 logger.warning）。
- 清空垃圾桶走 `purge_trash`（素材已先移入 `storage/trash/` 软删除层，写 audit_logs）；批量/单条物理删除均有 popconfirm + 审计。这些风险显著低于 reset。

## Resolution（分级防呆，用户选定）

### 🔴 P0 — reset（`DELETE /api/ai/reset`）：四重防护

1. **执行前自动快照（轻量兜底）**
   - reset 真正删除前，把当前 `fashion_inspo.db`（含 WAL）和 `storage/` 中 T3 认定的"必备份"目录**移动/复制**到带时间戳的快照目录，例如 `storage/_pre_reset_snapshot/YYYYMMDD_HHMMSS/`。
   - 用"移动 + 必要复制"而非全量 robocopy，避免阻塞 reset；快照保留 **7 天**，由启动时的清理逻辑（复用 lifespan）删除过期项。
   - 这是"先备份再执行"的轻量版（不调完整 E 盘备份，因为那要 ~1 分钟且 reset 是低频操作）；误操作后可从该快照回滚。
   - 注意：快照在项目盘 C 盘，**不防磁盘损坏**（那是 T1/T6 定时备份的职责），只防"误点 reset/误调用"。
2. **要求输入确认文字（强人工确认）**
   - 后端在 `confirm=yes` 之外增加一个必须精确匹配的确认字段，例如要求输入 `DELETE`（或库名）才执行；输入不符返回 400。
   - 前端把第二次 popconfirm 替换为"输入 `DELETE` 才能启用确认按钮"的输入框（替代纯点击）。
3. **API Key 裸奔兜底**
   - reset 接口即便在"未配置 api_key 的开发模式"下，也不能无条件开放：未配置 Key 时仍强制要求上述确认文字参数；且若绑定非回环地址（非 127.0.0.1/localhost/::1）又无 Key，直接拒绝 reset（403）。
   - 其余破坏性接口维持现有"配了 Key 才认证、未配则跳过"的行为，不扩大改动面。
4. **补审计留痕**
   - reset 执行写一条 `audit_logs`（action=`reset`，记录删除的表行数、文件数、快照路径、操作者来源 IP），与其他破坏性操作一致。审计表本就被 reset 刻意保留。
   - 防护层放在 **API 层**（任何调用方都受保护，最稳）；前端加强只为用户体验，不作为唯一防线。

### 🟡 P1 — 其他批量/清空类：维持现状

- 清空垃圾桶（`DELETE /api/inspirations/trash`）、批量物理删除（`/admin/batch-delete`）、去重、清理孤立文件、标签批量删除/合并、分析历史批量删除等：
  - **维持现状**：已有 popconfirm 确认 + audit_logs；清空垃圾桶还有"先移入 trash 软删除层"兜底。
  - 理由：这些是可定向/可预期的操作，软删除层 + 审计已足够；再加强（输文字）会高频烦人、收益低。
  - 不额外加"执行前快照"（reset 才是全库无差别清空）。

### 🟢 单条物理删除：维持现状

- 单条素材物理删除、单张照片删除等：popconfirm + 审计，维持现状。

## 实现 hand-off 备注（不在本票写代码）

- 改动文件集中在 `backend/app/routers/ai_reset.py`（快照逻辑、确认字段、审计、裸奔兜底）和 `web/src/components/model/SettingsPanel.vue`（输入确认 UI）。
- 快照目录加入 `.gitignore`；快照清理可复用 `main.py` lifespan 的启动清理模式（仿 `_sweep_expired_trash`）。
- 需补后端测试：确认文字缺失/错误返回 400、非回环无 Key 被拒、快照确实生成、audit_logs 写入、reset 仍能正确清空（核心链路测试，遵循 CLAUDE.md 测试约定）。
- 本票是规划决议；**具体编码在整张 map 的决策票都走完后统一 hand off 给执行阶段**。
