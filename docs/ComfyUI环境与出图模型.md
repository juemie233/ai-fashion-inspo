# ComfyUI 运行环境与出图模型（技术参考）

> **状态**：现行（2026-10-31 校对）。如与代码不一致，以代码为准。


> **这是什么**：本机「本地出图」环境的说明书 —— 装了什么、为什么这么选、实测能力边界在哪、
> 出问题怎么查、要重装或升级怎么做。安装已于 **2026-10 完成并通过验收**，本文记录的是
> **当前状态**，不是待执行清单。
>
> 出图如何接进项目（API、任务队列、候选区、确认入库）见
> [ComfyUI出图接入方案.md](./ComfyUI出图接入方案.md)；网页与命令行的日常用法见该文 §16。
>
> 最后核对：**2026-10-23**（ComfyUI 0.42.1，三档全部「节点与模型就绪」）。

## 1. 环境基线

| 项 | 值 | 影响 |
| --- | --- | --- |
| GPU | NVIDIA GeForce RTX 5060 Ti，**16311 MiB**（Blackwell / sm_120），驱动 **616.92** | 只能跑「16 GB 档」量化模型 |
| CPU / 内存 | i5-12600KF / **16 GB** | **内存比显存更紧**：加载大模型时 Windows 容易吃满 |
| 系统 | Windows 11 | 用 portable 包，不走 WSL/Docker |
| ComfyUI | **0.42.1**（Windows portable），前端 `comfyui_frontend_package` **1.55.18** | — |
| 安装路径 | `D:\AI\ComfyUI`（本体 `D:\AI\ComfyUI\ComfyUI`，便携 Python `python_embeded`） | **必须在仓库之外** |
| Python / torch | 3.13.14（portable 自带）/ **2.14.0+cu130**，`torch.version.cuda=13.0`，`capability=(12, 0)` | Blackwell 需 CUDA ≥ 12.8；**自带已满足，不要升** |
| 服务端口 | `8188`（仅本机） | 与项目后端（18888）互不相干 |
| 启停 | 双击 `D:\AI\ComfyUI\run_nvidia_gpu.bat`；**关窗口即停** | 无服务、无自启、不占端口 |
| 磁盘占用 | `D:\AI\ComfyUI` = **15.50 GiB**（62,685 个文件）；权重另计见 §2 | 安装包 1.88 GiB 在 `D:\AI\_downloads`（可删） |

两条踩过的坑，写在这里免得再犯：

1. **portable 包自带的 torch 已经是对的**（cu130 ≥ Blackwell 要求的 cu128）。只有校验真的不过
   （启动报 `no kernel image is available` / 提到 `sm_120`）才升级 torch，升级前先备份整个目录。
2. **内存只有 16 GB** 是这台机器的真实短板：模型加载 + 浏览器容易把内存吃满，
   表现是「出图极慢」（被 CPU offload），不是模型坏了。

## 2. 已装模型

### 2.1 目录约定

```
D:\AI\ComfyUI\ComfyUI\models\
├─ diffusion_models\     扩散主干（UNet / DiT）
├─ text_encoders\        文本编码器（CLIPLoader 读这里）
├─ vae\                  VAE
├─ checkpoints\          单体 checkpoint（本项目未用）
└─ loras\                LoRA（本项目未用）
```

**放错目录 = 对应节点的下拉里根本看不到它**（ComfyUI 只认它自己扫到的位置）。
`models\{unet,clip}` 这类旧目录名在新版里已合并为上面这套，别照着旧教程放。

### 2.2 档位 A：Z-Image-Turbo（文生图，速度快）

