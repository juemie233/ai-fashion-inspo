# 接入 ComfyUI 一键生成图片 · 技术方案

> 版本 1.0 · 2026-10-21 · 状态：**设计定稿，待评审后按 P0→P2 分期实施**
>
> 定位：本文是**实施方案**，不是科普。每节都给出「改哪个文件、加哪张表、什么契约、怎么验收」，
> 以及**为什么这么定**（含被否决的选项）。外部工具安装见 `docs/图片生成模型安装清单.md`。

---

## 0. 决策摘要（先看这张表）

本文所有设计都围绕下面 7 条已确认决策展开，有异议先改这里：

| # | 决策点 | 结论 | 一句话理由 |
| --- | --- | --- | --- |
| 1 | 生成图归属 | **入素材库**（`source_type='generated'` + `is_ai_generated=1`） | 白拿列表/详情/打标/合集/向量/去重全套能力，不必重写一遍展示层 |
| 2 | 负样本口径 | **`collect_samples` 负样本排除 `is_ai_generated=1`** | 否则你丢掉的废图会教初筛器「AI 生成 = 垃圾」，误杀以后采集到的真素材 |
| 3 | 显存互斥 | **排队等待**（不自动卸载 Ollama） | 不打断正在跑的打标任务；等待状态对用户可见 |
| 4 | 一期能力 | **文生图 + 图生图（参考图）** | 参考图才能「拿现有单品拼一套穿搭」，这是场景刚需 |
| 5 | 模型档位 | Z-Image-Turbo int8（已装）+ **Qwen-Image 2.1 int8/w4a8（待装）** | 参考图只有 Qwen 系给得好；w4a8 编码器才塞得进 16 GB |
| 6 | Prompt 来源 | **手写 + 从素材标签反推**（可编辑） | 复用库里 1.3 万标签的沉淀，这是本项目相对裸 ComfyUI 的核心增量 |
| 7 | 入库节奏 | **先候选区，勾选后确认入库** | 与项目一贯的「宁缺毋滥、手动确认」一致，也复用 f2 那套交互 |

补充两条当次确认的硬约束：

- **Qwen-Image 2.1 是 Qwen Research License（默认仅研究/评估，商用需另授权）** —— 用户已确认按非商用自用处理；代码与 UI 里都要留痕，**不得**把 B 档用于对外商用产出。
- 显存实测：Z-Image int8 在 1024² 峰值 **15.1 GiB / 总 15.9 GiB**（余量 0.8 GiB）、2560×1440 峰值 15.2 GiB。**16 GB 是本方案的硬天花板**，所有默认值都按这个算。

---

## 1. 目标与非目标

### 1.1 目标

1. 在 Web 上一键出图：填 prompt（或从素材反推）→ 提交 → 任务中心看进度 → 候选区挑选 → 确认入库。
2. 生成图与采集素材**可区分**（来源、统计、学习口径），但**同等可用**（打标、合集、语义搜索、相似推荐）。
3. **可复现**：每次生成记录 prompt/negative/seed/steps/cfg/sampler/模型/工作流指纹，支持「同参数再生成」。
4. **参考图**：能拿 1~10 张现有素材（单品图/穿搭图）作为参考生成新图。
5. 显存冲突**不静默失败**：等不到显存要说清「在等什么、还要等多久」。

### 1.2 非目标（一期不做，避免范围失控）

| 不做 | 理由 |
| --- | --- |
| 任意工作流编辑器 / 节点图 | ComfyUI 自带 WebUI 就干这个，我们只需要「固定模板 + 参数注入」 |
| LoRA 训练、ControlNet 细粒度控制 | 需要的数据与算力另立项（TODO 里的 LoRA 条目） |
| 视频生成、动图 | 显存与时长都不现实 |
| 自动/定时出图、无人值守批量 | 与「手动确认」的产品口径冲突 |
| 云端/多机出图 | 单机自用工具 |
| 生成图自动打标 | 走现有批量分析任务即可，不新增入口 |

---

## 2. 现状盘点与复用矩阵

这一节是方案能「薄」的前提。**下表每一行都是已存在的代码**，方案只补缺口，不重造：

