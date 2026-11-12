# 豆包 Seedream 5.0 Flash 图生图接入（方案 + 代码评测）

> **合并记录（2026-11-12）**：本文由下列 2 份文档合并而成，原文件已删除，正文逐字保留；只减少入口数量，不改写任何结论。
>
> - §1 ← `features/豆包Seedream5.0Flash图生图接入方案.md`
> - §2 ← `features/豆包Seedream5.0Flash提交代码评测-2026-11-03.md`

第 1 节是接入方案与实施状态，第 2 节是对该提交的逐文件代码评审与修复记录。

---

## 1. 接入方案（素材图 + 姿态图 → 目标姿态）

> 原文件：`features/豆包Seedream5.0Flash图生图接入方案.md`｜原题：接入豆包 Seedream 5.0 Flash 图生图（素材图 + 姿态图 → 目标姿态）

> **状态**：实施中（2026-11-02）。P0 真实联调已通过（3/3 成功、1 例审核拒绝）；P1 后端、P2 页面、P3 多姿态均已落地；P4 对比实验待做。
> **结论先行**：Seedream 5.0 Flash 支持「2～10 张参考图 + 提示词 → 单张图」，正好覆盖「1 张素材图 + 1 张姿态图」的换姿势场景；
> 按张计费约 **¥0.12/张**，1K/1.5K/2K 同价，参考图不额外收费；但它**没有 seed / 负向词 / steps / cfg**，且素材图会离开本机上传到火山引擎方舟。
> 因此建议做成**默认关闭、逐次确认的云端通道**，与本地 ComfyUI 通道并存，不替代本地链路。
>
> 依据：模型详情页 <https://ark.volcengine.com/region:cn-beijing/model/detail?Id=doubao-seedream-5-0-flash>、
> 图片生成 API <https://console.volcengine.com/ark/region:cn-beijing/docs/image-generation-api>、
> 提示词指南 <https://ark.volcengine.com/region:cn-beijing/docs/ark/seedream-4-0-5-0-prompt-guide>。
> 参数与价格以控制台实时数据为准；本文写作时的公开资料：模型 ID `doubao-seedream-5-0-flash-260915`，¥0.12/张。

---

### 0. 决策摘要（先看这张表）

| # | 决策点 | 结论 | 一句话理由 |
| --- | --- | --- | --- |
| 1 | 用途 | **素材图 + 姿态图 → 单张目标姿态图** | 与现有「换姿势」场景完全一致，不引入新交互概念 |
| 2 | 接入方式 | **复用现有 `generation_runs` 出图链路**，给 `MODEL_SPECS` 增加 `provider="ark"` 档位 | 历史、候选区、入库、重试、清理全部白拿，不另造一套 |
| 3 | 参考图顺序 | `image[0]=素材图`，`image[1]=姿态图`；prompt 用「图1 / 图2」指代 | 官方多图指南按传入顺序用「图1、图2」引用，顺序必须固定 |
| 4 | 提示词 | 固定包装模板 + `pose_prompts.prompt`（已有 1,439 条反推提示词） | 复用既有姿态描述缓存，减少手写与漂移 |
| 5 | 多姿态 | **一个姿态 = 一次请求**；前端循环提交 N 个 run，或 P1 增加批量接口 | 模型单次只出一张，`n` 不可依赖；逐姿态提交天然可重试、可比较 |
| 6 | 隐私边界 | **默认关闭**；首次使用必须弹窗确认「素材将上传火山引擎」 | 项目原边界是「素材不出内网」，云端通道属于边界变更，必须显式同意 |
| 7 | 可复现性 | 云端**无 seed**，只能重roll，不能同参复现 | 需要复现时继续用本地 ComfyUI 档位 |
| 8 | 一期范围 | 单人素材、1 张素材 + 1 张姿态、单张输出；不做组图/流式/视频 | 与官方能力边界对齐，避免一期范围失控 |

---

### 1. 目标与非目标

#### 1.1 目标

1. 在现有出图页新增「豆包 Seedream 5.0 Flash（云端）」档位：选 1 张素材图 + 1 个姿态 → 生成 1 张目标姿态图。
2. 一次可选择多个姿态，批量提交；每个姿态独立成批，历史里能按姿态对比。
3. 复用现有候选区、确认入库、生成历史、缩略图、清理、统计能力。
4. 成本可预期：提交前显示「N 个姿态 × ¥0.12」，提交后记录实际张数与耗时。
5. 隐私可控：默认关闭；每次云端生成前明确告知「素材图 + 姿态图将上传到火山引擎方舟」，不做静默上传。

#### 1.2 非目标（一期不做）

| 不做 | 理由 |
| --- | --- |
| 用云端替换本地 ComfyUI | 本地可复现、零单张成本、素材不出机；两条通道定位不同 |
| 组图 / 流式输出 | Seedream 5.0 pro / flash **不支持** `sequential_image_generation` 与流式 |
| 视频生成 | 不在本需求范围 |
| 自动批量无人值守 | 与项目「手动确认、宁缺毋滥」的口径冲突，也放大云端费用 |
| 绕过云端内容审核 | 姿态图库含成人向体系，被拦截时只提示原因并建议改用本地链路，不做规避 |
| 保存云端返回 URL | 官方 URL 24 小时失效，必须立即下载到本地候选目录 |

---

### 2. 模型能力与硬限制

#### 2.1 能力（与换姿势相关的部分）

| 能力 | Seedream 5.0 Flash |
| --- | --- |
| 文生图 | 支持 |
| 单图生图 | 支持（1 张参考图 + prompt） |
| 多图生图 | **支持 2～10 张参考图 + prompt → 单张图** |
| 交互编辑 | 支持坐标 / 框选 / 箭头（本期不用） |
| 图层拆分 | 支持（本期不用） |
| 透明背景 | 支持（仅单张带 alpha 图生图，本期不用） |
| 组图 / 流式 / 联网搜索 | **不支持**（5.0 pro / flash） |

#### 2.2 输入输出硬限制

| 项 | 取值 | 备注 |
| --- | --- | --- |
| 接口 | `POST https://ark.cn-beijing.volces.com/api/v3/images/generations` | OpenAI 风格 JSON，不是 multipart |
| 鉴权 | `Authorization: Bearer $ARK_API_KEY` | API Key 只放后端 `.env`，不进前端 |
| 参考图字段 | `image`: string 或 string[] | 2～10 张；本文固定 2 张 |
| 参考图格式 | URL 或 `data:image/<fmt>;base64,...` | `<fmt>` 小写；本地文件用 base64 |
| 图片格式 | jpeg、png、webp、bmp、tiff、gif、heic、heif | 姿态线稿是 PNG，素材图常见 JPEG/PNG |
| 单图大小 | ≤ 30 MB | base64 会膨胀约 33%，建议先压到 ≤ 5 MB |
| 单图像素 | 总像素 196 ～ 3600 万；宽高比 1/16～16；宽高 > 14 px | 正常素材都满足 |
| 输出张数 | **每次 1 张** | 不要依赖 `n`；多姿态必须多次请求 |
| 输出尺寸 | `size`: `1K` / `1.5K` / `2K`（默认 2K），或 `宽x高` | 显式像素时总像素 921,600～4,624,220，宽高比 1/16～16 |
| 输出格式 | `output_format`: `png` / `jpeg` | 建议 `png`，与候选文件命名一致 |
| 返回方式 | `response_format`: `url` / `b64_json` | `url` 24 小时失效，必须立即下载 |
| 水印 | `watermark`: 默认 `true` | 本地自用建议 `false`；对外分发需自行确认合规 |
| 提示词长度 | 中文建议 ≤ 300 字，英文 ≤ 600 词 | 平台是「建议」，不是硬拒绝；超过会分散模型注意力 |
| 计费 | 约 **¥0.12/张**，参考图免费，1K/1.5K/2K 同价 | 以控制台实时价格为准；失败是否计费以官方为准 |

> **没有的参数**：`seed`、`negative`、`steps`、`cfg`、`sampler`、`scheduler`、`shift`、`n`。
> 这决定了后端不能照搬 ComfyUI 的参数校验与「同参复现」语义。

#### 2.3 限流

- 官方按**账号 / 推理接入点**维度限制 RPM、TPM 与并发；超限返回 **429**，响应头可能带 `Retry-After`。
- 公开资料没有 5.0 flash 的确切 RPM/并发数字；BytePlus 同源资料提到 Seedream 系列理论 **500 IPM**，仅供参考。
- 实际配额以控制台「开通管理 / 限流配额」为准；一期按**串行 + 最多 2 并发**设计，遇 429 指数退避。

