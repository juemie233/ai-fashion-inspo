# 接入豆包 Seedream 5.0 Flash 图生图（素材图 + 姿态图 → 目标姿态）

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

## 0. 决策摘要（先看这张表）

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

## 1. 目标与非目标

### 1.1 目标

1. 在现有出图页新增「豆包 Seedream 5.0 Flash（云端）」档位：选 1 张素材图 + 1 个姿态 → 生成 1 张目标姿态图。
2. 一次可选择多个姿态，批量提交；每个姿态独立成批，历史里能按姿态对比。
3. 复用现有候选区、确认入库、生成历史、缩略图、清理、统计能力。
4. 成本可预期：提交前显示「N 个姿态 × ¥0.12」，提交后记录实际张数与耗时。
5. 隐私可控：默认关闭；每次云端生成前明确告知「素材图 + 姿态图将上传到火山引擎方舟」，不做静默上传。

### 1.2 非目标（一期不做）

| 不做 | 理由 |
| --- | --- |
| 用云端替换本地 ComfyUI | 本地可复现、零单张成本、素材不出机；两条通道定位不同 |
| 组图 / 流式输出 | Seedream 5.0 pro / flash **不支持** `sequential_image_generation` 与流式 |
| 视频生成 | 不在本需求范围 |
| 自动批量无人值守 | 与项目「手动确认、宁缺毋滥」的口径冲突，也放大云端费用 |
| 绕过云端内容审核 | 姿态图库含成人向体系，被拦截时只提示原因并建议改用本地链路，不做规避 |
| 保存云端返回 URL | 官方 URL 24 小时失效，必须立即下载到本地候选目录 |

---

## 2. 模型能力与硬限制

### 2.1 能力（与换姿势相关的部分）

| 能力 | Seedream 5.0 Flash |
| --- | --- |
| 文生图 | 支持 |
| 单图生图 | 支持（1 张参考图 + prompt） |
| 多图生图 | **支持 2～10 张参考图 + prompt → 单张图** |
| 交互编辑 | 支持坐标 / 框选 / 箭头（本期不用） |
| 图层拆分 | 支持（本期不用） |
| 透明背景 | 支持（仅单张带 alpha 图生图，本期不用） |
| 组图 / 流式 / 联网搜索 | **不支持**（5.0 pro / flash） |

### 2.2 输入输出硬限制

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

### 2.3 限流

- 官方按**账号 / 推理接入点**维度限制 RPM、TPM 与并发；超限返回 **429**，响应头可能带 `Retry-After`。
- 公开资料没有 5.0 flash 的确切 RPM/并发数字；BytePlus 同源资料提到 Seedream 系列理论 **500 IPM**，仅供参考。
- 实际配额以控制台「开通管理 / 限流配额」为准；一期按**串行 + 最多 2 并发**设计，遇 429 指数退避。

---

## 3. 两图输入的语义约定

### 3.1 顺序即角色

官方多图指南用「图1、图2」按传入顺序引用。本项目固定：

| 顺序 | 内容 | 作用 | prompt 指代 |
| --- | --- | --- | --- |
| `image[0]` | 素材图（人物照片） | 提供长相、发型、肤色、服装 | **图1** |
| `image[1]` | 姿态图（线稿 / 姿势参考） | 提供身体朝向、重心、四肢位置、镜头视角 | **图2** |

> 不要交换顺序。顺序反了，模型会把线稿人物当主体，输出「线稿风格的人」。

### 3.2 提示词模板

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

### 3.3 线稿泄漏的兜底方案

如果实测发现输出仍带线稿/人体模型风格，按顺序尝试：