| 已有能力 | 位置 | 本方案怎么用 | 需改动 |
| --- | --- | --- | --- |
| 任务队列 + worker + 暂停/取消/重试 + 心跳租约 | `app/worker.py`、`app/services/task_runner.py`、`models/task.py` | 新增任务类型 `image_gen`，直接进 `TASK_HANDLERS` | 注册 1 行 + 新 runner |
| **GPU 互斥组** | `worker.py:79` `GPU_EXCLUSIVE_TASK_TYPES`（6 种） | `image_gen` 加入该集合 → **与打标/审核/向量回填全局串行**，天然不会两边抢显存 | 加 1 个类型名 |
| 卸载 Ollama 模型 | `routers/ai_models.py:319` `POST /api/ai/models/unload-model?model_name=` | 不作为默认策略（我们选排队等待），仅作为「用户手动加速」入口 | 无 |
| Ollama 驻留查询 | `GET {ollama_base_url}/api/ps`（`ai_models.py` 已在用） | 显存守门判断「谁占着显存」 | 抽出复用函数 |
| 入库管线 | `services/inspiration_create.py:101-166`（存盘→sha256→去重→质量状态→向量回填登记） | 生成图确认入库时走**同一套**，只是入口从 `UploadFile` 换成「已有本地文件」 | 新增 `create_inspiration_from_file()`，原路径不动 |
| 批次清单 + 预览确认 | `services/scraper/f2_results.py` + `f2_preview.py`（上一版刚落地） | 候选区/确认入库照抄「先预览再执行」的交互与文案 | 无（只抄模式） |
| 标签体系（1.3 万标签、权重、类别） | `models/tag.py`、`inspiration_tags` | 「从素材反推 prompt」的数据源 | 新 prompt builder |
| 质量审核 + 负样本学习 | `services/quality_learner.py:49` | **必须在负样本侧排除生成图**（决策 2） | 1 处 where + 注释 + 测试 |
| 统计口径 | `services/admin_stats_service.py` | 生成图单独计数，别混进「采集量」 | 加 1 个维度 |
| 前端图表/网格/轮询 | `DensityImageGrid`、`usePolling`、`ThumbCard` | 候选区网格直接复用 | 无 |
| Alembic 迁移链 | `backend/alembic/` | 新增 2 张表 + 1 个列 | 1 个 revision |

**结论**：真正要新写的只有 6 个文件（`comfyui/client.py`、`workflow.py`、`probe.py`、`prompt_builder.py`、`task_runners/image_gen.py`、`generation_service.py`）+ 1 个路由 + 1 个前端视图。

---

## 3. 总体架构

```
┌─────────────── Web (Vue) ───────────────┐
│ /generate  GenerateView.vue             │
│  ├─ 参数面板（prompt/尺寸/步数/seed）    │
│  ├─ 「从素材反推 prompt」              │
│  ├─ 参考图选择（1~10 张现有素材）       │
│  └─ 候选区网格（勾选 → 确认入库）       │
└──────────────┬──────────────────────────┘
               │ /api/generate/*
┌──────────────▼──────────────────────────┐
│ FastAPI                                 │
│  routers/generate.py                    │
│   ├─ services/generation_service.py     │  ← 建 run / 查候选 / 确认入库
│   ├─ services/comfyui/probe.py          │  ← 就绪检查、显存、模型自检
│   ├─ services/comfyui/prompt_builder.py │  ← 从素材标签反推
│   └─ services/task_runner.py            │  ← 建 image_gen 任务
└──────────────┬──────────────────────────┘
               │ TaskQueue(type=image_gen)  ← 与打标/审核同组 GPU 互斥
┌──────────────▼──────────────────────────┐
│ worker 进程                              │
│  task_runners/image_gen.py              │
│   1. 显存守门（排队等待）                │
│   2. comfyui/client.py → POST /prompt   │
│   3. 轮询 /history/{id}                 │
│   4. GET /view → 落盘 storage/generated │
│   5. 更新 generation_runs / 候选清单     │
└──────────────┬──────────────────────────┘
               │ HTTP 127.0.0.1:8188
┌──────────────▼──────────────────────────┐
│ ComfyUI（外部工具，用户手动启动）         │
│  模型：Z-Image-Turbo int8 / Qwen-Image 2.1│
└─────────────────────────────────────────┘

入库路径（确认后）：候选文件 → create_inspiration_from_file()
  → inspirations(source_type='generated', is_ai_generated=1, generation_run_id=…)
  → 复用现有：缩略图 / sha256 去重 / 向量回填登记 / 打标任务
```

**关键分工**：

- ComfyUI 只管「算」，不感知素材库；
- 后端负责「编排 + 状态 + 落盘 + 入库」，全部可被任务队列观测；
- 前端只管「参数 + 挑选」，不直接连 8188（**包一层后端代理**，避免 CORS 与端口暴露）。

---

## 4. 数据模型

### 4.1 为什么建表而不是只写批次清单

f2 通道用「目录 + JSON 清单」管批次，零迁移。但生成场景多出两个需求：

1. **按参数检索**：「上次那个 1328²、steps=12 的 prompt 再跑一次」需要 SQL 过滤；
2. **统计**：出图量、耗时、失败率、模型占比要能按时间聚合。

所以：**参数与批次进表**，图片文件仍落目录（DB 不存二进制）。候选区的「哪张对应哪个 seed」用**文件名编码**解决，不再多建一张 per-image 表（省一张表，代价是 seed 只能从文件名解析，够用）。

### 4.2 `generation_runs`（新表）

