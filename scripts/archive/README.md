# scripts/archive —— 已结项 / 归档脚本

这里放**不再维护**的实验与一次性脚本：保留用于复现历史结果，不作为日常入口，
**不纳入 `scripts/ci/test.sh` / CI / `check_scripts.py` 门禁**。

> 归档不等于可随意运行：其中部分脚本写死了本机路径、依赖本机 ComfyUI/模型权重，
> 或使用**非商用**人脸/换脸模型；重跑前先读对应实验报告与脚本头部说明。

## 归档清单

| 脚本 | 原用途 | 归档原因 |
|---|---|---|
| `backfill_quality_reason_code.py` | 把历史 rejected 的自由文本原因回填为 `quality_reason_code` | 已完成使命（实测待回填 0 条）；保留 dry-run 逻辑备查 |
| `make_depth_maps.py` | 图库姿势线稿 → Depth Anything V2 深度图 | R2/换姿势方向结项 |
| `pose_fidelity_ab.py` | 换姿势零成本对照（提示词/参考顺序/参考数量） | R2/换姿势方向结项 |
| `pose_prompt_ab_eval.py` | 提示词增强 A/B（当前配置 vs +描述词 vs +负向词） | 换姿势实验收尾；实验报告见 `docs/换姿势实验报告-*` |
| `r2_batch10_eval.py` | R2 两段式 10 样本批量评估 | R2 方向结项 |
| `r2_controlnet_poc.py` | R2 PoC 阶段 1：SDXL + ControlNet-Union(lineart) | R2 方向结项 |
| `r2_stage2_cleanup.py` | R2 阶段 2：Qwen-Image-Edit 清洗（保姿势/换装） | R2 方向结项 |
| `r2_outfit_keep_eval.py` | 发型/服装保持实验 | R2 方向结项 |
| `r2_qwen_en_prompt_eval.py` | Qwen 英文约束 prompt / LoRA 实验 | R2 方向结项 |
| `r2_faceswap_batch.py` | InsightFace 换脸 + GFPGAN 后处理 | R2 方向结项；依赖**非商用**模型，仅个人本地复现 |

## 相关文档

- `docs/换姿势方向结项报告-2026-10-31.md`
- `docs/换姿势R2方案成本与代价评估.md`
- `docs/换姿势实验报告-2026-10-30-R2两段式10样本.md`
- `docs/换姿势实验报告-2026-10-30-R2换脸增强10样本.md`
- `docs/换姿势实验报告-2026-10-31-E3-Depth路线10样本.md`
- `docs/换姿势实验报告-2026-10-31-P0-Qwen英文prompt与LoRA.md`
- `docs/姿势图库换姿势（路径一）实现说明.md`（当前维护的「路径一」实现，不在本目录）

## 约定

1. **不再扩展**：新需求不要在本目录脚本上堆功能；需要重启方向时，先看结项报告第 6 节。
2. **不进门禁**：`check_scripts.py` 不扫描本目录；脚本内部的只读连接/argparse 规则可自行参考但不强制。
3. **路径**：多数脚本用 `Path(__file__).resolve().parents[2]` 定位仓库根；本目录与 `oneoff/` 同层级，层级不变。
4. **清理**：若确认历史结果不再需要复现，按「删除优先于维护」直接删除，并在提交信息说明。