---

### 3. 两图输入的语义约定

#### 3.1 顺序即角色

官方多图指南用「图1、图2」按传入顺序引用。本项目固定：

| 顺序 | 内容 | 作用 | prompt 指代 |
| --- | --- | --- | --- |
| `image[0]` | 素材图（人物照片） | 提供长相、发型、肤色、服装 | **图1** |
| `image[1]` | 姿态图（线稿 / 姿势参考） | 提供身体朝向、重心、四肢位置、镜头视角 | **图2** |

> 不要交换顺序。顺序反了，模型会把线稿人物当主体，输出「线稿风格的人」。

#### 3.2 提示词模板

基础模板（服务端统一拼接，前端可编辑但不要删角色声明）：

```text
保持图1中人物的长相、发型、肤色和服装完全不变，让人物按图2的姿势重新拍摄。
图2只作为姿势参考：只参考身体朝向、重心、四肢位置、手部动作和镜头视角；
不要复制图2的线稿风格、灰色背景、关节球和人体模型外观。
写实摄影，{全身/半身}构图，自然光，皮肤与服装细节清晰，画面干净。
姿势细节：{pose_prompts.prompt 或 pose_gallery 的 name/angle 描述}
```

要点：

1. **先声明保持什么，再声明要改什么**——官方提示词指南对编辑类任务的建议；不写「保持长相/服装」，模型很容易换脸或换衣服。
2. **显式排除线稿特征**——姿态图是结构线稿，不写「不要线稿/关节球/灰底」，输出可能带线稿风格。
3. **不要编造服装细节**——写「图1的服装」即可，让模型从素材图读取；编造颜色/款式会与素材冲突。
4. **长度控制**——`pose_prompts.prompt` 约 80～150 字，包装模板约 100 字，合计约 200～250 字，在官方建议的 300 字以内。
5. **成人向姿态**——姿态库含「性感系」等体系，云端审核可能拦截；这是预期行为，不做绕过。提示词只描述姿势，不堆叠露骨形容词。

#### 3.3 线稿泄漏的兜底方案

如果实测发现输出仍带线稿/人体模型风格，按顺序尝试：

| 方案 | 做法 | 代价 |
| --- | --- | --- |
| A. 强化提示词 | 模板里再加「最终画面必须是真实照片，不能出现任何线条、关节球、灰色背景」 | 零成本，先试 |
| B. 姿态图预处理 | 把线稿转成纯黑剪影（白底黑形），去掉关节球与线条细节 | 需要一次图像处理，可能降低姿势精度 |
| C. 姿态图 + 姿态描述 | 保留线稿，同时把 `pose_prompts` 的完整描述放进 prompt | 已经是默认方案，先看 A 是否足够 |
| D. 不用姿态图 | 只用 `pose_prompts` 文本描述姿势 | 姿势保真可能下降，作为对照 |

建议 P0 手工验证时同时跑 A/B 两组，各 3 个姿态，肉眼对比线稿残留与姿势保真。

---

### 4. API 接入

#### 4.1 请求示例（本地图片 → base64）

```python
import base64
from pathlib import Path

import httpx


def data_uri(path: Path, mime: str) -> str:
    """把本地图片转成 Ark 接受的 data URI（格式名必须小写）。"""
    payload = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{payload}"


body = {
    "model": "doubao-seedream-5-0-flash-260915",
    "prompt": (
        "保持图1中人物的长相、发型、肤色和服装完全不变，让人物按图2的姿势重新拍摄。"
        "图2只作为姿势参考：只参考身体朝向、重心、四肢位置、手部动作和镜头视角；"
        "不要复制图2的线稿风格、灰色背景、关节球和人体模型外观。"
        "写实摄影，全身构图，自然光，皮肤与服装细节清晰，画面干净。"
    ),
    "image": [
        data_uri(Path("material.jpg"), "image/jpeg"),
        data_uri(Path("pose.png"), "image/png"),
    ],
    "size": "1536x2048",
    "output_format": "png",
    "response_format": "url",
    "watermark": False,
}

resp = httpx.post(
    "https://ark.cn-beijing.volces.com/api/v3/images/generations",
    headers={"Authorization": "Bearer " + ARK_API_KEY},
    json=body,
    timeout=120,
)
resp.raise_for_status()
data = resp.json()
image_url = data["data"][0]["url"]  # 24 小时内有效，立即下载
```

#### 4.2 响应结构（字段以实际返回为准）

```json
{
  "model": "doubao-seedream-5-0-flash-260915",
  "created": 1784696685,
  "data": [
    {
      "url": "https://...",
      "size": "1536x2048",
      "output_format": "png"
    }
  ],
  "usage": {
    "input_images": 2,
    "generated_images": 1
  }
}
```

- `response_format="b64_json"` 时，`data[0].b64_json` 是 Base64 图片，可省一次下载请求；2K PNG 的 JSON 会比较大。
- 建议默认 `response_format="url"` + 立即下载；下载失败再降级 `b64_json` 重试一次（会再计费，慎用）。

#### 4.3 错误处理

| 状态/错误 | 含义 | 处理 |
| --- | --- | --- |
| 400 | 参数不合法（size/图片格式/数量） | **永久失败**，把中文原因写进 `run.error` |
| 401 / 403 | API Key 无效、未开通模型、无权限 | **永久失败**，提示检查 `.env` 与控制台开通状态 |
| 404 | 模型 ID 不存在 / 未开通 | 永久失败，提示核对模型 ID |
| 429 | 限流 | **可重试**：指数退避 1s/2s/4s + 抖动，优先遵守 `Retry-After` |
| 5xx | 服务端错误 | 可重试，最多 3 次 |
| 超时 / 网络错误 | 连接失败 | 可重试，最多 3 次 |
| 内容审核拒绝 | 图或 prompt 被拦截 | **永久失败**，提示「云端审核未通过，请换素材/姿态或改用本地链路」，不做绕过 |

> 解析 Ark 的 `error.code` / `error.message`，映射成中文；不要把英文原文直接弹给用户。

---

### 5. 后端接入设计

#### 5.1 总体思路：复用 `generation_runs`，加一个 provider 分支

现有出图链路：

```text
POST /api/generate/runs
  → generation_service.create_run（校验参数、建 GenerationRun、建 TaskQueue）
  → worker: task_runners/image_gen.execute_image_gen
  → ComfyUIClient 提交工作流、轮询、下载候选图
  → storage/generated/<run_id>/*.png
  → 候选区 / 确认入库 / 历史
```

云端接入只替换「执行器里的生成动作」，其余不变：

```text
execute_image_gen
  ├─ spec.provider == "comfyui" → 现有逻辑（显存守门 + 上传参考图 + 工作流）
  └─ spec.provider == "ark"     → _execute_seedream（准备图片 → 调 Ark → 下载候选图）
```

**不做**：新表、新候选目录、新入库逻辑、新历史页。`GenerationRun` 的 `model_key` 是字符串，新增档位不需要迁移。

#### 5.2 新增模型档位

在 `backend/app/services/comfyui/models.py` 的 `MODEL_SPECS` 增加（模块名是历史遗留，目前是全局档位表）：

```python
"seedream_5_0_flash": {
    "label": "豆包 Seedream 5.0 Flash（云端，约 ¥0.12/张）",
    "provider": "ark",
    "kind": "i2i",
    "max_refs": 10,          # 官方 2~10 张；本项目一期固定 2 张
    "max_poses": 10,         # 前端一次最多选 10 个姿态
    "defaults": {
        "width": 1536,
        "height": 2048,
        "steps": 1,          # 占位：云端不使用，保留字段兼容现有校验
        "cfg": 1.0,
        "sampler": "ark",
        "scheduler": "ark",
        "shift": 0.0,
    },
    "cost_per_image_cny": 0.12,
    "supports_seed": False,
    "supports_negative": False,
    "requires_upload": True,  # 前端据此显示隐私提示
},
```

> `steps/cfg/sampler/scheduler/shift` 只是为了让现有 `_validate_params` 与前端默认值不炸；云端执行器不读取这些字段。
> 更干净的做法是把 `MODEL_SPECS` 迁到 `app/services/image_models.py`，但那是独立重构，一期不做。
>
> **必须同步改探测逻辑**：`backend/app/services/comfyui/probe.py` 目前遍历 `MODEL_SPECS` 并对每一项执行
> `wf.load_template(spec["workflow"])`。新增 `provider="ark"` 的档位后，必须跳过非 ComfyUI 档位
> （例如 `if spec.get("provider", "comfyui") != "comfyui": status.models[key] = "n/a"; continue`），
> 否则打开出图页就会因缺少 `workflow` 键直接报错。`scripts/` 里遍历档位的工具同样要按 provider 过滤。

