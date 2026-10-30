# scripts/oneoff —— 一次性 / 诊断脚本

这里放**跑一次就结束**的脚本：历史数据回填、故障诊断、外部镜像下载工具。

## 为什么单独一个目录

2026-10-28 的 scripts 审查发现 6 个脚本在文档、`*.bat`、`*.sh`、后端代码里**没有任何引用**——
它们不是死代码（都要用得上），但混在 `scripts/` 与 `backend/scripts/` 里会稀释「常用工具」的
入口。统一收进来后：

- 新同学扫 `scripts/` 与 `backend/scripts/` 看到的都是**日常会用**的东西；
- 这里的脚本默认**不进测试、不计覆盖率**，属于「用完即弃 / 出问题再翻出来用」的工具；
- 判断标准很简单：**未来是否还会定期跑**。会定期跑的 → 上级目录并配测试；只跑一次 → 放这里。

## 目录里的脚本

| 脚本 | 用途 | 用法 | 依赖 |
|---|---|---|---|
| `backfill_quality_reason_code.py` | 把历史 rejected 素材的自由文本原因翻译成 `quality_reason_code` 枚举（**不重跑模型、不改判定**） | `python scripts/oneoff/backfill_quality_reason_code.py`（默认 dry-run，加 `--apply` 写库）；实测当前库里待回填 **0 条**，脚本已完成使命 | 真实库（只读 + 可选写该字段） |
| `diag_lancedb.py` | 对比 LanceDB 里的实际向量数与素材数，判断向量回填是否漏 | `python scripts/oneoff/diag_lancedb.py` | 需能读到存储根（自定义存储根时先设 `STORAGE_ROOT`） |
| `diag_vector_backfill.py` | 查 `pending_vector_backfills` 积压与最近 12 个 vector_backfill 任务（**只读**，`mode=ro`） | `python scripts/oneoff/diag_vector_backfill.py` | 真实库 |
| `diagnose_generation_failures.py` | 给出图失败分类：OOM / 工作流参数 / 参考图 / ComfyUI 连接 / 其它（**只读**） | `python scripts/oneoff/diagnose_generation_failures.py [--limit N]` | 真实库 |
| `download_hf_chunked.py` | 从 hf-mirror 分段下载大文件（该镜像不支持全量 GET、连续请求会 429；支持断点续传） | `python scripts/oneoff/download_hf_chunked.py <url> <保存路径> <总字节数>` | 网络 |
| `verify_toolcall.py` | 验证本地 Ollama 模型能否返回结构化 `tool_calls`（agent 能力的前置检查） | `python scripts/oneoff/verify_toolcall.py [模型名，默认 qwen3.5:9b]` | 本机 Ollama |
| `r2_controlnet_poc.py` | R2 PoC 阶段1：SDXL + ControlNet-Union(lineart) 用姿势线稿控姿势（txt2img / img2img 对比） | `python scripts/oneoff/r2_controlnet_poc.py --dry-run` / `--execute`（需 SDXL + union 权重、ComfyUI 在 8188） | 本机 ComfyUI + storage |
| `r2_stage2_cleanup.py` | R2 PoC 阶段2（两段式）：Qwen-Image-Edit 以 `[阶段1图, 主体]` 为参考，保姿势、去护具、换回日常服装 | `python scripts/oneoff/r2_stage2_cleanup.py --dry-run` / `--execute`（需阶段1产物） | 本机 ComfyUI + storage |
| `pose_fidelity_ab.py` | 换姿势零成本对照实验：提示词/参考顺序/参考数量对姿势保真度的影响；也可批量随取四星素材 × 单人姿势做文字姿势测试（只读库 + 直连 ComfyUI，产物落 storage/poc/） | `--dry-run`（只打印工作流）/ `--execute`（需 ComfyUI 在 8188）/ `--batch --batch-subjects 5 --batch-poses 5 --execute` | 本机 ComfyUI + storage |

## 使用约定

1. **只读优先**：诊断类脚本一律以 `sqlite3 ... ?mode=ro` 打开库（`scripts/check_scripts.py`
   会检查这一点）；确实要写库的（`backfill_quality_reason_code`）默认 dry-run，写库必须显式 `--apply`。
2. **不留常驻入口**：不要把它们接进 `test.sh` / CI / 任务计划——一旦需要定期跑，就说明它已经
   不是「一次性」，应该升级成 `scripts/` 或 `backend/scripts/` 里的正式工具并补测试。
3. **删除优先于维护**：如果某个脚本对应的历史问题已经彻底解决（字段已回填完、模型已换掉），
   直接删掉并在提交信息里说明即可——留着会让人误以为还要跑。
4. **路径**：这些脚本用 `Path(__file__).resolve().parents[2]` 定位仓库根（`scripts/oneoff/x.py`
   → `oneoff` → `scripts` → 仓库根）。在别处复制走时记得同步改层级。

## 历史

- 2026-10-28：从 `scripts/` 与 `backend/scripts/` 归拢 6 个无引用脚本到本目录，并补本文档。
  移动同时修正了它们对仓库根层级的假设（多了一层目录）与失效的向量库路径提示。