```sql
CREATE TABLE generation_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    kind              VARCHAR(16) NOT NULL,        -- t2i | i2i
    status            VARCHAR(16) NOT NULL,        -- queued|running|waiting_vram|done|failed|cancelled
    prompt            TEXT NOT NULL,
    negative          TEXT,
    params_json       TEXT NOT NULL,               -- {width,height,steps,cfg,sampler,scheduler,count,...}
    model_key         VARCHAR(32) NOT NULL,        -- z_image_int8 | qwen_2_1_i2i
    workflow_name     VARCHAR(64) NOT NULL,        -- 模板名：z_image_t2i | qwen_edit
    workflow_hash     VARCHAR(16) NOT NULL,        -- 模板文件 sha256 前 16 位（版本漂移可追溯）
    reference_ids     TEXT,                        -- JSON 数组：参考素材 id（i2i 用）
    batch_dir         VARCHAR(255) NOT NULL,       -- storage/generated/<run_id>
    total             INTEGER NOT NULL DEFAULT 0,
    done              INTEGER NOT NULL DEFAULT 0,
    failed            INTEGER NOT NULL DEFAULT 0,
    error             TEXT,
    task_id           INTEGER,                     -- 关联 TaskQueue.id（可空：任务记录被清）
    created_at        DATETIME NOT NULL,
    updated_at        DATETIME NOT NULL
);
CREATE INDEX ix_generation_runs_status   ON generation_runs(status);
CREATE INDEX ix_generation_runs_created  ON generation_runs(created_at);
```

### 4.3 `inspirations` 新增 1 列

```sql
ALTER TABLE inspirations ADD COLUMN generation_run_id INTEGER
    REFERENCES generation_runs(id) ON DELETE SET NULL;   -- 删 run 不删素材
CREATE INDEX ix_inspirations_generation_run_id ON inspirations(generation_run_id);
```

用途：详情页显示「本图由 run #123 生成，prompt/seed 可复现」，以及「同参再生成」的入口。**可空** —— 采集素材与手动上传永远为 NULL，不影响既有查询。

### 4.4 `source_type` 新枚举值 `generated`

必须同步改 3 处（项目约定，漏一处前端就显示英文）：

| 文件 | 改动 |
| --- | --- |
| `web/src/utils/sourceLabel.ts` | `generated: 'AI 生成'` |
| `mobile/utils/sourceLabel.ts` | 同上 |
| `backend/app/services/inspiration_query.py` | 若按 source_type 建了枚举校验，追加该值 |

### 4.5 迁移与回滚

- `alembic revision --autogenerate -m "add generation_runs and inspiration.generation_run_id"`
- **`downgrade()` 必须实现**（drop 索引 + drop 列 + drop 表），与最近两个迁移保持一致；
- 迁移后在**全新空库**上跑一次 `backend/scripts/check_fresh_migrations.py`（门禁已含）。

---

## 5. 后端模块设计

### 5.1 `app/services/comfyui/client.py` — HTTP 客户端

职责单一：把 ComfyUI 的 6 个 HTTP 接口包成有类型、有超时、有错误分类的 async 方法。

```python
class ComfyUIClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None: ...

    async def system_stats(self) -> dict          # 就绪探测 + 显存
    async def object_info(self, node: str) -> dict # 节点/模型存在性校验
    async def submit(self, workflow: dict) -> str  # POST /prompt → prompt_id
    async def poll(self, prompt_id: str) -> dict   # GET /history/{id}
    async def fetch_image(self, filename: str, subfolder: str = "") -> bytes  # GET /view
    async def cancel(self, prompt_id: str) -> None # POST /queue + /interrupt
```

**错误分类（必须区分，否则用户看到的技术债都是「出图失败」）**：

| 现象 | 判定 | 对用户的话 |
| --- | --- | --- |
| `ConnectError` / 超时 | ComfyUI 没启动 | 「ComfyUI 未启动：双击 `D:\AI\ComfyUI\run_nvidia_gpu.bat`」 |
| `/prompt` 返回 400 + `node_errors` | 工作流参数与节点不符（版本漂移/模型没下） | 「工作流与当前 ComfyUI/模型不匹配：<具体节点>」 |
| `/history` 里 `status_str == error` 且消息含 `Allocation on device` / OOM | 显存不足 | 「显存不足：请关闭浏览器标签或先卸载 Ollama 模型」 |
| 轮询超过 `image_gen_timeout`（默认 600 s） | 卡死 | 「生成超时（10 分钟），已取消」+ 主动 `/interrupt` |

### 5.2 `app/services/comfyui/workflow.py` — 模板与参数注入

一期用**两个固定模板**，随代码走（放 `app/services/comfyui/workflows/`，已登记目录）：