#### 5.3 `generation_service.create_run` 的改动

按 provider 分支校验，不要把 ComfyUI 的规则套到云端：

| 校验项 | 云端（ark）规则 |
| --- | --- |
| `kind` | 必须是 `i2i` |
| `reference_ids` | 一期 **恰好 1 张素材图**；有多张素材/服装参考时再放开 |
| `pose_id` | 一期 **必填**（本需求就是换姿势） |
| `count` | 必须为 1（云端单次只出一张） |
| `size` | 新增可选字段；缺省用 `width x height` 拼；显式像素需满足总像素 921,600～4,624,220、宽高比 1/16～16 |
| `steps/cfg/sampler/...` | 忽略，不报错（前端可能仍带默认值） |
| `seed` | 忽略，仅存档展示；不得声称可复现 |
| `negative` | 云端无负向词；若前端传了，追加到正向 prompt 末尾「避免出现：…」，并截断到 300 字以内 |
| `prompt` | 包装模板 + `pose_prompts.prompt`；总长建议 ≤ 300 字 |
| 云端开关 | `ark_enabled=false` 或 API Key 为空时直接 400「云端出图未启用/未配置 key」，不建任务、不入队 |

`create_run` 里还需要：

1. 姿势图校验：`await pose_gallery.get_pose(pose_id)` 确认存在；**不要**在这里下载图片（避免拿着数据库事务等网络）。
2. 姿势提示词：优先 `pose_prompts.prompt`（已有缓存表），没有则用 `_pose_text_prompt(pose)` 兜底。
3. 不加载 ComfyUI 工作流：`workflow_name="ark_seedream"`，`workflow_hash=f"ark:{settings.seedream_model}"`，跳过 `wf.load_template`。
4. `params_json` 增加：`provider: "ark"`、`size`、`watermark`、`pose_id`、`model_id`。
5. 建任务与返回结构不变，前端无需感知 `task_factory`。

#### 5.4 新增 `backend/app/services/seedream_client.py`

职责单一：HTTP 调用 + 错误归一化，不碰数据库、不碰文件。

```python
class SeedreamError(Exception):
    """Ark 调用失败；retryable 决定任务是否重试。"""

    def __init__(self, message: str, *, retryable: bool, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class SeedreamClient:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 120.0): ...

    async def generate(
        self,
        *,
        prompt: str,
        images: list[bytes],       # 顺序：素材图在前，姿态图在后
        size: str,
        output_format: str = "png",
        watermark: bool = False,
    ) -> bytes:
        """调用 Ark 图片生成，返回图片二进制；失败抛 SeedreamError。"""
```

实现要点：

- 用 `httpx.AsyncClient`，`timeout=settings.seedream_timeout`，`follow_redirects=True`。
- 图片转 data URI：按魔数判断 MIME（PNG/JPEG/WebP），不信任扩展名。
- `response_format="url"`：拿到 URL 后立即 `GET` 下载；校验响应 `Content-Type` 与文件魔数，防止拿到错误页。
- `response_format="b64_json"`：`base64.b64decode` 后同样校验魔数。
- 记录 `request_id`（响应头或 body 有则记）、模型、耗时、`usage`；**不记录 base64、不记录 API Key**。
- 429 / 5xx / 超时按 `retryable=True` 抛出；400/401/403/404/审核拒绝按 `retryable=False` 抛出。

#### 5.5 `task_runners/image_gen.execute_image_gen` 的改动

```python
async def execute_image_gen(db, task):
    run = await _load_run(db, task)
    spec = MODEL_SPECS.get(run.model_key)
    if spec is None:
        raise PermanentTaskError(f"未知出图模型档位: {run.model_key}")

    if spec.get("provider") == "ark":
        await _execute_seedream(db, task, run, spec)
        return

    # 以下为现有 ComfyUI 逻辑，原样保留
    ...
```

`_execute_seedream` 流程：

1. `run.status = "running"`，`_mark_stage(..., "preparing_images")`。
2. 读取素材图：`reference_ids[0]` → `Inspiration` → `storage_root / file_path`；文件缺失按永久失败。
3. 下载/读取姿态图：`pose_gallery.ensure_pose_image(pose_id)`；源站 503 按可恢复错误重试一次（复用现有策略）。
4. 预处理两张图：
   - 转 RGB，长边压到 ≤ 2048（足够 2K 输出且控制请求体积）；
   - 素材图存 JPEG quality 90；姿态线稿保留 PNG；
   - 去掉 EXIF（隐私）；
   - 单图 ≤ 30 MB、总像素 ≤ 3600 万。
5. `_mark_stage(..., "calling_seedream")`，调用 `SeedreamClient.generate(prompt=run.prompt, images=[素材, 姿态], size=..., watermark=...)`。
6. 下载/解码结果，校验 PNG/JPEG 魔数，写入 `out_dir / "01_seed-0.png"`（沿用候选命名规则；云端无 seed，用 0 占位）。
7. `run.done = 1`、`run.status = "done"`、`task.stage = "done"`；失败则 `run.failed = 1`、`run.status = "failed"`、`run.error = 中文原因`。
8. 每次重试前检查 `_is_cancelled`；已发出的 HTTP 请求无法中断，但拿到响应后不再落盘。

> 云端不走显存守门、不上传 ComfyUI、不读 `run.workflow_name`；这些分支必须在进入现有逻辑前 return。

#### 5.6 状态与模型列表

`GET /api/generate/status` 目前只探测 ComfyUI。新增：

```json
{
  "ready": true,
  "comfyui": {...},
  "ark": {
    "ready": true,
    "reason": null,
    "model": "doubao-seedream-5-0-flash-260915",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3"
  },
  "models": {...},
  "models_meta": {
    "seedream_5_0_flash": {
      "provider": "ark",
      "cost_per_image_cny": 0.12,
      "requires_upload": true,
      "supports_seed": false,
      "supports_negative": false
    }
  }
}
```

- `ark.ready` 只看「`ark_enabled` 且 API Key 非空」，不发起真实请求（探测要花钱，不能每次开页都调）。
- `ready` 的语义改为「至少一条通道可用」；前端分别显示 ComfyUI / 云端状态，不要因为 ComfyUI 没开就把云端档位一起藏掉。
- `GET /api/generate/models` 增加 `provider`、`cost_per_image_cny`、`supports_seed`、`supports_negative`、`requires_upload`。

#### 5.7 改动文件清单（预估）

| 文件 | 改动 |
| --- | --- |
| `backend/app/config.py` | 新增 `ark_enabled` / `ark_api_key` / `ark_base_url` / `seedream_model` / `seedream_timeout` / `seedream_max_poses` / `seedream_daily_budget_cny` |
| `backend/app/services/comfyui/models.py` | 新增 `seedream_5_0_flash` 档位；补 `provider`、`cost_per_image_cny`、`supports_seed`、`supports_negative` 等字段 |
| `backend/app/services/seedream_client.py` | **新建**：Ark HTTP 客户端、base64/data URI、错误归一化、结果下载与魔数校验 |
| `backend/app/services/generation_service.py` | 按 provider 校验参数；拼接姿态提示词；云端跳过 `wf.load_template`；`workflow_name/workflow_hash` 写云端标识 |
| `backend/app/services/task_runners/image_gen.py` | 早期分支到 `_execute_seedream`；新增准备图片、调用、下载、状态流转 |
| `backend/app/services/comfyui/probe.py` | 遍历档位时跳过非 ComfyUI provider；状态里增加云端可用性 |
| `backend/app/routers/generate.py` | `/status`、`/models` 返回 `provider`、`cost_per_image_cny`、`requires_upload`、`supports_seed/negative` |
| `backend/app/schemas/generation.py` | `GenerationRunCreate` 增加可选 `size`、`watermark` 字段（云端专用） |
| `web/src/components/generate/GenerateParamForm.vue` | 云端档位隐藏 steps/cfg/sampler/seed；增加尺寸、水印、成本提示 |
| `web/src/composables/useImageGen.ts` | 多姿态循环提交、并发上限、部分失败提示、隐私确认状态 |
| `web/src/views/GenerateView.vue` | 姿态多选、云端批次提交与历史刷新 |
| `web/src/api/generate.ts` / `web/src/types/` | 补 `provider`、`size`、`watermark`、`cost_per_image_cny` 类型 |
| `backend/tests/test_seedream_client.py` | **新建**：mock Ark 的单元测试 |
| `backend/tests/test_generation_seedream.py` | **新建**：provider 分支、校验、状态流转 |
| `web/src/components/generate/__tests__/` | 云端档位参数显隐、成本提示、多姿态提交用例 |
| `backend/.env.example`（如新建） | 只放键名与空值，**不得**写真实 key |

