# 豆包 Seedream 5.0 Flash 接入提交代码评测（3645006）

> **评审对象**：`3645006132c954698df7af3e6e7ccdef4134623a`（短号 `3645006`）
> `feat: 接入豆包 Seedream 5.0 Flash 云端图生图（P1/P2，默认关闭）`
> **评审时间**：2026-11-03。**只读评审，未改代码**。
> **评审范围**：该提交改动的 16 个文件、1311 行新增 / 32 行删除。按用户要求，**不评估安全面**（密钥、隐私、日志泄露、审核绕过、访问控制等）；隐私弹窗只作为交互行为提及，不作为安全结论。
> **快照说明**：本文按该提交的代码快照评审。后续提交（如 `949f670` 隐藏云端一键出图、`a288197` 多姿态）可能已修掉部分问题，本文不回溯评价后续改动。
> **修复记录（2026-11-03）**：已按 §7 建议顺序修复全部 P0/P1/P2/P3 问题，实现细节见 [接入方案 §15](./豆包Seedream5.0Flash图生图接入方案.md)，逐项对照见文末 §11。

---

## 0. 结论与评级

**一句话结论**：整体架构方向正确（provider 分支 + 复用 `generation_runs` + 客户端职责窄 + 有 mock 测试），但存在 **2 个阻断级正确性缺陷**和若干 P1 集成缺口；**建议先修 P0/P1，再默认启用云端档位**。

| 维度 | 分数 | 说明 |
| --- | ---: | --- |
| 架构与复用 | 8 / 10 | provider 分支插在正确位置，历史/候选/入库全部复用，没有另造一套 |
| 正确性 | 5 / 10 | 用户自定义 prompt 被丢弃；预调用失败让批次卡在 running |
| 错误处理 | 4 / 10 | 只处理 `SeedreamError`；下载失败会重跑整次生成；取消不可中断 |
| 测试 | 6 / 10 | 客户端 mock 覆盖不错，但漏了 prompt、失败状态、下载重试、预算等关键路径 |
| 可维护性 | 7 / 10 | 代码可读；有死常量、死配置、未使用字段 |
| 性能/资源 | 7 / 10 | 图片处理走 `asyncio.to_thread`；但下载失败重试会产生重复付费调用 |
| 一致性 | 6 / 10 | retry/重出/带入表单丢云端参数；状态横幅仍假设 ComfyUI |
| 文档 | 7 / 10 | 接入方案已更新；但“已实现”与代码仍有差距（如 `SEEDREAM_MAX_POSES` 未使用） |
| **综合** | **6.3 / 10（B-）** | 可用，但**不建议在未修 P0 的情况下开启云端档位** |

**改动规模**：16 个文件；新增 2 个后端测试文件（14 个用例）+ 1 个前端云端用例；新增 `seedream_client.py` 330 行、执行器 133 行、服务层 156 行。

---

## 1. 改动地图

| 层 | 文件 | 改动 | 风险 |
| --- | --- | --- | --- |
| 配置 | `backend/.env.example`、`backend/app/config.py` | `ARK_*` / `SEEDREAM_*` 配置 | 低 |
| 模型表 | `backend/app/services/comfyui/models.py` | 新增 `seedream_5_0_flash` 档位（`provider=ark`） | 中 |
| 探测 | `backend/app/services/comfyui/probe.py` | `ProbedStatus.ark`；云端不依赖 ComfyUI 模板 | 中 |
| API | `backend/app/routers/generate.py` | 隐藏未启用的云端档位；下发 provider/成本/能力 | 低 |
| Schema | `backend/app/schemas/generation.py` | `size` / `watermark` 可选字段 | 低 |
| 服务层 | `backend/app/services/generation_service.py` | 云端分支 `_create_ark_run`、提示词包装、预算 | **高** |
| 客户端 | `backend/app/services/seedream_client.py` | 新增 Ark HTTP 客户端（330 行） | **高** |
| 执行器 | `backend/app/services/task_runners/image_gen.py` | `_execute_seedream`、图片预处理 | **高** |
| 前端 | `web/src/api/generate.ts`、`GenerateParamForm.vue` | 云端档位、尺寸/水印、隐私确认 | 中 |
| 测试 | 两个后端测试文件、一个前端测试 | mock Ark / 创建/执行链路 | 中 |