| 方案 | 做法 | 代价 |
| --- | --- | --- |
| A. 强化提示词 | 模板里再加「最终画面必须是真实照片，不能出现任何线条、关节球、灰色背景」 | 零成本，先试 |
| B. 姿态图预处理 | 把线稿转成纯黑剪影（白底黑形），去掉关节球与线条细节 | 需要一次图像处理，可能降低姿势精度 |
| C. 姿态图 + 姿态描述 | 保留线稿，同时把 `pose_prompts` 的完整描述放进 prompt | 已经是默认方案，先看 A 是否足够 |
| D. 不用姿态图 | 只用 `pose_prompts` 文本描述姿势 | 姿势保真可能下降，作为对照 |

建议 P0 手工验证时同时跑 A/B 两组，各 3 个姿态，肉眼对比线稿残留与姿势保真。

---

## 4. API 接入

### 4.1 请求示例（本地图片 → base64）

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

### 4.2 响应结构（字段以实际返回为准）

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

### 4.3 错误处理

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

## 5. 后端接入设计

### 5.1 总体思路：复用 `generation_runs`，加一个 provider 分支

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

### 5.2 新增模型档位

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

### 5.3 `generation_service.create_run` 的改动

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

### 5.4 新增 `backend/app/services/seedream_client.py`

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

### 5.5 `task_runners/image_gen.execute_image_gen` 的改动

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

### 5.6 状态与模型列表

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

### 5.7 改动文件清单（预估）

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

### 5.8 重用与边界

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

## 6. 前端设计

### 6.1 模型选择

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

### 6.2 多姿态提交

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

### 6.3 隐私确认

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

### 6.4 历史与文案

- 历史列表模型列显示「Seedream 5.0 Flash（云端）」；`pose_id` 仍按现有逻辑显示姿态名。
- 云端档位的「同参数再生成」按钮文案改为「重新生成（云端无 seed，结果随机）」。
- 失败原因直接展示后端 `run.error`，例如：
  - 「云端审核未通过：请更换素材或姿态图」
  - 「方舟限流，已重试 3 次仍未成功」
  - 「ARK_API_KEY 未配置或已失效」

---

## 7. 配置、安全与隐私

### 7.1 新增配置（`backend/app/config.py`，走 `.env`）

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

### 7.2 隐私边界变更（必须让用户拍板）

项目原安全边界写的是「单机本地运行，素材不出内网」。启用云端通道后：

- 素材图 + 姿态图会出本机，传到火山引擎方舟（cn-beijing）；
- 云端是否留存、留存多久，取决于方舟服务条款，不由本项目控制；
- 人物照片/人脸属敏感数据，**不得默认开启**，不得后台自动批量上传；
- 建议只对「明确同意本次上传」的批次启用，不提供全局静默开关。

如果用户不接受这一点，本方案直接终止，继续用本地 ComfyUI 链路。

### 7.3 内容审核

- 姿态库含「性感系」等成人向体系，云端审核可能拒绝部分图片或提示词。
- 这是云端服务的正常行为：**提示原因、建议换图或改用本地链路，不做提示词混淆、裁剪规避、换号重试**。
- 记录被拒的 `pose_id` / 素材 id（不含图片内容），便于统计哪些姿态不可用。

### 7.4 日志与审计

- 记录：`run_id`、`model`、`size`、参考图张数、耗时、`request_id`、`usage`、估算费用、错误码。
- 不记录：API Key、base64、图片二进制、完整本地路径（路径可记相对 storage 的路径）。
- 每次云端调用在 `run.params_json` 留 `provider/model_id/watermark/size`，便于对账。

---

## 8. 成本与性能

### 8.1 成本

| 项 | 数值 |
| --- | --- |
| 单张 | **约 ¥0.12**（1K/1.5K/2K 同价） |
| 参考图 | 免费 |
| 10 个姿态 | ≈ ¥1.20 |
| 100 个姿态 | ≈ ¥12.00 |
| 失败请求 | 是否计费以官方为准；重试前先确认，避免无谓重试 |

### 8.2 耗时