#### 5.8 重用与边界

| 现有能力 | 是否可复用 | 说明 |
| --- | --- | --- |
| `GenerationRun` / 任务队列 | ✅ | `model_key` 字符串，无需迁移 |
| 候选目录 / 缩略图 / 下载 | ✅ | 文件仍写 `storage/generated/<run_id>/` |
| 确认入库 / `source_type=generated` | ✅ | 走现有 `confirm_import` |
| 生成历史 / 导出 / 统计 | ✅ | 按 `model_key` 分组，新档位自动出现 |
| 重试 / 单张重出 | ⚠️ | 云端无 seed，「重出」= 重新随机，不是复现 |
| 显存守门 | ❌ | 云端不占本机显存，跳过 |
| 负向词 | ❌ | 云端无 negative，只能拼进正向 prompt |
| 同参复现 | ❌ | 无 seed，UI 文案必须区分 |

---

### 6. 前端设计

#### 6.1 模型选择

在出图页模型下拉增加：

```text
豆包 Seedream 5.0 Flash（云端 · ¥0.12/张 · 约 15~30 秒）
```

- 带「云端」徽标，与本地档位视觉区分。
- 选中后隐藏 `steps / cfg / sampler / scheduler / shift / seed`（这些对云端无效），只保留：
  - 素材图（1 张）
  - 姿态（可多选）
  - 尺寸（1K / 1.5K / 2K / 自定义）
  - 水印开关（默认关）
  - 提示词（自动填包装模板 + 姿态描述，可编辑）
- `kind` 固定 `i2i`，不要再让用户切「文生图」。

#### 6.2 多姿态提交

一期采用**前端循环**，不改后端批量契约：

```text
用户选 1 张素材 + N 个姿态（N ≤ 10）
  → 逐个 POST /api/generate/runs
      { kind: "i2i", model_key: "seedream_5_0_flash",
        reference_ids: [素材id], pose_id: <每个姿态>,
        prompt: <自动拼接>, size: "1536x2048", count: 1 }
  → 每个 run 独立进任务队列；历史里按姿态区分
```

- 提交前显示：「本次 5 个姿态 × ¥0.12 ≈ ¥0.60」。
- 提交后 toast：「已提交 5 个云端出图批次」；部分失败时列出失败姿态，不重复提交已成功的。
- 并发：前端最多同时提交 2 个，其余排队；避免一次性打满 429。
- P1 可增加 `POST /api/generate/runs/batch`，后端一次建 N 个 run；一期不做，先验证质量与成本。

#### 6.3 隐私确认

首次切换到云端档位、或首次提交云端批次时弹窗：

```text
本次生成会把「素材图」和「姿态图」上传到火山引擎方舟（cn-beijing）用于推理。
素材涉及人物/人脸时，请确认你有权上传并接受云端处理。
云端接口可能触发内容审核；被拦截时会给出提示，不会自动绕过。

[ ] 我已知晓，本次允许上传
[取消] [继续生成]
```

- 不做「勾选一次永久同意」；至少每个浏览器会话确认一次（localStorage 记 session）。
- 素材图、姿态图的本地路径与 base64 **不写日志、不进前端 store**。

#### 6.4 历史与文案

- 历史列表模型列显示「Seedream 5.0 Flash（云端）」；`pose_id` 仍按现有逻辑显示姿态名。
- 云端档位的「同参数再生成」按钮文案改为「重新生成（云端无 seed，结果随机）」。
- 失败原因直接展示后端 `run.error`，例如：
  - 「云端审核未通过：请更换素材或姿态图」
  - 「方舟限流，已重试 3 次仍未成功」
  - 「ARK_API_KEY 未配置或已失效」

---

### 7. 配置、安全与隐私

#### 7.1 新增配置（`backend/app/config.py`，走 `.env`）

```ini
# 豆包 Seedream 云端图生图（默认关闭；不配置 key 时档位不可用）
ARK_ENABLED=false
ARK_API_KEY=
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
SEEDREAM_MODEL=doubao-seedream-5-0-flash-260915
SEEDREAM_TIMEOUT=120
SEEDREAM_MAX_ATTEMPTS=3
SEEDREAM_MAX_POSES=10
SEEDREAM_DAILY_BUDGET_CNY=5.0
```

> `SEEDREAM_MAX_ATTEMPTS`（2026-11-03 新增）：**生成请求**的重试次数（含第一次）；
> 429/5xx/短时网络错误才重试。生成成功后的**下载失败只重试下载**、不重新生成，
> 因此不会因为下载抖动重复计费。默认 3，不配置即使用默认值。

- `ARK_ENABLED=false` 时，`MODEL_SPECS` 里的云端档位仍可存在，但 `/api/generate/models` 不返回，前端看不到。
- `ARK_API_KEY` 只放 `backend/.env`（已 gitignore）或环境变量；**不写进代码、不写进文档、不提交**。
- `SEEDREAM_DAILY_BUDGET_CNY` 是软上限：当天累计估算费用超过时拒绝新提交，提示改天或调高配置。实现可用 `generation_runs` 当天记录数 × 单价，不新增表。

#### 7.2 隐私边界变更（必须让用户拍板）

项目原安全边界写的是「单机本地运行，素材不出内网」。启用云端通道后：

- 素材图 + 姿态图会出本机，传到火山引擎方舟（cn-beijing）；
- 云端是否留存、留存多久，取决于方舟服务条款，不由本项目控制；
- 人物照片/人脸属敏感数据，**不得默认开启**，不得后台自动批量上传；
- 建议只对「明确同意本次上传」的批次启用，不提供全局静默开关。

如果用户不接受这一点，本方案直接终止，继续用本地 ComfyUI 链路。

#### 7.3 内容审核

- 姿态库含「性感系」等成人向体系，云端审核可能拒绝部分图片或提示词。
- 这是云端服务的正常行为：**提示原因、建议换图或改用本地链路，不做提示词混淆、裁剪规避、换号重试**。
- 记录被拒的 `pose_id` / 素材 id（不含图片内容），便于统计哪些姿态不可用。

#### 7.4 日志与审计

- 记录：`run_id`、`model`、`size`、参考图张数、耗时、`request_id`、`usage`、估算费用、错误码。
- 不记录：API Key、base64、图片二进制、完整本地路径（路径可记相对 storage 的路径）。
- 每次云端调用在 `run.params_json` 留 `provider/model_id/watermark/size`，便于对账。

---

### 8. 成本与性能

#### 8.1 成本

| 项 | 数值 |
| --- | --- |
| 单张 | **约 ¥0.12**（1K/1.5K/2K 同价） |
| 参考图 | 免费 |
| 10 个姿态 | ≈ ¥1.20 |
| 100 个姿态 | ≈ ¥12.00 |
| 失败请求 | 是否计费以官方为准；重试前先确认，避免无谓重试 |

#### 8.2 耗时

- 官方未给出 5.0 flash 的明确延迟；第三方资料约 15～20 秒/张，**以本机实测为准**。
- 端到端还要加上：两张图预处理（< 1 秒）+ base64/上传（取决于图片大小）+ 结果下载。
- 10 个姿态串行约 3～5 分钟；2 并发约 1.5～2.5 分钟。一期建议串行/2 并发，先保证 429 可控。

#### 8.3 与本地链路对比

| 维度 | 本地 Qwen-Image 2.1 UC | Seedream 5.0 Flash |
| --- | --- | --- |
| 单张成本 | 电费 + 时间 | ¥0.12 |
| 速度 | 20～45 秒 | 15～30 秒（待实测） |
| 素材隐私 | 不出本机 | **上传云端** |
| 可复现 | seed 固定，可复现 | **无 seed，只能重 roll** |
| 负向词 | 支持（cfg > 1 时有效） | **不支持** |
| 参考图 | 1～10 张 | 2～10 张 |
| 姿势保真 | E3 路线约 3.7/5 | **待测** |
| 服装保持 | 历史实验 0/10 | **待测** |
| 内容审核 | 未审查档位无过滤 | 云端审核 |