---

## 2. 阻断级缺陷（P0）

### P0-1 用户自定义 prompt 被静默丢弃

**位置**：

- 前端提交时确实带了 prompt：`web/src/components/generate/GenerateParamForm.vue:637`（`prompt: prompt.value.trim() || '按图2姿势重新拍摄...'`）；
- 后端 `_create_ark_run` 构造 prompt 时只传了姿势提示词：`backend/app/services/generation_service.py:282`
  ```python
  prompt = _ark_prompt_text(pose, pose_prompt, payload.get("negative"))
  ```
- `_ark_prompt_text` 的签名与实现（`:245-251`）只接受 `pose`、`pose_prompt`、`negative`，**没有 user_prompt 参数**：
  ```python
  def _ark_prompt_text(pose, pose_prompt, negative):
      detail = (pose_prompt or "").strip() or _pose_text_prompt(pose)
      text = _ARK_PROMPT_TEMPLATE.format(framing=_ark_framing(pose))
      if detail:
          text = f"{text}\n姿势细节：{detail}"
      return build_seedream_prompt(text, negative)
  ```

**影响**：

- 用户在描述框写的任何内容（“换成红色背景”“不要笑”“保留项链”等）**不会进入云端请求**；
- 前端「插入姿势描述」按钮产生的 prompt 也不会生效；
- 输出看起来“不听描述”，但日志/历史里又显示用户填过 prompt，排查成本高；
- 现有测试 `test_create_seedream_run_uses_ark_branch` 传了 `"prompt": "x"`，但只断言 `图1/图2/姿势细节`，没有断言 `"x"`，所以测试全绿也发现不了。

**修复建议**：

1. 给 `_ark_prompt_text` 增加 `user_prompt` 参数，并明确拼接优先级：
   ```text
   固定包装（身份/姿势/线稿排除）
   → 用户补充要求（payload.prompt）
   → 姿势细节（pose_prompts）
   → 负向改写（避免出现：...）
   ```
   用户要求优先于姿势细节，因为姿势图已经承担“姿势”信号；长度不够时先截断姿势细节，不要截用户要求。
2. 在 `_create_ark_run` 里传 `payload.get("prompt")`；
3. 测试断言 `run.prompt` / 实际请求体包含用户原文（例如 `"换成红色背景"`）。

### P0-2 预调用失败让 `GenerationRun` 永久卡在 `running`

**位置**：`backend/app/services/task_runners/image_gen.py:387-425`

```python
run.status = "running"                      # :399
...
if insp is None or insp.deleted_at: raise PermanentTaskError(...)   # :406
if not source.exists(): raise PermanentTaskError(...)               # :409
try:
    pose_path = await pose_gallery.ensure_pose_image(pose_id)
except HTTPException as exc:
    if exc.status_code == 503:
        raise RecoverableTaskError(...)      # :417
    raise PermanentTaskError(...)            # :418
...
prepared_material = await asyncio.to_thread(_prepare_ark_image, ...) # :423
prepared_pose = await asyncio.to_thread(_prepare_ark_image, ...)     # :425
```

只有 `SeedreamError` 分支（`:449-452`）会设置 `run.failed = 1` / `run.status = "failed"`。上面这些路径**直接抛出任务异常，没有回写 GenerationRun**。

**影响**：

- 素材文件缺失、素材被删、姿势图源站 503/404、图片损坏（PIL 抛错）等情况，任务会转 pending/failed，但批次状态仍是 `running`；
- 历史里显示“生成中”永远不结束；
- `discard_run` 明确拒绝 `running` 批次（`generation_service.py` 中“本批仍在生成中”），用户无法丢弃，只能手动改库或等清理；
- 这是**显式处理过的 503 路径**也会中招，不只是极端异常。

**修复建议**：