- 官方未给出 5.0 flash 的明确延迟；第三方资料约 15～20 秒/张，**以本机实测为准**。
- 端到端还要加上：两张图预处理（< 1 秒）+ base64/上传（取决于图片大小）+ 结果下载。
- 10 个姿态串行约 3～5 分钟；2 并发约 1.5～2.5 分钟。一期建议串行/2 并发，先保证 429 可控。

### 8.3 与本地链路对比

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

## 9. 测试策略与验收

### 9.1 单元 / 集成测试（不花钱）

| 测试 | 内容 |
| --- | --- |
| `test_seedream_client.py` | data URI 格式、MIME 魔数、size 校验、429 重试、400 永久失败、响应解析、无 key 报错 |
| `test_generation_seedream.py` | provider 分支、count 必须 1、pose_id 必填、negative 合并、prompt 长度、run 状态流转 |
| `test_generate_router.py` | `/models` 返回 provider/cost、`ARK_ENABLED=false` 时不返回云端档位 |
| 前端 | 云端档位隐藏无关参数、成本提示、隐私弹窗、多姿态循环提交（mock API） |

- 用 `httpx.MockTransport` 或 `respx` 模拟 Ark，**CI 不调用真实接口**。
- 断言日志中不出现 API Key / base64。

### 9.2 手工联调（花钱，P0 先做）

1. 在 `backend/.env` 配 `ARK_API_KEY`（不提交）。
2. 用 curl / 一次性脚本跑 1 张素材 + 1 个姿态，确认：
   - 模型 ID 正确、size 合法、返回图片可下载；
   - 输出不是线稿风格、姿势基本跟随、长相/服装基本保持；
   - 记录实际耗时、`data.size`、`usage`、是否被审核拦截。
3. 再跑 1 张素材 × 3 个姿态，确认多姿态循环与历史展示。
4. 产物放 `G:/fashion-inspo-storage/poc/seedream_<日期>/`，脚本用完即删，不提交。

### 9.3 验收口径

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

### 9.4 对比实验

- 样本：与 `docs/pose/` 既有实验同一批 10 张 4 星以上素材 × 3 个姿态，保持可比。
- 每组同素材同姿态各跑 1 张，盲评姿势/长相/服装/画面质量。
- 产出实验报告（`docs/pose/` 或 `docs/features/`），含输入素材、姿态图、云端输出对比图与量化表。

---

## 10. 分阶段实施

| 阶段 | 内容 | 预估 | 验收 |
| --- | --- | --- | --- |
| P0 | 手工 curl / 一次性脚本验证 API、模型、尺寸、提示词、审核 | 0.5 天 | 1 素材 × 3 姿态出图，记录耗时/成本/质量 |
| P1 | `seedream_client.py` + provider 分支 + 单姿态 run + mock 测试 | 1～1.5 天 | 后端单姿态端到端通过，CI 全绿 |
| P2 | 前端模型档位、参数隐藏、隐私弹窗、成本提示 | 1 天 | 页面可提交单姿态，候选区可入库 |
| P3 | 多姿态循环、并发限制、预算上限、历史文案 | 1 天 | 5～10 个姿态一次提交，失败可单独重试 |
| P4 | 10 素材 × 3 姿态对比实验与报告 | 0.5～1 天 | 量化结论：保留 / 仅备用 / 移除 |

> 每阶段独立可验收；P0 不通过（例如审核大面积拦截、质量不达标）就不进入 P1。

---

## 11. 风险与对策

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

## 12. 待确认问题（评审时必须拍板）

1. **是否接受素材图/姿态图上传到火山引擎方舟？** 这是本方案的前置条件；不接受就不做。
2. 使用哪个火山账号 / 区域 / API Key？模型是否已开通（`doubao-seedream-5-0-flash-260915`）？
3. 每日/每批预算上限是多少？`SEEDREAM_DAILY_BUDGET_CNY=5.0` 是否合适？
4. 水印是否关闭（`watermark=false`）？生成图对外分发时谁负责合规？
5. 成人向姿态被审核拦截时，是「提示后跳过」还是「整批中止」？
6. 多姿态一次最多 10 个是否够用？是否需要 P1 的批量接口？
7. 是否需要保留「同参复现」？如果需要，云端档位只能作为补充，不能替代本地。