> 不预设「云端一定更好」。P4 必须用同一批素材/姿态做对比，按项目实验报告规范出量化结论。

---

### 9. 测试策略与验收

#### 9.1 单元 / 集成测试（不花钱）

| 测试 | 内容 |
| --- | --- |
| `test_seedream_client.py` | data URI 格式、MIME 魔数、size 校验、429 重试、400 永久失败、响应解析、无 key 报错 |
| `test_generation_seedream.py` | provider 分支、count 必须 1、pose_id 必填、negative 合并、prompt 长度、run 状态流转 |
| `test_generate_router.py` | `/models` 返回 provider/cost、`ARK_ENABLED=false` 时不返回云端档位 |
| 前端 | 云端档位隐藏无关参数、成本提示、隐私弹窗、多姿态循环提交（mock API） |

- 用 `httpx.MockTransport` 或 `respx` 模拟 Ark，**CI 不调用真实接口**。
- 断言日志中不出现 API Key / base64。

#### 9.2 手工联调（花钱，P0 先做）

1. 在 `backend/.env` 配 `ARK_API_KEY`（不提交）。
2. 用 curl / 一次性脚本跑 1 张素材 + 1 个姿态，确认：
   - 模型 ID 正确、size 合法、返回图片可下载；
   - 输出不是线稿风格、姿势基本跟随、长相/服装基本保持；
   - 记录实际耗时、`data.size`、`usage`、是否被审核拦截。
3. 再跑 1 张素材 × 3 个姿态，确认多姿态循环与历史展示。
4. 产物放 `G:/fashion-inspo-storage/poc/seedream_<日期>/`，脚本用完即删，不提交。

#### 9.3 验收口径

| 维度 | 通过标准 |
| --- | --- |
| 技术链路 | 3/3 成功；候选图可读、可缩略图、可入库；历史/重试/清理正常 |
| 延迟 | 单张 ≤ 60 秒（含上传/下载），10 张串行 ≤ 8 分钟 |
| 姿势 | 人工 1～5 分，平均 ≥ 4.0（与本地 E3 3.7 对比） |
| 长相 | 平均 ≥ 4.0 |
| 服装 | 平均 ≥ 4.0（此前本地路线 0/10，是主要短板） |
| 线稿残留 | 0/10 出现明显线稿/关节球/灰底 |
| 成本 | 实际账单 ≈ 生成张数 × 0.12（误差以官方计费为准） |
| 隐私 | 未确认不上传；日志无 key/base64 |

#### 9.4 对比实验

- 样本：与 `docs/pose/` 既有实验同一批 10 张 4 星以上素材 × 3 个姿态，保持可比。
- 每组同素材同姿态各跑 1 张，盲评姿势/长相/服装/画面质量。
- 产出实验报告（`docs/pose/` 或 `docs/features/`），含输入素材、姿态图、云端输出对比图与量化表。

---

### 10. 分阶段实施

| 阶段 | 内容 | 预估 | 验收 |
| --- | --- | --- | --- |
| P0 | 手工 curl / 一次性脚本验证 API、模型、尺寸、提示词、审核 | 0.5 天 | 1 素材 × 3 姿态出图，记录耗时/成本/质量 |
| P1 | `seedream_client.py` + provider 分支 + 单姿态 run + mock 测试 | 1～1.5 天 | 后端单姿态端到端通过，CI 全绿 |
| P2 | 前端模型档位、参数隐藏、隐私弹窗、成本提示 | 1 天 | 页面可提交单姿态，候选区可入库 |
| P3 | 多姿态循环、并发限制、预算上限、历史文案 | 1 天 | 5～10 个姿态一次提交，失败可单独重试 |
| P4 | 10 素材 × 3 姿态对比实验与报告 | 0.5～1 天 | 量化结论：保留 / 仅备用 / 移除 |

> 每阶段独立可验收；P0 不通过（例如审核大面积拦截、质量不达标）就不进入 P1。

---

### 11. 风险与对策

| 风险 | 等级 | 对策 |
| --- | --- | --- |
| 隐私边界：素材/人脸上传云端 | **高** | 默认关闭；逐次确认；文档与 UI 明示；不接受则不做 |
| 云端内容审核拦截成人向姿态 | 中高 | 先小样本验证；被拒时提示换图/改本地；不绕过 |
| 线稿风格泄漏 | 中 | 提示词显式排除；必要时姿态图转剪影；A/B 验证 |
| 姿势保真不足 | 中 | 姿态图 + `pose_prompts` 文本双输入；对比本地 E3 |
| 无 seed，无法复现 | 中 | UI 明确「重新生成 = 随机」；复现需求走本地档位 |
| 无负向词 | 低 | 负向内容改写进正向 prompt；关键约束优先 |
| 成本累积 | 低中 | 单批 ≤ 10 姿态；日预算软上限；提交前显示费用 |
| 429 / 限流 | 低 | 串行/2 并发；指数退避；遵守 `Retry-After` |
| 云端 URL 24h 失效 | 低 | 拿到 URL 立即下载；不落库 URL |
| API/模型下线或改名 | 低 | 模型 ID 走配置；P0 记录版本；保留本地通道 |
| 图片过大导致 400/超时 | 低 | 预压到长边 ≤ 2048、JPEG q90、≤ 30MB |

---

### 12. 待确认问题（评审时必须拍板）

1. **是否接受素材图/姿态图上传到火山引擎方舟？** 这是本方案的前置条件；不接受就不做。
2. 使用哪个火山账号 / 区域 / API Key？模型是否已开通（`doubao-seedream-5-0-flash-260915`）？
3. 每日/每批预算上限是多少？`SEEDREAM_DAILY_BUDGET_CNY=5.0` 是否合适？
4. 水印是否关闭（`watermark=false`）？生成图对外分发时谁负责合规？
5. 成人向姿态被审核拦截时，是「提示后跳过」还是「整批中止」？
6. 多姿态一次最多 10 个是否够用？是否需要 P1 的批量接口？
7. 是否需要保留「同参复现」？如果需要，云端档位只能作为补充，不能替代本地。

---

### 13. 附录

#### 13.1 常用尺寸

| 场景 | 建议 `size` | 总像素 | 说明 |
| --- | --- | --- | --- |
| 全身 3:4 | `1152x1536` | 1,769,472 | 与 900×1200 姿态图同比例，质量足够 |
| 全身 3:4 高清 | `1536x2048` | 3,145,728 | 推荐默认；2K 档，价格相同 |
| 半身 1:1 | `1024x1024` | 1,048,576 | 上半身姿态 |
| 半身 1:1 高清 | `1536x1536` | 2,359,296 | 2K 档 |
| 横构图 4:3 | `1536x1152` | 1,769,472 | 双人/横向场景（本期不用） |

> 显式像素必须同时满足：总像素 921,600～4,624,220；宽高比 1/16～16。`512x512`（262,144）会 400。

#### 13.2 图片预处理约定

```text
素材图：EXIF 方向校正（按 Orientation 转正，不保留 EXIF）→ 转 RGB → 长边 ≤ 2048 → JPEG quality 90
姿态图：保留 PNG（线稿细节）→ 长边 ≤ 2048；透明背景先贴白底；必要时转黑白剪影做 A/B
两张图：单图 ≤ 30 MB；总像素 ≤ 3600 万；输出图下载上限 32 MB
```

#### 13.3 与本地链路的代码复用对照

| 现有代码 | 云端是否复用 | 备注 |
| --- | --- | --- |
| `GenerationRun` / `TaskQueue` | ✅ | 不改表 |
| `generation_service.create_run` | ✅（加分支） | 参数校验按 provider 分叉 |
| `task_runners/image_gen.py` | ✅（加分支） | 云端执行体独立 |
| `ComfyUIClient` | ❌ | 云端用 `SeedreamClient` |
| `pose_gallery.ensure_pose_image` | ✅ | 姿态图下载/缓存 |
| `pose_prompts` 表 | ✅ | 姿态提示词 |
| `generation_service.confirm_import` | ✅ | 入库逻辑不变 |
| `probe` / 显存守门 | ❌ | 云端不占显存 |
| `workflow.py` | ❌ | 云端无工作流 |

#### 13.4 参考链接