1. 把“准备阶段”包在 try/except 里，统一走一个 `_fail_run(db, run, message)`：设置 `run.failed=1`、`run.error`、`run.status="failed"`、`await db.commit()`；
2. 或者把 `run.status="running"` 推迟到所有准备完成、真正要调云端之前；准备失败时保持 `queued` 并让任务失败，至少不会显示 running；
3. 捕获 `Exception` 兜底（PIL `UnidentifiedImageError`、`OSError`、`json.JSONDecodeError`、`httpx.HTTPError` 等），记录 traceback，再按可恢复/永久错误分类；
4. 测试补充：素材缺失、姿势 503、损坏图片三条路径，断言 `run.status == "failed"` 且 `run.error` 非空。

> 这两条是“功能看起来能用、但实际行为不对”的典型；建议列为合入后的第一优先级。

---

## 3. 高优先级问题（P1）

### P1-1 下载失败会重跑整次生成（重复计费）

**位置**：`backend/app/services/seedream_client.py:231-245` 的重试循环 + `:302-311` 的 `_download`

```python
for attempt in range(1, MAX_ATTEMPTS + 1):
    try:
        result = await self._request_once(body)   # 内部含 _download
    except SeedreamError as exc:
        if not exc.retryable ...: raise
        await asyncio.sleep(...)
```

`_download` 失败会抛 `SeedreamError(retryable=True)`，于是 `generate` 会**重新调用 `/images/generations`**，而不是只重试下载。

**影响**：

- 云端已经生成并计费的一张图，因为 TOS 下载抖动被丢弃，下一次重试又生成一张 → **重复付费**；
- 两次生成结果可能不同，用户看到的是第二次的图，第一次白花钱；
- 调试时日志里只有一个 `Seedream 出图完成`，看不到中间失败。

**修复建议**：

- 把“生成”和“下载”拆成两段重试：200 响应拿到 `url` 后，只对 `_download` 做 2～3 次退避；生成请求本身不再重发；
- 或者直接 `response_format="b64_json"`，省掉下载请求；
- 如果必须重生成，先确认官方是否对失败/取消请求计费，并在日志里明确“本次是重新生成”。

### P1-2 云端档位仍显示「一键出图」轮播面板

**位置**：`web/src/components/generate/GenerateParamForm.vue:863`（`<QuickPoseBatchPanel ... />` 没有 `v-if="!isArk"`）

**影响**：

- 云端模式下仍可点“一键出图”，而该面板的逻辑是**本地线稿参考批处理**（`onQuickRun` 会套用本地档位、count、steps，并可能把模型切回本地未审查档位）；
- 若用户不点，它只是噪音；若误点，可能切换档位、提交 count>1 的云端批次（后端 `count` 必须 1），得到 400 或空转；
- 后续提交 `949f670` 已修（“云端档位隐藏「一键出图」轮播面板”），说明这是真实踩到的坑。

**修复建议**：`v-if="!isArk"`；补一条断言（云端档位下 `wrapper.text()` 不含“一键出图”）。

### P1-3 云端 fake seed 与「可复现」语义冲突

**位置**：`generation_service.py:313`（`seed_base = random.randint(...)`）、`:323-324`（写进 params）、`:361`（返回给前端）；`retry_run` / `regenerate_candidate` 仍按 seed 传递。

**影响**：

- Seedream 没有 seed 参数，但接口返回了一个随机 seed，历史/结果区可能显示“seed=123”；
- 用户以为同 seed 能复现，实际每次都是新图；
- `retry_run` / `regenerate_candidate` 的 API 语义仍是“同参复现”，对云端不成立。

**修复建议**：

- 云端 `params` 不写 `seed_base/seeds`，或写 `seed_supported=False`，返回 `seed=None`；
- 历史/结果区对云端显示“无 seed（每次随机）”，`retry_run`/`regenerate_candidate` 文案改为“重新生成”；
- 如果产品上需要“可复现”，云端档位不能承担，应明确引导回本地档位。

### P1-4 retry / 单张重出 / 带入表单丢失云端参数

**位置**：

- `generation_service.retry_run`（`:1322-1335`）的 payload 只有 `width/height/steps/cfg/count/...`，**没有 `size` / `watermark`**；
- `regenerate_candidate`（`:1367-1380`）同样没有；
- 前端 `applyRunPayload`（`GenerateParamForm.vue:690-734`）只恢复本地 `sizePresets`，不恢复 `cloudSize` / `cloudWatermark`。