---

## 13. 附录

### 13.1 常用尺寸

| 场景 | 建议 `size` | 总像素 | 说明 |
| --- | --- | --- | --- |
| 全身 3:4 | `1152x1536` | 1,769,472 | 与 900×1200 姿态图同比例，质量足够 |
| 全身 3:4 高清 | `1536x2048` | 3,145,728 | 推荐默认；2K 档，价格相同 |
| 半身 1:1 | `1024x1024` | 1,048,576 | 上半身姿态 |
| 半身 1:1 高清 | `1536x1536` | 2,359,296 | 2K 档 |
| 横构图 4:3 | `1536x1152` | 1,769,472 | 双人/横向场景（本期不用） |

> 显式像素必须同时满足：总像素 921,600～4,624,220；宽高比 1/16～16。`512x512`（262,144）会 400。

### 13.2 图片预处理约定

```text
素材图：EXIF 方向校正（按 Orientation 转正，不保留 EXIF）→ 转 RGB → 长边 ≤ 2048 → JPEG quality 90
姿态图：保留 PNG（线稿细节）→ 长边 ≤ 2048；透明背景先贴白底；必要时转黑白剪影做 A/B
两张图：单图 ≤ 30 MB；总像素 ≤ 3600 万；输出图下载上限 32 MB
```

### 13.3 与本地链路的代码复用对照

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

### 13.4 参考链接

- 模型详情：<https://ark.volcengine.com/region:cn-beijing/model/detail?Id=doubao-seedream-5-0-flash>
- 图片生成 API：<https://console.volcengine.com/ark/region:cn-beijing/docs/image-generation-api>
- 提示词指南：<https://ark.volcengine.com/region:cn-beijing/docs/ark/seedream-4-0-5-0-prompt-guide>
- Base URL 与鉴权：<https://docs.volcengine.com/docs/ark/base-url-and-authentication>
- 官方 SDK / OpenAI 兼容：<https://docs.volcengine.com/docs/ark/compatible-with-openai-sdk>


---

## 14. 实施记录（2026-11-02）

### 14.1 已完成

| 层 | 内容 |
| --- | --- |
| 配置 | `config.py` 新增 `ark_enabled` / `ark_api_key` / `ark_base_url` / `seedream_model` / `seedream_timeout` / `seedream_max_poses` / `seedream_daily_budget_cny`；`.env.example` 只放空 key |
| 档位 | `MODEL_SPECS` 新增 `seedream_5_0_flash`（provider=ark、i2i、cost 0.12、supports_seed/negative=false、requires_upload=true）；探测跳过非 ComfyUI 档位，`/status` 增加 `ark` 状态 |
| 客户端 | 新增 `seedream_client.py`：data URI、尺寸校验、URL/b64 解析、429/5xx/超时重试、400/401/403/404/审核拒绝永久失败、结果下载与魔数校验 |
| 提交 | `create_run` provider=ark 分支：仅 i2i、恰好 1 张素材、pose_id 必填、count=1；固定包装模板 + pose_prompts；日预算软上限；写入 `workflow_name=ark_seedream`、`workflow_hash=ark:<model>`、params provider/size/watermark/model_id |
| 执行 | `image_gen` 早期分支到 `_execute_seedream`：素材图/姿态图预处理（长边≤2048、去 EXIF）、调用、落盘 `01_seed-0.png`、状态流转与可重试/永久错误 |
| 前端 | 云端档位带「云端」标，隐藏 steps/cfg/seed/负向词；显示 1K/1.5K/2K 尺寸、水印、单张费用；首次会话隐私确认弹窗；单姿态提交 |
| 测试 | `test_seedream_client.py`（MIME/data URI/尺寸/429 重试/错误映射/无 key）、`test_generation_seedream.py`（provider 分支校验/提示词/模型可见性/执行器落盘）；前端云端档位显隐用例 |