- 模型详情：<https://ark.volcengine.com/region:cn-beijing/model/detail?Id=doubao-seedream-5-0-flash>
- 图片生成 API：<https://console.volcengine.com/ark/region:cn-beijing/docs/image-generation-api>
- 提示词指南：<https://ark.volcengine.com/region:cn-beijing/docs/ark/seedream-4-0-5-0-prompt-guide>
- Base URL 与鉴权：<https://docs.volcengine.com/docs/ark/base-url-and-authentication>
- 官方 SDK / OpenAI 兼容：<https://docs.volcengine.com/docs/ark/compatible-with-openai-sdk>


---

### 14. 实施记录（2026-11-02）

#### 14.1 已完成

| 层 | 内容 |
| --- | --- |
| 配置 | `config.py` 新增 `ark_enabled` / `ark_api_key` / `ark_base_url` / `seedream_model` / `seedream_timeout` / `seedream_max_poses` / `seedream_daily_budget_cny`；`.env.example` 只放空 key |
| 档位 | `MODEL_SPECS` 新增 `seedream_5_0_flash`（provider=ark、i2i、cost 0.12、supports_seed/negative=false、requires_upload=true）；探测跳过非 ComfyUI 档位，`/status` 增加 `ark` 状态 |
| 客户端 | 新增 `seedream_client.py`：data URI、尺寸校验、URL/b64 解析、429/5xx/超时重试、400/401/403/404/审核拒绝永久失败、结果下载与魔数校验 |
| 提交 | `create_run` provider=ark 分支：仅 i2i、恰好 1 张素材、pose_id 必填、count=1；固定包装模板 + pose_prompts；日预算软上限；写入 `workflow_name=ark_seedream`、`workflow_hash=ark:<model>`、params provider/size/watermark/model_id |
| 执行 | `image_gen` 早期分支到 `_execute_seedream`：素材图/姿态图预处理（长边≤2048、去 EXIF）、调用、落盘 `01_seed-0.png`、状态流转与可重试/永久错误 |
| 前端 | 云端档位带「云端」标，隐藏 steps/cfg/seed/负向词；显示 1K/1.5K/2K 尺寸、水印、单张费用；首次会话隐私确认弹窗；单姿态提交 |
| 测试 | `test_seedream_client.py`（MIME/data URI/尺寸/429 重试/错误映射/无 key）、`test_generation_seedream.py`（provider 分支校验/提示词/模型可见性/执行器落盘）；前端云端档位显隐用例 |

#### 14.2 P3 多姿态（2026-11-02 补齐）

- `PosePickerModal` 支持多选（≤10 个，按选择顺序），云端档位用「选择多个姿势」；
- 参数面板为每个姿态生成独立 `CreateRunPayload`（固定 1 张素材 + 各自 pose_id），一次提交 N 个批次；
- `useImageGen.submitBatch`：最多 2 并发、逐批收集成功/失败，展示最后一个成功批次，失败项不重复提交；
- 费用提示按「已选姿态数 × ¥0.12」计算；隐私确认一次覆盖整批；
- 测试：前端新增多姿态提交用例、submitBatch 成功/部分失败用例、隐私确认未确认不发出批次用例；全量前端 886 项通过。
- 对账：执行成功后把 `ark_request_id` / `ark_elapsed_s` / `ark_usage` 写回 `params_json`（不记录 key/base64/图片）；README 已补充云端通道说明。

#### 14.3 P0 真实联调（2026-11-02 完成）

用户配置 `ARK_ENABLED=true` + `ARK_API_KEY` 后，用 `scripts/oneoff/seedream_smoke.py` 直接走「素材图 + 姿态线稿 → Ark」链路（与执行器同一套预处理/客户端）：

| # | 姿态 | 结果 | 耗时 | 质量（目视） |
| --- | --- | --- | ---: | --- |
| 01 | full_single_P026_high（站立单臂上举） | 成功 | 16.38s | 姿势 5/5、长相/服装 5/5、无线稿残留 |
| 02 | full_sexy_P037_diag（跪姿双手抱头） | 成功 | 14.81s | 姿势 5/5、长相/服装 5/5、无线稿残留 |
| 03 | full_single_P002_front（坐姿单手支撑） | 成功 | 18.50s | 姿势 4.5/5（手臂角度略偏）、长相/服装 5/5、无线稿残留 |
| 04 | upper80v2_P025_front（半身遮脸） | **审核拒绝** | — | 云端审核未通过（预期行为，不绕过） |

- 产物：`G:/fashion-inspo-storage/poc/seedream_p0_20261102/`；对比图 `comparison_sheet.jpg`（输出 / 姿态线稿 / 素材）；
- usage：每次 `input_images=2`、`generated_images=1`、`output_tokens=12288`；成功 3 张估算 **¥0.36**（审核拒绝是否计费以方舟为准）；
- 结论：**P0 通过**——链路、尺寸、提示词、身份/服装保持、姿势跟随、线稿排除均达预期；延迟 15~19 秒；
- 注意：该轮由独立脚本验证，后端进程当时仍是旧配置（`/status ark.ready=false`），网页端需重启 backend 后才可提交。

#### 14.4 未完成 / 待用户决定

- **P4 对比实验**（方案 §9.4）：10 素材 × 3 姿态与本地链路对比、盲评与报告，费用约 ¥3.6+，待用户确认预算。
- **验收口径补全**：本机真实 API 路径（web → worker）尚未跑，需要在 backend/worker 重启后提交 1 个批次确认状态流转。
- **水印/预算默认值**：当前 `watermark=false`、日预算 ¥5，按方案默认值实现，可在 `.env` 调整；实际账单以方舟为准。

---

### 15. 评审修复记录（2026-11-03，提交 `9c0fecd` 之后）

> 依据 豆包 Seedream 5.0 Flash 接入提交代码评测（3645006）（本文第 2 节）
> 的 §7 建议顺序逐项修复。本文只记录**当前实际实现**，旧行为不再保留。

#### 15.1 修复清单

| 编号 | 原问题 | 修复后行为 |
| --- | --- | --- |
| **P0-1** | 用户自定义 prompt 被静默丢弃 | `_ark_prompt_text` 增加 `user_prompt`：固定包装 → **用户补充要求** → 姿势细节 → 负向改写；长度不够先截姿势细节，不截用户要求。创建批次时传 `payload["prompt"]` |
| **P0-2** | 预调用失败让批次永久停在 `running` | `_execute_seedream` 全流程 `try/except`：素材缺失 / 姿势 503 / 坏图 / 预处理失败 / 落盘失败 / 未知异常都会写 `run.status=failed` + `run.error` 再抛出；取消写 `cancelled` |
| **P1-1** | 下载失败会重跑整次生成（重复计费） | 生成与取图分离：生成请求最多 `SEEDREAM_MAX_ATTEMPTS` 次；成功后**只重试下载** 3 次；下载最终失败为永久错误（`code=download_failed`，错误信息保留云端 URL 供人工恢复），不再自动重发生成 |
| **P1-2** | 云端档位仍显示一键出图 | 云端档位不渲染 `QuickPoseBatchPanel`（既有修复保留，测试锁定） |
| **P1-3** | 云端返回假 seed | `create_run` 不写 `seed_base`/`seeds`，响应 `seed=null`；前端候选图显示「无 seed（每次随机）」，重跑按钮文案改「重新生成」，消息不再打印 seed |
| **P1-4** | retry / 单张重出 / 带入表单丢云端参数 | `retry_run`、`regenerate_candidate` 的 payload 原样携带 `size` / `watermark`；前端 `applyRunPayload` 对云端档位恢复 `cloudSize` / `cloudWatermark` / 姿态 |
| **P1-5** | 双人姿态与单张素材图不匹配 | `_create_ark_run` 遇到 `system` 含「双人」的姿势返回 400（提示改用本地或换单人姿态）；前端选姿态时过滤双人并提示，提交前再兜底拦截 |
| **P1-6** | 取消无法在云端调用期间生效 | `SeedreamClient.generate` 接受 `should_cancel` 回调，请求期间每 0.5s 轮询任务状态；取消抛 `code=cancelled`，执行器将批次置 `cancelled`（已发出的请求仍可能计费，UI 已提示） |
| **P2-1** | 日预算按 UTC 零点重置 | `_ark_budget_day_start()` 按**本地时区零点**换算成 naive UTC |
| **P2-2** | 预算检查非原子/排队不查 | 创建时用 `_ARK_BUDGET_LOCK` 把「检查 + 建行」串行化；执行器调用云端前用 `ark_budget_exceeded` 做二次检查，超预算直接 failed、不发请求 |
| **P2-3** | 档位尺寸与实际输出不一致 | 执行成功把响应 `size` 回写 `params.size`；若为 `宽x高` 同时回写 `width`/`height` |
| **P2-4** | 执行用全局 `seedream_model` | 执行器用 `params.model_id`（提交时写入），提交后改配置不影响在跑/重试批次 |
| **P2-5** | 未写 request_id/usage/耗时 | 成功回写 `ark_request_id` / `ark_elapsed_s` / `ark_usage` / `ark_model_id`（不写 key/base64/图片） |
| **P2-6** | EXIF 未转正、透明图变黑底 | 预处理先 `ImageOps.exif_transpose`；带 alpha 的图先合成到白色背景再转 RGB |
| **P2-7** | 审核错误只匹配英文 | 错误映射同时匹配中文关键词（审核/敏感/违规/内容安全/不合规） |
| **P2-8** | 未遵守 Retry-After / 退避写错 | 解析 429 响应头 `Retry-After`（秒、上限 60），退避取 `max(指数退避, Retry-After)`；文档与实现一致（1/2 秒 + 抖动，上限由重试次数决定） |
| **P2-9** | 下载无大小上限 | 下载前检查 `Content-Length`、下载后检查实际长度，超过 32 MB 直接拒绝 |
| **P2-10** | prompt 截断 600 字 | 上限收紧到 **300 字**（官方中文建议），用户要求优先于姿势细节 |
| **P2-11** | 成功横幅假设 ComfyUI 有值 | `GenerateView` 增加「ComfyUI 未启动但云端就绪」分支，不再显示 `undefined / undefined GiB`；就绪横幅提示云端已就绪 |
| **P2-12** | `max_refs=10` 与云端 1 张素材不一致 | 云端档位 `max_refs=1`（素材图；姿态图由 pose_id 单独处理），`/models` 与 `/status` 都下发 `max_refs`，前端按它校验 |
| **P2-13** | 云端费用为 0 时不回退 0.12 | 前端费用计算改为 `costPerImage || 0.12`（0/缺省都回退默认单价） |
| **P3** | 死常量/死配置/未使用字段 | 删除 `_MIME_BY_MAGIC`；`SEEDREAM_MAX_POSES` 经 `/models` 的 `max_poses` 真正下发并被前端使用；`max_poses`/`max_refs`/`supports_seed`/`supports_negative`/`requires_upload` 均被前端能力判断消费；删除 `_execute_seedream` 未使用的 `spec` 参数；`_get_client` 增加并发锁 |