**影响**：

- 用户用 `1K 方形` 或 `水印开` 跑了一次云端，点“再生成”会退回默认 `1536x2048` + 水印关；
- 从历史“带入表单”也会丢这两个云端参数；
- 用户会认为“同一批参数重跑”，实际参数已经变了。

**修复建议**：`retry_run` / `regenerate_candidate` 把 `params.get("size")` / `params.get("watermark")` 原样放进 payload；前端 `applyRunPayload` 识别 `payload.model_key` 为云端时恢复 `cloudSize`/`cloudWatermark`；补测试。

### P1-5 双人姿态与「1 张素材图」不匹配

**位置**：`generation_service._ark_framing`（`:235-240`）对 `system` 含“双人”返回“双人合照”；`_create_ark_run` 只接受 1 张素材图（`:269`）；包装模板写的是“图1中人物”（单数）。

**影响**：

- 姿势库含 275 条双人姿态；选到双人姿态时，只有一张素材图，模型可能把两个人合成/截断，或忽略其中一人；
- 前端不拦截，后端也不拦截，错误在出图后才暴露。

**修复建议**：一期在 `_create_ark_run` 拒绝 `system` 含“双人”的姿态（400，提示改用本地或换单人姿态），或在 UI 过滤；如果确实要支持双人，需要 2 张素材图和对应 prompt 模板，不能复用当前单人物模板。

### P1-6 取消无法在云端调用期间生效

**位置**：`image_gen._execute_seedream` 只在调用前（`:427`）和调用后（`:461`）检查 `_is_cancelled`；`SeedreamClient.generate` 内部最多 3 次尝试、每次 120s 超时 + 退避。

**影响**：

- 用户点取消后，任务可能继续跑几分钟（最坏 3×120s + 退避）；
- 拿到结果后虽然不落盘，但费用已经产生；
- 与本地 ComfyUI 链路“轮询期间可取消”的体验不一致。

**修复建议**：

- 给 `SeedreamClient.generate` 传入 `should_cancel` 回调，在每次重试前/请求前检查；或把 `generate` 包在可取消的 `asyncio.Task` 里，取消时 `await task` 并关闭连接；
- 缩短单次超时或把 `MAX_ATTEMPTS` 做成可配置；
- UI 明确“云端请求已发出，可能仍会计费”。

---

## 4. 中优先级问题（P2）

| # | 问题 | 位置 | 影响/建议 |
| --- | --- | --- | --- |
| P2-1 | 日预算按 **UTC 零点**重置，不是本地零点 | `generation_service.py:296` | 中国用户会在 08:00 才重置；建议用本地日期或明确文档 |
| P2-2 | 预算检查非原子；只查创建时，队列中的 run 不查 | `:294-309` | 并发/排队可超预算；建议加锁或执行前二次检查 |
| P2-3 | 档位尺寸 `1K/1.5K/2K` 被记成 1024/1536/2048 正方形 | `seedream_client.validate_size:96-110` | 历史/导出显示的宽高不一定等于实际输出；建议用响应 `data.size` 回写 |
| P2-4 | 执行时用 `settings.seedream_model`，不用 run 里的 `model_id` | `image_gen.py:438` | 提交后改配置会改变在跑/重试批次的实际模型；建议 `params["model_id"]` |
| P2-5 | 未把 `request_id` / `usage` / 耗时写回 params | `image_gen.py:442-488` | 只能从日志找，无法对账；建议写 `ark_request_id`/`usage`/`elapsed_s` |
| P2-6 | 图片预处理未做 EXIF 方向校正；透明图转 RGB 会变黑底 | `image_gen._prepare_ark_image:362-385` | 手机竖拍可能横向；透明线稿可能变黑；建议 `ImageOps.exif_transpose`，透明图先贴白底 |
| P2-7 | 审核错误只匹配英文关键词 | `seedream_client._error_from_response:129-166` | 中文“内容审核”可能落到“参数不合法”；建议同时匹配中文/错误码 |
| P2-8 | 未遵守 `Retry-After`；实际只退避 1/2 秒（文档写 1/2/4） | `seedream_client.py:231-245,315` | 429 时容易继续撞限流；建议读取响应头，退避上限可配 |
| P2-9 | 下载响应无大小上限 | `_download:302-311` | 异常大响应可能占内存；建议限制字节数 |
| P2-10 | prompt 截断到 600 字，官方建议中文 ≤300 字 | `build_prompt:320-330` | 过长会分散模型注意力；建议 300 字并优先保留用户要求 |
| P2-11 | `GenerateView` 成功横幅仍假设 ComfyUI 有值 | `web/src/views/GenerateView.vue:392` | ComfyUI 未启动但云端就绪时，横幅显示 `undefined / undefined GiB`；建议按 `status.ark.ready` 分支显示 |
| P2-12 | `MODEL_SPECS.max_refs=10`，云端实际只接受 1 张素材 | `models.py:87` vs `_create_ark_run:269` | 元数据/文档/前端不一致；建议云端档位标 `max_refs=2`（1 素材 + 1 姿态）或明确字段语义 |
| P2-13 | 云端口径的 `cloudCost` 在 cost 为 0 时不回退 0.12 | `GenerateParamForm.vue:328` | 后端缺字段时显示 ¥0.00；建议 `?? 0.12` 而不是 `?? 0` 链路 |
| P2-14 | `retry_run`/`regenerate_candidate` 不保留 `size/watermark` | 见 P1-4 | 已单列 P1；补测试防回归 |