| 文件（相对 `models\`） | 体积 | 角色 |
| --- | --- | --- |
| `diffusion_models\z_image_turbo_int8_convrot.safetensors` | 5.78 GiB | 扩散主干（int8 量化） |
| `text_encoders\qwen_3_4b_fp8_mixed.safetensors` | 5.25 GiB | 文本编码器（`CLIPLoader.type = lumina2`） |
| `vae\ae.safetensors` | 0.31 GiB | VAE |

sha256（重装下载后必须比对，2026-10-21 实测全部匹配）：

| 文件 | sha256 |
| --- | --- |
| `z_image_turbo_int8_convrot.safetensors` | `be517ebd47c912a5626a588e1aeea43e6be4a43c0cdcd2b48a2a780d9f358635` |
| `qwen_3_4b_fp8_mixed.safetensors` | `72450b19758172c5a7273cf7de729d1c17e7f434a104a00167624cba94f68f15` |
| `ae.safetensors` | `afc8e28272cd15db3919bacdb6918ce9c1ed22e96cb12c4d5ed0fba823529e38` |

来源：`Comfy-Org/z_image_turbo`（HF）。同仓库另有 `bf16`(12.31 GB) 与 `nvfp4`(4.51 GB) 两档，
本项目选 int8 是因为 5.78 + 5.25 = 11.0 GiB 在 15.9 GiB 可用显存里还留得出余量。

### 2.3 档位 B：Qwen-Image 2.1（参考图改款 / 也可文生图）

| 文件 | 体积 | 角色 |
| --- | --- | --- |
| `diffusion_models\qwen_image_2.1_int8_convrot.safetensors` | 6.76 GiB | 扩散主干（标准版） |
| `diffusion_models\qwen-image-2.1-UC-int8_convrot.safetensors` | 6.76 GiB | 同一套图的**未审查**权重（前端标红警示） |
| `text_encoders\qwen3vl_8b_w4a8.safetensors` | 5.88 GiB | **真正的文本编码器**（`type = qwen_image`） |
| `vae\qwen_image_2.1_vae_bf16.safetensors` | 0.63 GiB | 专用 VAE |
| `text_encoders\qwen3.5_9b_qwen_image_2.1_pe_i2i_w4a8.safetensors` | 6.26 GiB | **提示词改写 LLM**，不是编码器（见 §2.5） |

标准档合计 13.27 GiB，在 15.9 GiB 可用显存里是「刚好装满」——这也是本项目只给 1024²、
不给更大尺寸的原因（见 §4）。

### 2.4 为什么是 int8 / w4a8

16 GB 卡上真正决定「能不能跑」的是三件权重之和，而不是模型参数量：

| 组合 | 合计 | 结论 |
| --- | --- | --- |
| Z-Image **bf16** 主干 + bf16 编码器 | 12.31 + 8.04 = 20.4 GiB | 超显存，靠 offload，慢且易 OOM ✗ |
| **Z-Image int8 + fp8_mixed 编码器** | **11.03 GiB** | 稳态 6~7 s，留余量 ✓ |
| Qwen 2.1 **bf16** 主干 | 14.2 GiB 单项 | 直接挤爆 ✗ |
| **Qwen 2.1 int8 + w4a8 编码器** | **13.27 GiB** | 可跑，无余量 ✓ |

### 2.5 名字陷阱：`pe_i2i` / `pe_t2i` 不是文本编码器

`qwen3.5_9b_qwen_image_2.1_pe_i2i_w4a8.safetensors` 长得像「i2i 的文本编码器」，实际是官方
那条**提示词改写 LLM**（模板里接 `TextGenerate` 节点，把中文短句扩写成英文长描述）。

把它塞进 `CLIPLoader` 当图像条件编码器用 → 条件向量是垃圾 → **出图整张纯噪声，且 ComfyUI
不报任何错**。正确做法：编码器用 `qwen3vl_8b_*` + `type=qwen_image`；`pe_i2i` 想用就得按官方
模板接 `TextGenerate`，而那条链路在 16 GB 上装不下（主编码器 5.88 + 改写 LLM 6.26 + 扩散 6.76
= 18.9 GiB > 15.9 可用）。

> 排查顺序记牢：**先看 `CLIPLoader` 的文件与 type 是否成对，再看 latent 来源，最后才怀疑步数/cfg。**
> 完整复盘见设计文档 §15.6.1。

### 2.6 未安装的备选档位

| 档位 | 为什么没装 |
| --- | --- |
| FLUX.1 dev fp8（约 12 GB） | 生态最强（LoRA/ControlNet 多），但要 `clip_l`+`t5xxl`+`ae` 三件套；本项目出图目标单一，先不铺 |
| HiDream-I1 fp8（17.1 GB 标注 16 GB 卡） | 中文提示词理解强，但属「刚好卡线」，还要额外 4 个编码器 |
| SDXL（单体 checkpoint） | 只适合批量初筛/基线对比，画质与文字不如上面几档 |

真要用起来，按 §2 的目录约定放权重 → `--check` 看是否就绪 → 再进项目（需要额外档位就在
`backend/app/services/comfyui/models.py` 里加一条 `MODEL_SPECS`，模板可复用现有两份）。

### 2.7 许可提醒

**Qwen Research License：默认仅研究/评估，商用需另行授权。** 商用前先停下来确认，
不要默认「跑得通就能商用」。Z-Image 的许可以其 HF 仓库页面为准。

## 3. 工作流的两种形态

同一个模型有两种「喂参数」的方式，混用是最常见的坑：

| | **页面 UI 工作流** | **仓库 API 模板** |
| --- | --- | --- |
| 位置 | `D:\AI\ComfyUI\ComfyUI\user\default\workflows\` | `backend/app/services/comfyui/workflows\` |
| 格式 | `{"nodes": [...], "links": [...]}`（含布局、subgraph） | `{"1": {"class_type": ..., "inputs": ...}}` |
| 谁在用 | 人在页面里点（改文字 → Queue） | 后端 `/prompt` 提交（任务队列出图） |
| 参数 | 已写死在节点里 | **占位符**（`__SEED__` 等），提交前注入 |
| 能不能互拖 | 拖进页面 ✅ | 拖进页面会因占位符报「参数非法」，要先 `--dump` 导出 |

已装的三张页面工作流（`user\default\workflows\`）：

| 文件 | 内容 | 状态 |
| --- | --- | --- |
| `默认.json` | Qwen-Image 2.1 文生图（10 节点，内置节点，1024²/8 步/cfg 1/res_multistep/AuraFlow shift 3） | 可用；**编码器必须是 `qwen3vl_8b_w4a8` + `qwen_image`** |
| `image_z_image_turbo_int8.json` | 官方 Z-Image 模板（12 节点，含 1 个 subgraph） | 可用 |
| `qwen_image_2.1_uncensored_t2i.json` | 未审查 UNET 的 t2i 变体（19 节点，含 1 个 subgraph） | 可用 |

想在页面里用「仓库模板的参数」：先导出填好值的版本再拖进去 ——
`python backend/scripts/generate_image.py --dump <路径>.json "描述"`，脚本会告诉你改哪个节点。
导出后按 `Ctrl+S` 存进 `user\default\workflows\`，以后直接从左侧列表打开即可。

## 4. 实测性能基线

本机实测（RTX 5060 Ti 16 GB，Windows 11，2026-10-21 起多次复测）：

| 场景 | 耗时 | 峰值显存 | 备注 |
| --- | --- | --- | --- |
| Z-Image 1024² 首张（含加载模型） | **30.7 s** | 15.1 GiB | 一次性开销 |
| Z-Image 1024² 稳态（连续 3 张） | **6.1 / 6.2 / 7.3 s** | 15.1 GiB | 余量仅 0.8 GiB |
| Z-Image 2560×1440 | **37.5 s** | 15.2 GiB | 能跑，但**没有余量**，再大先关浏览器 |
| Qwen 2.1 参考图改款 1 张 | **20.8 s** | — | 参考图 1 张（1024²） |
| Qwen 2.1 参考图改款 3 张 | **37.5 s** | — | 未 OOM |
| Qwen 2.1 文生图 1024² | **11.1 s** | — | 8 步 / cfg 1 / res_multistep / AuraFlow shift 3 |

**能力边界**：16 GB 卡上 1024² 就是 Z-Image 的舒适区（峰 15.1 GiB），2560×1440 属「能跑但没
余量」；Qwen 2.1 因权重更大，项目侧只放行 1024²（`MODEL_SPECS[...]["max_pixels"]`）。

**显存口径**：`/system_stats` 报 `vram_total` 16283 MiB，`nvidia-smi` 报 16311 MiB，
差 28 MiB 是驱动保留；关闭 ComfyUI 后模型占的显存全部释放，剩余约 2.1 GiB 是桌面/浏览器常态占用，
不是 ComfyUI 残留。

## 5. 与 Ollama 的显存互斥

项目用 `Qwen3-VL:8B`（Ollama）做打标/审核，**与出图抢同一块 16 GB 显存**。两条路：

```powershell
ollama ps                        # 看当前驻留
ollama stop qwen3-vl:8b-instruct # 卸载（名字以 ollama ps 输出为准）
```

项目侧另有现成接口（端口以 `backend\.env` 的 `PORT` 为准，默认 18888，需 api key 若已配置）：

```
POST http://127.0.0.1:18888/api/ai/unload-model
```

出图结束后正常跑项目任务即可，Ollama 会按需重新加载。项目侧执行器本身也有 `wait_for_vram`
守门（等显存、超时前可取消），所以「显存不够」在正式链路里表现为**排队**，在页面里表现为 OOM。

## 6. 运维

### 6.1 启停

- 启动：双击 `D:\AI\ComfyUI\run_nvidia_gpu.bat`，约 30 秒内就绪（`/system_stats` 返回 200）。
- 停止：关掉那个终端窗口；确认残留进程：
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? { $_.ExecutablePath -like 'D:\AI\ComfyUI*' }`
- 首次启动不要求先停 Ollama（实测 `vram_free` 已有 14.82 GiB）；**跑大图前**才需要。