#### 15.2 新增/调整的测试

| 文件 | 覆盖 |
| --- | --- |
| `backend/tests/test_seedream_client.py` | 下载失败只重试下载（生成只调 1 次）、429 Retry-After、中文审核映射、下载大小上限、取消回调、prompt 300 字截断 |
| `backend/tests/test_generation_seedream.py` | 用户 prompt 进入 `run.prompt`、seed 为 None、双人姿态 400、retry 保留 size/watermark、准备阶段三类失败均写 failed、真实尺寸回写、model_id 来自 params、执行前预算二次检查、取消置 cancelled |
| `backend/tests/test_unit_seedream_image.py` | EXIF 方向转正、透明图贴白底、长边缩放 |
| `web/src/components/generate/__tests__/GenerateParamForm.test.ts` | 带入云端历史参数恢复 size/水印/姿态、双人姿态过滤、`max_poses` 配置生效 |
| `web/src/components/generate/__tests__/GenerateCandidatesPanel.test.ts` | 云端无 seed 文案 + 「重新生成」按钮 |
| `web/src/components/generate/__tests__/GenerateHistoryTable.test.ts` | 云端批次「重新生成」文案、候选图「无 seed（每次随机）」 |
| `web/src/views/__tests__/GenerateView.test.ts` | ComfyUI 未启动 + 云端就绪时横幅不出现 undefined |

#### 15.3 边界说明

- 云端取消只能做到「尽快终止本地等待」；**已经发到方舟的请求仍可能计费**，UI 不再承诺零费用取消。
- 下载最终失败不会自动重试生成（防止重复计费），错误信息保留云端 URL；URL 24h 失效，超时后只能重新生成。
- 双人姿态一期直接拒绝；若后续要支持，需要 2 张素材图 + 双人物提示词模板，不能复用当前单人物模板。
- 预算锁只在单 API 进程内原子；多进程部署需换成数据库/Redis 级原子检查。

---

### 16. 多姿态结果合并展示（2026-11-03）

> 问题：一次选多个姿态提交后，候选区只显示最后一个成功批次、生成历史里每个姿态各占一行，
> 用户看不到本次提交的全貌。要求**展示层合并**，底层批次/任务保持独立。

#### 16.1 数据模型：`group_id`（不合并任务）

- `generation_runs` 新增 `group_id VARCHAR(32) NULL`（迁移 `u1v2w3x4y5z6`）：
  - 前端在一次「多姿态提交」时生成 32 位小写十六进制 id，**每个独立批次带同一个 id**；
  - 单批次提交（本地出图/单姿态云端）不传，保持 `NULL`，行为与旧版完全一致；
  - 每个姿态仍是独立 `GenerationRun` + 独立 `TaskQueue`，排队/执行/计费/重试互不影响。
- API 入参：`POST /api/generate/runs` 新增可选 `group_id`（`^[a-f0-9]{32}$`，非法 422）。
- 为什么不建分组表：分组只是展示属性，`group_id` 足够；不新增表、不改任务队列。

#### 16.2 历史列表：按组合并成一行

`GET /api/generate/runs` 的 `items` 语义由「每个 run 一行」变为「每组一行（单批次组 = 一行）」：

| 字段 | 单批次 | 多姿态合并行 |
| --- | --- | --- |
| `id` | 自身 id | 组内最新批次 id（代表批次） |
| `run_ids` | `[id]` | 组内全部批次 id（按 id 升序） |
| `group_id` | `null` | 32 位分组 id |
| `total/done/failed` | 自身 | 组内求和 |
| `status` | 自身 | 聚合：有 running/queued 显示在跑；全失败 failed；全取消 cancelled；否则 done |
| `candidates` | 最多 4 张 | 跨批拼起来，最多 `GROUP_CANDIDATES_LIMIT=8` 张，每张带 `run_id` |
| `candidate_total` | 真实候选数 | 组内候选总数 |
| `imported_count` | 自身 | 组内求和 |
| `favorite/title/note` | 自身 | 代表批次（组内任意批次收藏 → 行显示已收藏） |

- `total` 改为「组数」：分页也按组；组内只要有批次命中筛选，整组完整返回（不会出现
  「10 个姿态只显示 2 张」的截断假象）。
- 展开行：前端用 `run_ids` 逐批 `GET /runs/{id}`，把候选图合并展示（候选带 `run_id`）；
  入库时按 `run_id` 分组逐批调用 `POST /runs/{id}/confirm`。
- 删除：合并行删除整组（逐批删除）；收藏/标注/带入表单作用于代表批次；重跑按钮在合并行隐藏
  （重跑语义是单批次，避免误操作只重跑其中一个姿态）。
- 导出/统计仍按底层批次（不改 CSV 口径与统计卡）。

#### 16.3 候选区：合并展示、按批入库

- `useImageGen.submitBatch` 记录本次全部成功批次 id（`groupRunIds`），轮询时**逐批拉详情**：
  - `run` 合成一个「组批次」（状态/总数/失败数聚合），`stageText` 显示
    「多姿态生成中：已完成 X/Y 张（N 个姿态仍在生成）」/「已完成 X 张（N 个姿态合并显示）」；
  - `candidates` 跨批合并，每张带 `run_id` 与选择键 `run_id:filename`
    （同一次提交里文件名都是 `01_seed-0.png`，必须用批次区分）；
  - 确认入库时按 `run_id` 分组逐批调用接口；丢弃/取消对整组逐批执行；
  - 单张「重新生成」用候选自带的 `run_id`，不会错误地操作最后一个批次；
  - 单批次提交路径完全不变（选择键 = 文件名，保持既有交互与测试）。
- 候选区顶部显示「本次合并 N 个姿态（M 张，底层批次独立执行）」。

#### 16.4 兼容与边界