---

## 5. 代码质量与死代码（P3）

| 项 | 位置 | 说明 |
| --- | --- | --- |
| `_MIME_BY_MAGIC` 常量未使用 | `seedream_client.py:36-40` | `_mime_of` 自己判断；删除或改用常量 |
| `seedream_max_poses` 配置未使用 | `config.py:112`、`.env.example:155` | 只有定义，没有读取；`max_poses` 只下发不消费 |
| `max_poses` 前端未使用 | `GenerateParamForm.vue` / `generate.ts:32` | 类型有字段，但 `ModelOption` 没接；等 P3 多姿态再启用 |
| `supportsSeed` / `supportsNegative` / `requiresUpload` 只赋值未读取 | `GenerateParamForm.vue:89-91,138-140` | 目前 UI 用 `isArk` 硬编码判断；建议要么用字段，要么删掉 |
| `_execute_seedream` 的 `spec` 参数未使用 | `image_gen.py:387` | 可删或用于读取 `cost_per_image`/`max_refs` |
| `.env.example` 多一个空行 | `backend/.env.example:145-146` | 纯格式 |
| `_get_client` 无并发保护 | `seedream_client.py:190-195` | 同一客户端并发调用时可能重复建连接；当前一任务一客户端，影响低 |

---

## 6. 做得好的地方（应保留）

1. **provider 分支位置正确**：`execute_image_gen` 在加载 ComfyUI 模板/显存守门之前就分流到 `_execute_seedream`，没有把云端硬塞进 ComfyUI 执行体。
2. **复用彻底**：`GenerationRun`、候选目录、缩略图、确认入库、历史/导出/清理全部复用，没有新增表/新页面。
3. **客户端职责窄**：`SeedreamClient` 只做 HTTP、data URI、MIME/魔数校验、错误归一化、下载；不碰 DB/文件系统，容易单测。
4. **错误归一化**：把 401/403/404/429/5xx/审核映射成中文，并区分 `retryable`，比直接把 HTTP 文本抛给前端好。
5. **图片处理不阻塞事件循环**：`_prepare_ark_image`、文件读写都走 `asyncio.to_thread`。
6. **隐私确认**：默认关闭 + 会话级确认 + 弹窗文案，交互上做到了“不静默上传”（安全面不在本文评价）。
7. **测试方法正确**：用 `httpx.MockTransport` 和 FakeClient，不花钱、可重复；覆盖了 429 重试、400 永久失败、401、data URI、尺寸校验、创建/执行主链路。

---

## 7. 建议修复顺序