| 模板 | 节点链 | 用于 |
| --- | --- | --- |
| `z_image_t2i.json` | `UNETLoader → CLIPLoader(lumina2) → ModelSamplingAuraFlow(shift=3) → KSampler(8步,cfg=1,res_multistep/simple) → VAEDecode → SaveImage` | 文生图，6 s/张 |
| `qwen_edit.json` | Qwen-Image 2.1 edit 链（`TextEncodeQwenImageEdit` 多参考图 + `VAEEncode` 参考图） | 图生图/多参考拼穿搭 |

**注入方式**：模板里用占位符（`"__PROMPT__"`、`"__NEGATIVE__"`、`"__SEED__"`、`"__WIDTH__"` 等），
`inject(template, **params)` 做类型安全的替换；**不要**在前端拼 JSON（前端只传语义参数）。

**启动自检（必须有）**：服务启动时用 `/object_info` 校验「模板里出现的每个 class_type 都存在」，
缺一个就往前端报「ComfyUI 版本/节点不匹配」，并把 checklist 写到日志。ComfyUI 升级改名是这个方案最现实的风险来源。

### 5.3 `app/services/comfyui/probe.py` — 就绪与显存探测

```python
async def probe() -> ProbedStatus:
    """返回 {ready, comfyui:{version,vram_total,vram_free},
             models:{z_image_int8:"ok"/"missing", qwen_2_1_i2i:"ok"/"missing"},
             ollama_resident:[{name, size_gb}], reason}"""
```

- `/system_stats` 超时 **2 s**（不能把 UI 拖死）；
- `ollama_resident` 来自 `GET {ollama_base_url}/api/ps`，失败只降级（不影响出图判断）；
- `models` 通过 `/object_info/UNETLoader` 等节点里的下拉选项比对文件名，**比查磁盘准**（ComfyUI 只认它自己扫到的）。

### 5.4 `app/services/comfyui/vram.py` — 显存守门（决策 3 的落点）

状态机（写在 `task.result.stage` 里，前端直接显示）：

```
提交任务 → [检查显存]
    ├─ vram_free ≥ 所需(模型已驻留则更低阈值) → 直接出图
    ├─ 显存不足 且 有 Ollama 驻留 → stage=waiting_vram，每 10 s 复查
    │     ├─ 等到 → 继续
    │     └─ 超过 IMAGE_GEN_WAIT_VRAM_SECONDS（默认 900）→ 失败：
    │         「等待显存超时：Ollama 的 qwen3-vl:8b-instruct 占着 19 GB，
    │           可执行 ollama stop … 或稍后再试」
    └─ ComfyUI 未启动 → 立即失败（不傻等）
```

所需显存按模型给静态预估（`Z_IMAGE_INT8: 15.0 GiB`、`QWEN_I2I: 14.5 GiB`），**保守取实测峰值**，不动态算 —— 动态算容易算不准，宁可早判。

### 5.5 `app/services/comfyui/prompt_builder.py` — 从素材反推 prompt（决策 6）

输入 1~N 个素材 ID，输出**可编辑**的结构化 prompt：

```
读取 → inspiration_tags（按 tag.category 分组 + 权重）
     → caption（正文里已有的风格词）
     → dominant_colors（主色 → 中文色名映射）
     → media_type / rating（质量高的素材权重更高）
拼接模板（顺序固定，便于模型理解）：
  「<主体>，<单品细节>，<配色>，<场景/光线>，<风格词表>，<画质词>」
```

设计要点：

- **不做** LLM 扩写（一期）：多一跳 Ollama 调用既慢又抢显存，且失败路径更难测；
- 输出带 `parts`（每段来自哪个标签/字段），前端可逐段删改 —— 比"一大坨字符串"可控；
- 拼接规则与词表放常量（`PROMPT_STYLE_WORDS` 等），集中一处便于调参；
- 单测只测「结构化输入 → 确定性输出」，不碰模型。

### 5.6 `app/services/task_runners/image_gen.py` — 执行器

```python
async def execute_image_gen(db: AsyncSession, task: TaskQueue) -> None:
    run = await _load_run(db, task)
    await _wait_for_vram(run, task)          # 决策 3
    for i in range(run.total):
        seed = run.seed_base + i             # 可复现：seed 连续递增并编进文件名
        pid = await client.submit(inject(template, seed=seed, ...))
        await _poll_until_done(pid, task)     # 每 0.5 s，期间检查 paused/cancelled
        data = await client.fetch_image(...)
        path = run.batch_dir / f"{i+1:03d}_seed-{seed}.png"
        path.write_bytes(data)
        run.done += 1; task.progress = int(run.done * 100 / run.total); await db.commit()
    run.status = "done"
```

硬性要求（对齐现有 runner 的规范）：

- **每张落盘后立刻 commit** —— 中途崩溃/取消时已出的图不丢（候选区仍可用）；
- 支持取消：轮询里读 `task.status == "cancelled"` → 调 `/interrupt` + `/queue` 清理，已出的图保留；
- 支持暂停：`paused` 时在当前张完成后停，`pending` 续跑；
- **禁止**在 runner 里做入库（入库必须由用户确认触发，见 5.7）。