- 历史里旧的 `group_id=NULL` 批次不受影响；迁移只加列+索引。
- 分组信息只在**同一次提交**内有效：页面刷新后历史仍按 `group_id` 合并（后端持久化）。
- 合并行代表批次的收藏/标注只写一条；如需「整组一起收藏/命名」后续可扩展为批量 PATCH。
- 导出仍逐批（可据此对账）；若后续要导出合并视图，再在导出层按 `group_id` 聚合。
- 测试：`backend/tests/test_generation_grouping.py`（合并/状态/筛选/入参/候选 run_id）、
  前端 `useImageGen` 合并提交、候选区选择键、历史合并行/入库分组、入库弹窗。

---

## 2. 提交代码评测（提交 3645006，含修复记录）

> 原文件：`features/豆包Seedream5.0Flash提交代码评测-2026-11-03.md`｜原题：豆包 Seedream 5.0 Flash 接入提交代码评测（3645006）

> **评审对象**：`3645006132c954698df7af3e6e7ccdef4134623a`（短号 `3645006`）
> `feat: 接入豆包 Seedream 5.0 Flash 云端图生图（P1/P2，默认关闭）`
> **评审时间**：2026-11-03。**只读评审，未改代码**。
> **评审范围**：该提交改动的 16 个文件、1311 行新增 / 32 行删除。按用户要求，**不评估安全面**（密钥、隐私、日志泄露、审核绕过、访问控制等）；隐私弹窗只作为交互行为提及，不作为安全结论。
> **快照说明**：本文按该提交的代码快照评审。后续提交（如 `949f670` 隐藏云端一键出图、`a288197` 多姿态）可能已修掉部分问题，本文不回溯评价后续改动。
> **修复记录（2026-11-03）**：已按 §7 建议顺序修复全部 P0/P1/P2/P3 问题，实现细节见 接入方案 §15（本文第 1 节），逐项对照见文末 §11。

---

### 0. 结论与评级

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

### 1. 改动地图

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

### 2. 阻断级缺陷（P0）

#### P0-1 用户自定义 prompt 被静默丢弃

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

#### P0-2 预调用失败让 `GenerationRun` 永久卡在 `running`

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

### 3. 高优先级问题（P1）

#### P1-1 下载失败会重跑整次生成（重复计费）

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

#### P1-2 云端档位仍显示「一键出图」轮播面板

**位置**：`web/src/components/generate/GenerateParamForm.vue:863`（`<QuickPoseBatchPanel ... />` 没有 `v-if="!isArk"`）

**影响**：

- 云端模式下仍可点“一键出图”，而该面板的逻辑是**本地线稿参考批处理**（`onQuickRun` 会套用本地档位、count、steps，并可能把模型切回本地未审查档位）；
- 若用户不点，它只是噪音；若误点，可能切换档位、提交 count>1 的云端批次（后端 `count` 必须 1），得到 400 或空转；
- 后续提交 `949f670` 已修（“云端档位隐藏「一键出图」轮播面板”），说明这是真实踩到的坑。

**修复建议**：`v-if="!isArk"`；补一条断言（云端档位下 `wrapper.text()` 不含“一键出图”）。

#### P1-3 云端 fake seed 与「可复现」语义冲突

**位置**：`generation_service.py:313`（`seed_base = random.randint(...)`）、`:323-324`（写进 params）、`:361`（返回给前端）；`retry_run` / `regenerate_candidate` 仍按 seed 传递。

**影响**：

- Seedream 没有 seed 参数，但接口返回了一个随机 seed，历史/结果区可能显示“seed=123”；
- 用户以为同 seed 能复现，实际每次都是新图；
- `retry_run` / `regenerate_candidate` 的 API 语义仍是“同参复现”，对云端不成立。

**修复建议**：

- 云端 `params` 不写 `seed_base/seeds`，或写 `seed_supported=False`，返回 `seed=None`；
- 历史/结果区对云端显示“无 seed（每次随机）”，`retry_run`/`regenerate_candidate` 文案改为“重新生成”；
- 如果产品上需要“可复现”，云端档位不能承担，应明确引导回本地档位。

#### P1-4 retry / 单张重出 / 带入表单丢失云端参数

**位置**：

- `generation_service.retry_run`（`:1322-1335`）的 payload 只有 `width/height/steps/cfg/count/...`，**没有 `size` / `watermark`**；
- `regenerate_candidate`（`:1367-1380`）同样没有；
- 前端 `applyRunPayload`（`GenerateParamForm.vue:690-734`）只恢复本地 `sizePresets`，不恢复 `cloudSize` / `cloudWatermark`。

**影响**：

- 用户用 `1K 方形` 或 `水印开` 跑了一次云端，点“再生成”会退回默认 `1536x2048` + 水印关；
- 从历史“带入表单”也会丢这两个云端参数；
- 用户会认为“同一批参数重跑”，实际参数已经变了。

**修复建议**：`retry_run` / `regenerate_candidate` 把 `params.get("size")` / `params.get("watermark")` 原样放进 payload；前端 `applyRunPayload` 识别 `payload.model_key` 为云端时恢复 `cloudSize`/`cloudWatermark`；补测试。

#### P1-5 双人姿态与「1 张素材图」不匹配

**位置**：`generation_service._ark_framing`（`:235-240`）对 `system` 含“双人”返回“双人合照”；`_create_ark_run` 只接受 1 张素材图（`:269`）；包装模板写的是“图1中人物”（单数）。

**影响**：

- 姿势库含 275 条双人姿态；选到双人姿态时，只有一张素材图，模型可能把两个人合成/截断，或忽略其中一人；
- 前端不拦截，后端也不拦截，错误在出图后才暴露。

**修复建议**：一期在 `_create_ark_run` 拒绝 `system` 含“双人”的姿态（400，提示改用本地或换单人姿态），或在 UI 过滤；如果确实要支持双人，需要 2 张素材图和对应 prompt 模板，不能复用当前单人物模板。

#### P1-6 取消无法在云端调用期间生效

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

### 4. 中优先级问题（P2）

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

### 5. 代码质量与死代码（P3）

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

### 6. 做得好的地方（应保留）

1. **provider 分支位置正确**：`execute_image_gen` 在加载 ComfyUI 模板/显存守门之前就分流到 `_execute_seedream`，没有把云端硬塞进 ComfyUI 执行体。
2. **复用彻底**：`GenerationRun`、候选目录、缩略图、确认入库、历史/导出/清理全部复用，没有新增表/新页面。
3. **客户端职责窄**：`SeedreamClient` 只做 HTTP、data URI、MIME/魔数校验、错误归一化、下载；不碰 DB/文件系统，容易单测。
4. **错误归一化**：把 401/403/404/429/5xx/审核映射成中文，并区分 `retryable`，比直接把 HTTP 文本抛给前端好。
5. **图片处理不阻塞事件循环**：`_prepare_ark_image`、文件读写都走 `asyncio.to_thread`。
6. **隐私确认**：默认关闭 + 会话级确认 + 弹窗文案，交互上做到了“不静默上传”（安全面不在本文评价）。
7. **测试方法正确**：用 `httpx.MockTransport` 和 FakeClient，不花钱、可重复；覆盖了 429 重试、400 永久失败、401、data URI、尺寸校验、创建/执行主链路。

---

### 7. 建议修复顺序

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

### 8. 测试缺口清单

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

### 9. 附：评审方法与限制

- **静态审查**：逐文件读 diff，并在提交快照上读完整函数（`git show <sha>:<path>`），核对调用方/被调方、状态机、异常路径、前后端契约。
- **未在提交快照上重跑测试**：该提交已在历史中，当前工作区含后续改动；测试结果以现有 CI/仓库为准。本文结论来自代码静态分析与现有测试覆盖对照。
- **未评估安全面**：按用户要求，API Key、隐私上传、内容审核、日志脱敏、访问控制等不在本文结论内。
- **后续提交**：`949f670`（云端隐藏一键出图）、`a288197`（多姿态）等可能修复了本文部分问题；如需按最新代码复核，请以最新提交为准。

---

### 10. 最终建议

**可以继续保留这条链路，但启用云端档位前必须完成 P0-1、P0-2、P1-1**；P1-2～P1-6 建议在同一轮修复，否则用户会遇到“描述不生效 / 批次卡住 / 重跑参数变了 / 取消没反应”这类难以解释的行为。修完后再按接入方案里的 10 素材 × 3 姿态做一次人工对比，确认质量与成本。

---

### 11. 修复记录（2026-11-03）

按 §7 建议顺序逐项修复，**全部 P0/P1/P2/P3 已落地**；实现说明见 接入方案 §15（本文第 1 节）。

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