### 6.2 健康检查（三个判据一次看完）

```bash
python backend/scripts/generate_image.py --check
```

| 判据 | 不合格时的表现 |
| --- | --- |
| **节点就位** | 模板用到某 `class_type`，而 `/object_info` 里没有 → 环境升级导致节点改名/被移除 |
| **权重就位** | 文件在盘上但节点的下拉里没有 → 目录放错；下拉里没有该文件名 → 文件缺失 |
| **显存够** | 可用显存 < 档位建议值 → 出图会 OOM 或大幅 offload |

`--check` 同时会打印 ComfyUI 版本、峰值显存与每个档位的就绪结论；日常排障先跑它。

### 6.3 升级

- **ComfyUI 升级**：换新的 portable 包解压到新目录，把 `models\` 与 `user\default\workflows\`
  搬过去（或改成软链），再跑一次 `--check`。升级后**必须**重新确认三件事：节点名是否还在、
  编码器 type 是否还认、`/object_info` 里权重是否仍在下拉中。
- **torch 升级**：只在 §1 的 CUDA 校验真的不过时才做，命令见下（升级可能破坏 portable 自洽性）：

  ```powershell
  D:\AI\ComfyUI\python_embeded\python.exe -m pip install --upgrade torch torchvision torchaudio ^
    --index-url https://download.pytorch.org/whl/cu128
  ```

### 6.4 回滚 / 清理

```powershell
# 1) 先彻底退出 ComfyUI（关窗口 + 确认进程没了）
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? { $_.ExecutablePath -like "D:\AI\ComfyUI*" } | Select-Object ProcessId, ExecutablePath
# 2) 确认路径无误后再删（整包含权重，删了要重下 ≈ 30 GB）
Remove-Item -LiteralPath "D:\AI\ComfyUI" -Recurse -Force
```

- **不要**删 `%USERPROFILE%\.cache\huggingface`（下载缓存，留着下次省流量）。
- **不要**动项目仓库与 `backend\storage\`（那里是素材库，与出图环境无关）。
- 卸载后 `nvidia-smi` 显存应回到 < 1 GB 基线。

## 7. 重装要点

1. 下载 ComfyUI Windows portable（NVIDIA）→ 解压到 `D:\AI\ComfyUI`（**不要放仓库里**）。
2. 校验 torch（Blackwell 必查）：
   `.\python_embeded\python.exe -c "import torch;print(torch.__version__, torch.version.cuda, torch.cuda.get_device_capability(0))"`
   → 必须是 `cu128` 及以上、`(12, 0)`。
3. 拉权重（本机实测：`github.com` / `huggingface.co` 直连不通，用镜像；`download.pytorch.org` 可用）：

   ```powershell
   $base = "https://hf-mirror.com/Comfy-Org/z_image_turbo/resolve/main"
   $out  = "D:\AI\_downloads"
   foreach ($p in @("split_files/diffusion_models/z_image_turbo_int8_convrot.safetensors",
                    "split_files/text_encoders/qwen_3_4b_fp8_mixed.safetensors",
                    "split_files/vae/ae.safetensors")) {
     $dst = Join-Path $out $p
     New-Item -ItemType Directory -Force -Path (Split-Path $dst -Parent) | Out-Null
     curl.exe -L -C - --connect-timeout 20 --speed-limit 102400 --speed-time 20 `
       --retry 8 --retry-delay 5 --retry-all-errors -o $dst "$base/$($p)?download=true"
   }
   ```

   ⚠️ **`--speed-limit/--speed-time` 不能省**：hf-mirror 下大文件会「假活」（进程还在、文件不再
   增长，实测卡在 5.21/6.20 GB），不做停速检测会一直挂着。
4. 下完**逐个比对 sha256**（镜像不保证零错漏），期望值从 HF API 的 `lfs.oid` 取：

   ```powershell
   curl.exe -s "https://hf-mirror.com/api/models/Comfy-Org/z_image_turbo/tree/main?recursive=true"
   certutil -hashfile "D:\AI\_downloads\split_files\diffusion_models\z_image_turbo_int8_convrot.safetensors" SHA256
   ```

5. 按 §2.1 的目录放好 → 启动 → `python backend/scripts/generate_image.py --check` 三判据全绿。

## 8. 故障排查（按症状查）

| 症状 | 常见原因 | 处理 |
| --- | --- | --- |
| 启动报 `no kernel image is available` / 提到 `sm_120` | PyTorch 的 CUDA < 12.8（Blackwell 不支持） | 按 §6.3 升级到 cu128+，重跑校验 |
| `torch.cuda.is_available()` 为 False | 装成 CPU 版 torch，或驱动太旧 | 重装 cu128 轮子；先确认 `nvidia-smi` 正常 |
| 加载模型时报 `Allocation on device` / OOM | 选了 bf16/fp16 大权重，或 Ollama 还占着显存 | 换 int8/w4a8；先 `ollama stop` |
| 模型下拉里看不到刚下的文件 | 放错目录（diffusion_models / text_encoders / vae） | 按 §2.1 归位，页面刷新 |
| 出图糊 / 全黑 | 文本编码器或 VAE 选错 | 核对三个加载节点 |
| **出图整张纯噪声**（跑得完、进度 100%、**不报错**） | `CLIPLoader` 的**权重与 type 不成对**（如把 `pe_i2i` 当编码器、或给 Qwen 编码器填 `lumina2`） | Qwen 2.1 → `qwen3vl_8b_w4a8` + `qwen_image`；Z-Image → `qwen_3_4b_fp8_mixed` + `lumina2`（§2.5） |
| 下载到一半失败 / 卡住 | HF 直连不稳、镜像「假活」 | 设 `HF_ENDPOINT=https://hf-mirror.com`，用 §7 的续传命令 |
| 出图极慢（> 5 min/张） | 模型被 CPU offload（16 GB 内存不够） | 降分辨率 / 换小档位；考虑加内存 |
| 节点 `Unet Loader (GGUF)` 不存在 | 没装 ComfyUI-GGUF 插件（只有用 GGUF 量化才需要） | 装插件并重启；本项目未用 GGUF |
| 浏览器打不开 8188 | 端口被占 / 进程已退出 | `Get-NetTCPConnection -LocalPort 8188`；换端口或重开 |

## 9. 参考资料

1. Z-Image-Turbo 在 RTX 5060 Ti 16 GB 上的实测（17.55 s / 2560×1440 65.7 s）：
   <https://blog.aidec.tw/post/z-image-turbo>
2. HiDream-I1 各精度显存对照（fp8 17.1 GB / 16 GB 卡；GGUF Q4≈12、Q2≈8）：
   <https://comfyui-wiki.com/zh/tutorial/advanced/image/hidream/i1-t2i>
3. Flux 各精度显存需求（FP16 23.8 GB / FP8≈12 GB / GGUF Q4 6–8 GB）：
   <https://evezone.evetech.co.za/daily-drop/new-flux-image-models-and-the-vram-they-demand-on-local-rigs>
4. Qwen-Image-2.1 发布说明（7B 生成器 + Qwen3-VL-8B 编码器；bf16 14.2 GB / int8 7.26 GB；
   原生 2K、RGBA、最多 10 张参考图；**Qwen Research License 非商用默认**）：
   <https://qwenimages.com/tw/blog/qwen-image-2-1-release>
5. Z-Image 权重（ComfyUI 组织）：<https://huggingface.co/Comfy-Org/z_image_turbo>；
   官方仓库：<https://github.com/TongyiLab/Z-Image>
6. ComfyUI 发布页（Windows portable）：<https://github.com/comfyanonymous/ComfyUI/releases>
7. ComfyUI-GGUF 插件：<https://github.com/city96/ComfyUI-GGUF>
8. Z-Image 官方示例工作流：<https://comfyanonymous.github.io/ComfyUI_examples/z_image/>
9. Blackwell（RTX 50 系）需要 CUDA 12.8+ 的 PyTorch：
   <https://blog.comfy.org/p/how-to-get-comfyui-running-on-your>
10. 同卡跑 Flux2-dev fp8 的实测（速度参考）：<https://blog.aidec.tw/post/flux2-dev-fp8-comfyui>

> ⚠️ 来源 1、10 是个人博客的单次测试，不是独立基准；来源 4 的评测分数是厂商自报。
> 用它们判断「能不能跑 / 大概多快」可以，别当质量排名。

## 9.5 实验模型（不在产品依赖内，2026-10-31）

换姿势实验（已结项）额外下载、但**不属于本地出图主链路**的模型：

| 模型 | 路径 | 大小 | 用途 / 现状 |
| --- | --- | ---: | --- |
| SDXL base 1.0 | `models/checkpoints/sd_xl_base_1.0.safetensors` | 6.46 GiB | R2 ControlNet 实验；产品未用 |
| ControlNet-Union SDXL promax | `models/controlnet/controlnet-union-sdxl-1.0-promax.safetensors` | 2.34 GiB | lineart/depth 控制实验；产品未用 |
| Depth Anything V2 Small | `models/depthanything/depth_anything_v2_small.onnx` | 99 MB | 线稿→深度图（E3）；产品未用 |
| InsightFace buffalo_l + inswapper | `models/insightface/buffalo_l/` | 0.69 GiB | 换脸（A 方案）；未接入 API（非商用许可，仅本地） |
| GFPGAN 1.4 ONNX | `models/facerestore_models/gfpgan14-fp16.onnx` | 0.16 GiB | 面部修复；未接入 API |
| VNCCS Pose Studio QI2.1 v2 LoRA | `models/loras/qwen2.1/VNCCS_QI2_PoseStudioV2.safetensors` | 152 MB | Qwen 2.1 姿态 LoRA；int8 权重下效果崩坏，不可用 |

结论与资产处置见 [换姿势方向结项报告](换姿势方向结项报告-2026-10-31.md)；这些文件可整体保留或按报告 §5 清理。
**不要**把它们写进产品依赖或模型档位清单。

---

## 10. 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-10-21 | 首次安装 ComfyUI 0.42.1 + 档位 A（Z-Image int8），按 sha256 校验通过；实测 1024² 稳态 6.1~7.3 s |
| 2026-10-21 | 追加档位 B（Qwen-Image 2.1 int8 + w4a8 编码器 + 专用 VAE），图生图打通 |
| 2026-10-23 | 记录 `CLIPLoader` 权重/type 不匹配导致纯噪声的坑；补命令行工具 `--dump`（导出可拖进页面的工作流） |
| 2026-10-23 | 本文由「安装清单（供 AI 执行）」改写为技术参考：去掉待执行语气，补环境基线、工作流两种形态、运维与升级章节 |