### 5.7 `app/services/generation_service.py` — 批次与入库

| 函数 | 职责 |
| --- | --- |
| `create_run(db, payload)` | 校验参数 → 固定工作流/模型 → 写 `generation_runs` → 建 `image_gen` 任务 → 返回 `{run_id, task_id}` |
| `list_candidates(db, run_id)` | 扫 `batch_dir` 里的 png，返回 `[{filename, seed, url, imported:bool}]`（`imported` 靠 `inspirations.generation_run_id` + 文件名判定） |
| `confirm_import(db, run_id, filenames, tag_ids, collection_id)` | 逐张 `create_inspiration_from_file()` → 打 `is_ai_generated=1` / `source_type='generated'` / `generation_run_id` → 可选挂标签/合集 → 返回 `{imported, skipped, ids}` |
| `discard_run(db, run_id)` | 删除候选目录（用户明确不要这批） |

**幂等**：`confirm_import` 重复调用同一文件名 → 靠 `content_hash` 去重命中 409 转「已存在，跳过」，不报错。

### 5.8 `app/services/inspiration_create.py` 新增入口

```python
async def create_inspiration_from_file(
    db, *, src_path: Path, source_type: str = "generated",
    generation_run_id: int | None = None, is_ai_generated: bool = False,
    quality_status: str | None = None, tag_ids: list[int] | None = None,
) -> Inspiration:
```

实现体是现有 `create_inspiration` 第 101~166 行的「复制文件 → 缩略图 → sha256 → 去重 → 向量回填登记」抽出来的**同一份逻辑**，`create_inspiration` 改为内部调用它（`UploadFile` 版本先落盘再委托）。

**为什么抽而不是复制**：入库的 5 个坑（孤儿文件清理、平台 ID 冲突、内容去重、质量状态、向量登记）踩一次就够了。

---

## 6. API 契约

统一前缀 `/api/generate`，全部 JSON，错误体沿用 `{"detail": "..."}`。

### 6.1 `GET /api/generate/status`

```json
{
  "ready": true,
  "comfyui": {"version": "0.42.1", "url": "http://127.0.0.1:8188",
              "vram_total_gib": 15.9, "vram_free_gib": 14.8},
  "models": {"z_image_int8": "ok", "qwen_2_1_i2i": "missing"},
  "ollama_resident": [{"name": "qwen3-vl:8b-instruct", "size_gib": 5.4}],
  "reason": null
}
```

`ready=false` 时 `reason` 给中文原因（「ComfyUI 未启动」「模型缺失：…」），前端据此**禁用运行按钮**并显示引导，而不是让用户点了才失败。

### 6.2 `POST /api/generate/runs`

```json
{
  "kind": "t2i",                    // t2i | i2i
  "prompt": "米色针织开衫，白色阔腿裤，……",
  "negative": "",
  "width": 1024, "height": 1024,
  "steps": 8, "cfg": 1.0, "seed": 0,   // seed=0 表示随机并写回实际值
  "count": 4,
  "model_key": "z_image_int8",
  "reference_ids": []               // i2i 必填 1~10 个素材 id
}
```

返回 `{"run_id": 12, "task_id": 345, "seed": 4829103, "batch_dir": "generated/12"}`。

**参数校验（服务端，不信任前端）**：

| 校验 | 规则 | 依据 |
| --- | --- | --- |
| 尺寸上限 | `width*height ≤ 1328*1328`（t2i）/ `≤ 1024*1024`（i2i） | 实测 2560×1440 已到 15.2 GiB，再大必 OOM |
| 单批张数 | `1 ≤ count ≤ 4` | 单张 6 s、4 张 ~30 s；更多请分批（避免一个任务占 GPU 太久） |
| steps | `1 ≤ steps ≤ 30` | Z-Image 8 步已够，超 30 无意义 |
| i2i 参考图 | `1 ≤ len(reference_ids) ≤ 10`，必须是未删除素材 | Qwen-Image 上限 10 张 |