### 14.2 P3 多姿态（2026-11-02 补齐）

- `PosePickerModal` 支持多选（≤10 个，按选择顺序），云端档位用「选择多个姿势」；
- 参数面板为每个姿态生成独立 `CreateRunPayload`（固定 1 张素材 + 各自 pose_id），一次提交 N 个批次；
- `useImageGen.submitBatch`：最多 2 并发、逐批收集成功/失败，展示最后一个成功批次，失败项不重复提交；
- 费用提示按「已选姿态数 × ¥0.12」计算；隐私确认一次覆盖整批；
- 测试：前端新增多姿态提交用例、submitBatch 成功/部分失败用例、隐私确认未确认不发出批次用例；全量前端 886 项通过。
- 对账：执行成功后把 `ark_request_id` / `ark_elapsed_s` / `ark_usage` 写回 `params_json`（不记录 key/base64/图片）；README 已补充云端通道说明。

### 14.3 P0 真实联调（2026-11-02 完成）

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

### 14.4 未完成 / 待用户决定

- **P4 对比实验**（方案 §9.4）：10 素材 × 3 姿态与本地链路对比、盲评与报告，费用约 ¥3.6+，待用户确认预算。
- **验收口径补全**：本机真实 API 路径（web → worker）尚未跑，需要在 backend/worker 重启后提交 1 个批次确认状态流转。
- **水印/预算默认值**：当前 `watermark=false`、日预算 ¥5，按方案默认值实现，可在 `.env` 调整；实际账单以方舟为准。

---

## 15. 评审修复记录（2026-11-03，提交 `9c0fecd` 之后）

> 依据 [豆包 Seedream 5.0 Flash 接入提交代码评测（3645006）](./豆包Seedream5.0Flash提交代码评测-2026-11-03.md)
> 的 §7 建议顺序逐项修复。本文只记录**当前实际实现**，旧行为不再保留。

### 15.1 修复清单

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

### 15.2 新增/调整的测试

| 文件 | 覆盖 |
| --- | --- |
| `backend/tests/test_seedream_client.py` | 下载失败只重试下载（生成只调 1 次）、429 Retry-After、中文审核映射、下载大小上限、取消回调、prompt 300 字截断 |
| `backend/tests/test_generation_seedream.py` | 用户 prompt 进入 `run.prompt`、seed 为 None、双人姿态 400、retry 保留 size/watermark、准备阶段三类失败均写 failed、真实尺寸回写、model_id 来自 params、执行前预算二次检查、取消置 cancelled |
| `backend/tests/test_unit_seedream_image.py` | EXIF 方向转正、透明图贴白底、长边缩放 |
| `web/src/components/generate/__tests__/GenerateParamForm.test.ts` | 带入云端历史参数恢复 size/水印/姿态、双人姿态过滤、`max_poses` 配置生效 |
| `web/src/components/generate/__tests__/GenerateCandidatesPanel.test.ts` | 云端无 seed 文案 + 「重新生成」按钮 |
| `web/src/components/generate/__tests__/GenerateHistoryTable.test.ts` | 云端批次「重新生成」文案、候选图「无 seed（每次随机）」 |
| `web/src/views/__tests__/GenerateView.test.ts` | ComfyUI 未启动 + 云端就绪时横幅不出现 undefined |

### 15.3 边界说明

- 云端取消只能做到「尽快终止本地等待」；**已经发到方舟的请求仍可能计费**，UI 不再承诺零费用取消。
- 下载最终失败不会自动重试生成（防止重复计费），错误信息保留云端 URL；URL 24h 失效，超时后只能重新生成。
- 双人姿态一期直接拒绝；若后续要支持，需要 2 张素材图 + 双人物提示词模板，不能复用当前单人物模板。
- 预算锁只在单 API 进程内原子；多进程部署需换成数据库/Redis 级原子检查。