| 顺序 | 动作 | 预估 | 验收 |
| --- | --- | --- | --- |
| 1 | 修 P0-1：把用户 prompt 拼进云端提示词，测试断言用户原文 | 0.5 h | 自定义描述出现在 `run.prompt` 与请求体 |
| 2 | 修 P0-2：准备阶段失败统一回写 `run.failed/error/status`；捕获通用异常 | 1 h | 素材缺失/姿势 503/坏图 → run failed，不卡 running |
| 3 | 修 P1-1：下载失败只重试下载，不重跑生成 | 1 h | mock 断言生成请求只调 1 次 |
| 4 | 修 P1-2：云端隐藏 `QuickPoseBatchPanel` | 5 min | 云端档位文本不含“一键出图” |
| 5 | 修 P1-3/P1-4：云端不返回 fake seed；retry/重出/带入表单保留 size/watermark | 1～2 h | 历史/重试参数一致 |
| 6 | 修 P1-5/P1-6：双人姿态拦截；取消回调 | 1～2 h | 双人姿态 400；取消 ≤ 一个超时周期 |
| 7 | P2 批量：预算本地日/原子性、model_id、request_id/usage、EXIF、审核中文、Retry-After、横幅 | 1 天 | 对应单测/手工验证 |
| 8 | P3 清理：死常量/死配置/未用字段 | 0.5 h | lint/自查 |

---

## 8. 测试缺口清单

| 缺口 | 对应缺陷 | 建议用例 |
| --- | --- | --- |
| 用户 prompt 未断言 | P0-1 | 创建 run 时传“换成红色背景”，断言 `run.prompt` / mock 请求体包含 |
| 准备阶段失败未覆盖 | P0-2 | 素材文件删除、姿势 503、坏图 → `run.status=failed` |
| 下载失败重试未覆盖 | P1-1 | 生成 200 + 下载 500，断言生成只调用 1 次 |
| 云端一键出图未覆盖 | P1-2 | 云端档位下 `wrapper.text()` 不含“一键出图” |
| fake seed 未覆盖 | P1-3 | 云端 create 返回 `seed` 为 None / 不写 seeds |
| retry/重出参数未覆盖 | P1-4 | `retry_run` 后新 run 的 `size/watermark` 与原 run 一致 |
| 双人姿态未覆盖 | P1-5 | 双人 system 的 pose → 400 |
| 取消未覆盖 | P1-6 | 取消后任务在超时内停止（可用 fake client 延迟） |
| 预算未覆盖 | P2-1/2 | 达到日上限 → 400；并发/排队场景 |
| 探测/横幅未覆盖 | P2-11 | ComfyUI down + ARK ready → 页面不显示 undefined |
| 前端提交/隐私未覆盖 | — | 首次提交弹隐私、确认后 emit；取消不 emit |
| 下载/URL 路径未覆盖 | P1-1 | `response_format=url` + content-type/魔数校验 |

---

## 9. 附：评审方法与限制

- **静态审查**：逐文件读 diff，并在提交快照上读完整函数（`git show <sha>:<path>`），核对调用方/被调方、状态机、异常路径、前后端契约。
- **未在提交快照上重跑测试**：该提交已在历史中，当前工作区含后续改动；测试结果以现有 CI/仓库为准。本文结论来自代码静态分析与现有测试覆盖对照。
- **未评估安全面**：按用户要求，API Key、隐私上传、内容审核、日志脱敏、访问控制等不在本文结论内。
- **后续提交**：`949f670`（云端隐藏一键出图）、`a288197`（多姿态）等可能修复了本文部分问题；如需按最新代码复核，请以最新提交为准。

---

## 10. 最终建议

**可以继续保留这条链路，但启用云端档位前必须完成 P0-1、P0-2、P1-1**；P1-2～P1-6 建议在同一轮修复，否则用户会遇到“描述不生效 / 批次卡住 / 重跑参数变了 / 取消没反应”这类难以解释的行为。修完后再按接入方案里的 10 素材 × 3 姿态做一次人工对比，确认质量与成本。

---

## 11. 修复记录（2026-11-03）