### 6.3 其余端点

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/runs/{id}` | run 详情 + `task`（进度）+ `candidates[]` |
| `GET` | `/runs?limit=20` | 最近批次（生成历史） |
| `POST` | `/runs/{id}/confirm` | `{filenames: [...], tag_ids: [], collection_id: null}` → 入库 |
| `POST` | `/runs/{id}/retry` | 同参数重跑（可覆盖 seed） |
| `POST` | `/runs/{id}/cancel` | 取消任务 + 清理未完成项 |
| `DELETE` | `/runs/{id}` | 丢弃候选目录（未入库的图） |
| `POST` | `/prompt/preview` | `{inspiration_ids: []}` → `{prompt, parts[]}` 从素材反推 |
| `GET` | `/models` | 可用模型 + 推荐参数 + 预期耗时（前端填默认值用） |

### 6.4 与破坏性接口认证的关系

`POST /runs/{id}/confirm` 会写库但**不删任何东西**，`DELETE /runs/{id}` 只删自己生成的候选文件 —— 都不进 `DESTRUCTIVE_ROUTES`。
（若将来加「清空生成历史」，再单独评估。）

---

## 7. 前端设计

### 7.1 页面与路由

- 路由 `/generate`，视图 `web/src/views/GenerateView.vue`（目标 ≤ 400 行，只做编排）；
- 导航入口放「上传」之后：「生成」；
- 三个子组件：

| 组件 | 职责 | 约束 |
| --- | --- | --- |
| `components/generate/GenerateParamPanel.vue` | prompt/负向/尺寸预设/步数/cfg/seed/张数/模型 | 尺寸用**预设下拉**（1024²/1328²/竖版 768×1344），不给自由输入，避免用户填出必 OOM 的值 |
| `components/generate/GeneratePromptPanel.vue` | 从素材反推：选素材 → 预览分段 → 逐段编辑/删除 → 应用到 prompt | 分段可见（来自哪个标签） |
| `components/generate/GenerateCandidateGrid.vue` | 候选网格：缩略图 + seed + 勾选 + 单张重生成 + 下载 + 确认入库 | 复用 `DensityImageGrid`/`ThumbCard` |

- 状态逻辑抽 `composables/useImageGen.ts`（提交/轮询/候选/确认），组件不直接碰 axios。

### 7.2 交互状态机

```
[idle] --填参数--> [ready?]
   ready=false → 顶部黄条：<原因> + 「如何启动」折叠块，运行按钮置灰
   ready=true  → 可提交
[提交中] → 立即拿到 run_id + task_id → [生成中]（usePolling 2 s 拉 /runs/{id}）
   stage=waiting_vram → 进度条上方显示「等待显存：Ollama 占用 5.4 GiB，已等 2 分 10 秒」
[完成] → 候选网格出现 → 勾选 → 「确认入库」→ toast「已入库 3 张」（附「去素材库查看」链接）
[失败] → 错误卡片（分类文案 + 重试按钮 + seed 保留）
```

**轮询纪律**（沿用项目 `usePolling` 约定）：只在 `status ∈ {queued, running, waiting_vram}` 时轮询，进入终态立即停；离开页面自动停。

### 7.3 时间与列表规范

- 生成历史列表的时间列走 `renderTimeCell`（`utils/format.ts`），**单行不换行**（项目硬规则）；
- 空状态、错误、等待态文案全中文。

---

## 8. 显存编排（16 GB 的残酷现实）

### 8.1 三个模型档位在本机的账

| 档位 | 权重合计 | 实测/预估峰值 | 建议分辨率 | 单张耗时 |
| --- | --- | --- | --- | --- |
| Z-Image-Turbo int8（已装） | 11.35 GiB | **15.1 GiB（实测）** | 1024² 稳、1328² 可、2560×1440 无余量 | 6.1 s（1024²） |
| Qwen-Image 2.1（int8 扩散 + w4a8 编码器 + VAE，待装） | 14.3 GiB | ~15.5 GiB（预估） | **≤1024²** | 20~40 s（预估） |
| 二者**同时**驻留 | — | 必 OOM | — | — |

### 8.2 三层保护

1. **互斥组（结构性）**：`image_gen` 加入 `GPU_EXCLUSIVE_TASK_TYPES` → 与 `quality_check`/`batch_analyze`/`vector_backfill`/`face_*` 全局串行，从根上不会两边同时跑。
2. **排队等待（决策 3）**：Ollama 模型可能在上一次任务后仍驻留（`keep_alive`），守门逻辑等它自然过期或等用户手动 `ollama stop`。
3. **参数上限（表 6.2）**：尺寸/张数硬校验，前端只给预设，别让人试出 OOM。

### 8.3 为什么不自动 `unload-model`

技术上一条命令就能腾出 5~19 GB（接口现成）。但代价是：正在跑的 `quality_check` 任务会拿到空结果或重试，**用户看到的是「打标突然变慢/失败」**，且原因不可见。排队等待把选择权留给用户，且状态可见 —— 与项目「不静默改变别人正在跑的任务」的一贯风格一致。

---

## 9. 质量与学习口径（决策 1 + 2 的落地）

### 9.1 生成图的标记

| 字段 | 值 | 理由 |
| --- | --- | --- |
| `source_type` | `generated` | 与采集/上传区分；前端显示「AI 生成」 |
| `is_ai_generated` | `true` | 复用既有「只标记不拒绝」语义 |
| `quality_status` | `pending`（**不自动 approved**） | 生成图质量参差，必须过人工/批量审核 |
| `generation_run_id` | run id | 溯源/复现 |

### 9.2 负样本排除（必改）

`services/quality_learner.py` 的负样本当前是「垃圾桶里全部图片」。生成图进入垃圾桶多为「构图不满意」，
与「采集到的垃圾素材」是两回事。混在一起会让初筛器学到「AI 生成特征 = 垃圾」，误杀日后采集的优质素材。

```python
negative_rows = (
    await db.execute(
        select(Inspiration.id, Inspiration.trash_reason)
        .where(..., Inspiration.is_ai_generated.is_(False))   # ← 新增
    )
)
```

- 正样本**不**排除（与现有注释一致：AI 生成图可作合格样本）；
- 同一处补注释说明「若将来改为接受 AI 生成图作负样本，删这一行并更新注释」；
- 单测：造 1 张 AI 生成图 + 1 张采集图都进垃圾桶，断言只有采集图进负样本。

### 9.3 统计口径

`admin_stats_service` 增加 `generated_count`（`source_type='generated'`），素材总量拆「采集 / 上传 / 生成」三列 —— 否则「采集量」会悄悄虚高，看不出真实采集进度。

---

## 10. 配置项（`app/config.py` 新增，全部走 `.env`）

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `comfyui_base_url` | `http://127.0.0.1:8188` | 只允许本机（安全约束） |
| `comfyui_timeout` | `10` | 单次 HTTP 超时（秒） |
| `image_gen_timeout` | `600` | 单张生成总超时（秒），超时主动 interrupt |
| `image_gen_wait_vram_seconds` | `900` | 排队等待显存上限 |
| `image_gen_poll_interval` | `0.5` | 轮询间隔（秒） |
| `image_gen_keep_candidates_days` | `7` | 未确认候选目录的保留天数（清理任务用，二期） |

---

## 11. 测试策略

| 层 | 用例 | 工具 |
| --- | --- | --- |
| `client.py` | 提交成功/400 节点错误/500 OOM/超时/连接拒绝 各一条 | `httpx.MockTransport` |
| `workflow.py` | 占位符全部替换、缺参数报错、模板 hash 稳定、`object_info` 缺节点时报错 | 纯单测 |
| `probe.py` | ComfyUI 不可用降级、模型缺失、Ollama 未启动不影响判断 | mock client |
| `vram.py` | 足够→直接过；不足→waiting_vram；超时→明确失败文案 | 注入假时钟 |
| `prompt_builder.py` | 标签分组顺序稳定、无标签时回退 caption、主色映射 | 纯单测 |
| `image_gen.py` | 正常出 2 张、第 2 张失败保留第 1 张、取消后不再提交、暂停续跑 | 假 client |
| `generation_service` | 幂等 confirm、重复入库跳过、参数校验边界 | 真 sqlite（项目 conftest） |
| `quality_learner` | AI 生成图不进负样本（决策 2 回归） | 真 sqlite |
| 前端 | `useImageGen` 状态机、轮询停止、确认入库调用 | vitest |

**门禁不变**：`bash scripts/test.sh` 11 项；覆盖率不得低于当前 70%。

---

## 12. 上线路径（每期可独立交付、独立验收）

### P0 · 最小可用（预计 1.5~2 天）

1. 装 Qwen-Image 2.1（int8 扩散 + w4a8 i2i 编码器 + VAE，合计 14.3 GB，走 hf-mirror + sha256 校验）；
2. `client.py` + `workflow.py`（先只做 `z_image_t2i`）+ `probe.py`；
3. `image_gen` 任务类型 + `GPU_EXCLUSIVE_TASK_TYPES` 加入；
4. `generation_runs` 表 + `inspirations.generation_run_id` 迁移；
5. `/generate` 页（参数 + 候选区 + 确认入库）；
6. 负样本排除 + 统计拆列。

**验收**：UI 上 1024² 出 4 张 → 勾 2 张入库 → 素材库能看到（来源「AI 生成」）→ 垃圾桶丢 1 张后跑质量学习，负样本数不增加。

### P1 · 复现与效率（预计 2 天）

1. 「从素材标签反推 prompt」（含分段编辑）；
2. 历史批次列表 + 同参数重跑 + 单张重生成（换 seed）；
3. 等待显存的进度可见、取消/暂停联调；
4. 候选目录过期清理任务。

**验收**：选 3 张穿搭素材 → 反推出 prompt → 出 2 张 → 「同参再生成」得到同风格不同 seed 的图。

### P2 · 参考图（预计 2~3 天）

1. `qwen_edit.json` 模板 + `workflow.py` 的 i2i 注入（参考图上传/引用）；
2. 前端参考图选择（从素材库多选 1~10 张）；
3. 参数预设保存（「我的出图配方」）—— 存 `generation_runs` 的一条特殊记录或新表，届时再定；
4. 出图耗时/失败率统计面板。

**验收**：3 张单品图 → 生成 1 套搭配图；参数与耗时如实记录；不 OOM。

---

## 13. 风险与对策