按 §7 建议顺序逐项修复，**全部 P0/P1/P2/P3 已落地**；实现说明见 [接入方案 §15](./豆包Seedream5.0Flash图生图接入方案.md)。

| 编号 | 结论 | 修复落点 | 验证 |
| --- | --- | --- | --- |
| P0-1 | 已修 | `generation_service._ark_prompt_text` 增 `user_prompt`，`_create_ark_run` 传 `payload["prompt"]`；用户要求优先于姿势细节 | `test_generation_seedream.py` 断言 `run.prompt` 含用户原文 |
| P0-2 | 已修 | `image_gen._execute_seedream` 全流程异常兜底，失败统一写 `run.status=failed` + `error` | 素材缺失/姿势 503/坏图三类用例 |
| P1-1 | 已修 | 生成与取图分离：生成按 `SEEDREAM_MAX_ATTEMPTS` 重试，下载单独重试 3 次且不重新生成 | `test_download_failure_retries_download_only` 断言 POST 只 1 次 |
| P1-2 | 已修（后续提交，保留） | 云端档位不渲染 `QuickPoseBatchPanel` | `GenerateParamForm.test.ts` 断言文本不含「一键出图」 |
| P1-3 | 已修 | 云端不写 `seed_base`/`seeds`、响应 `seed=null`；前端「无 seed（每次随机）」/「重新生成」文案 | 后端创建用例 + 前端三处文案用例 |
| P1-4 | 已修 | `retry_run`/`regenerate_candidate` 携带 `size`/`watermark`；前端 `applyRunPayload` 恢复云端参数 | 后端 retry 用例 + 前端带入历史用例 |
| P1-5 | 已修 | 后端双人姿态 400；前端选姿态过滤 + 提交前拦截 | 后端 400 用例 + 前端过滤用例 |
| P1-6 | 已修 | `SeedreamClient.generate` 支持 `should_cancel`（0.5s 轮询），取消置 cancelled | 客户端取消用例 + 执行器取消用例 |
| P2-1 | 已修 | 预算日改用本地零点（naive UTC 存储） | `_ark_budget_day_start` 单测 |
| P2-2 | 已修 | 创建时 `_ARK_BUDGET_LOCK` 串行化；执行前二次检查 | 执行前超预算用例（不发请求） |
| P2-3 | 已修 | 响应实际尺寸回写 `params.size/width/height` | 实际尺寸回写用例 |
| P2-4 | 已修 | 执行用 `params.model_id`，不再用全局配置 | 提交后改配置用例 |
| P2-5 | 已修 | 回写 `ark_request_id`/`ark_elapsed_s`/`ark_usage`/`ark_model_id` | 执行器落盘用例 |
| P2-6 | 已修 | EXIF 转正 + 透明贴白底 | 预处理单测（方向/白底/缩放） |
| P2-7 | 已修 | 审核关键词中英文都匹配 | 中文审核映射用例 |
| P2-8 | 已修 | 429 遵守 `Retry-After`，退避取较大值 | Retry-After 用例 |
| P2-9 | 已修 | 下载响应大小上限 32 MB | 超限拒绝用例 |
| P2-10 | 已修 | prompt 上限 300 字，用户要求优先 | 截断用例 |
| P2-11 | 已修 | `GenerateView` 增加「ComfyUI 未启动但云端就绪」横幅分支 | 前端横幅用例 |
| P2-12 | 已修 | 云端档位 `max_refs=1`，`/models` 与 `/status` 下发，前端按它校验 | `/models` 元数据用例 |
| P2-13 | 已修 | 云端费用 `costPerImage || 0.12` | 前端费用提示（既有用例覆盖） |
| P3 | 已修 | 删 `_MIME_BY_MAGIC`、`_execute_seedream` 的 `spec` 参数；`SEEDREAM_MAX_POSES` 经 `max_poses` 真正下发；能力字段被 UI 消费；`_get_client` 加锁 | 元数据用例 + lint/vulture |

**验证**：`PYTHON_BIN=py bash scripts/ci/test.sh` 13 项门禁全绿；后端新增/更新用例见各测试文件共 40+ 条断言；前端 `npm test` 全量通过。