| 风险 | 触发概率 | 影响 | 对策 |
| --- | --- | --- | --- |
| ComfyUI 升级导致节点/参数改名 | 中 | 出图全线失败 | 模板随代码版本化 + `workflow_hash` 记录 + 启动自检缺节点即报「版本不匹配」 |
| 16 GB 显存边缘（余量 0.8 GiB） | **高** | OOM | 尺寸/张数硬上限；互斥组；`waiting_vram` 可见；文档写清实测值 |
| Qwen-Image 许可（非商用） | 确定 | 合规 | UI 与文档标注；不改默认模型为 Qwen（Z-Image 仍是默认） |
| 生成图污染统计/学习 | 中 | 数据口径失真 | 决策 1/2：来源分离 + 负样本排除 + 统计拆列 |
| 候选区磁盘膨胀 | 中 | 磁盘满 | `keep_candidates_days=7` + 丢弃按钮 + 每日清理任务（P1） |
| 用户误以为「点了就入库」 | 中 | 困惑 | 两段式文案：「已生成 4 张，勾选后入库」 |
| worker 与 ComfyUI 双进程互相干扰（用户手动 kill ComfyUI） | 中 | 任务失败 | 失败文案明确「ComfyUI 已退出」+ 重试按钮 |

---

## 14. 待定 / 下一轮再议

1. **预设管理**（「我的出图配方」）放哪：复用 `generation_runs` 打标记，还是单独 `generation_presets` 表 —— 等 P2 有真实使用数据再定。
2. **生成图是否进「抖音入库自动收藏」这类智能合集**：当前不自动挂，避免污染用户的采集归档。
3. **是否写 sidecar json**（每张图旁存 params）：ComfyUI 的 PNG 已内嵌完整工作流，一期不重复存；若将来要对外导出再补。
4. **多卡/多实例**：`comfyui_base_url` 已是配置项，将来可扩成列表 + 负载分配，但一期单实例。

---

## 附录 A · 本次改动文件清单（预估）

```
backend/
  alembic/versions/xxxx_add_generation_runs.py      新增
  app/config.py                                      +6 配置项
  app/models/generation.py                           新增（GenerationRun）
  app/models/inspiration.py                           +generation_run_id
  app/routers/generate.py                            新增（8 个端点）
  app/schemas/generation.py                          新增
  app/services/comfyui/__init__.py                   新增
  app/services/comfyui/client.py                     新增
  app/services/comfyui/workflow.py                   新增
  app/services/comfyui/workflows/z_image_t2i.json    新增
  app/services/comfyui/workflows/qwen_edit.json      新增（P2）
  app/services/comfyui/probe.py                      新增
  app/services/comfyui/vram.py                       新增
  app/services/comfyui/prompt_builder.py             新增（P1）
  app/services/generation_service.py                 新增
  app/services/task_runners/image_gen.py             新增
  app/services/inspiration_create.py                 抽出 create_inspiration_from_file
  app/services/quality_learner.py                    负样本排除 AI 生成图
  app/services/admin_stats_service.py                生成量单独计数
  app/services/task_runner.py                        TASK_HANDLERS + image_gen
  app/worker.py                                      GPU_EXCLUSIVE_TASK_TYPES + image_gen
  tests/test_unit_comfyui_client.py                  新增
  tests/test_unit_comfyui_workflow.py                新增
  tests/test_unit_image_gen_runner.py                新增
  tests/test_unit_generation_service.py              新增
web/
  src/views/GenerateView.vue                         新增
  src/components/generate/GenerateParamPanel.vue     新增
  src/components/generate/GeneratePromptPanel.vue    新增（P1）
  src/components/generate/GenerateCandidateGrid.vue  新增
  src/composables/useImageGen.ts                     新增
  src/api/generate.ts                                新增
  src/router/index.ts                                +/generate
  src/utils/sourceLabel.ts                           +generated
mobile/utils/sourceLabel.ts                          +generated
docs/ComfyUI出图接入方案.md                          本文
```

## 附录 B · 与既有约定的对照（自查表）

| 项目约定 | 本方案是否遵守 | 说明 |
| --- | --- | --- |
| 服务启停由用户手动 | ✅ | ComfyUI 由用户双击 bat；后端只探测不启动 |
| 新文件放已登记目录 | ✅ | 全部在 `backend/app/**`、`tests/`、`web/src/{views,components,composables,api,utils}`、`docs/` |
| DB 变更走 Alembic | ✅ | 1 个 revision，含 downgrade |
| 破坏性接口需 api key | ✅ | 本方案无不可恢复删除（`DELETE /runs/{id}` 只删自己的候选文件） |
| 时间列 nowrap | ✅ | 生成历史用 `renderTimeCell` |
| 中文注释/UI | ✅ | — |
| 核心链路补测试 | ✅ | 第 11 节；负样本改动必须带回归用例 |
| 模块 ≤ 1000 行、视图 ≤ 800 行 | ✅ | 最大的 `image_gen.py` 预估 ~250 行 |
