# scripts/oneoff —— 仍可用的一次性 / 诊断脚本

这里放**跑一次或偶尔排查时才用**的脚本：历史数据诊断、单次验证、外部工具下载。
与 `scripts/archive/` 的区别：

- `oneoff/`：**仍可能在当前代码上使用**，出问题会翻出来跑；仍纳入 `scripts/ci/check_scripts.py` 的规范检查；
- `archive/`：**已结项/不再维护**的实验脚本，只用于复现历史，不纳入门禁。

## 目录里的脚本

| 脚本 | 用途 | 用法 | 依赖 |
|---|---|---|---|
| `diag_lancedb.py` | 对比 LanceDB 实际向量数与素材数，判断向量回填是否漏 | `python scripts/oneoff/diag_lancedb.py` | 存储根（`STORAGE_ROOT`） |
| `diag_vector_backfill.py` | 查 `pending_vector_backfills` 积压与最近向量回填任务（**只读 `mode=ro`**） | `python scripts/oneoff/diag_vector_backfill.py` | 真实库 |
| `diagnose_generation_failures.py` | 出图失败分类：OOM / 工作流参数 / 参考图 / ComfyUI 连接 / 其它（**只读**） | `python scripts/oneoff/diagnose_generation_failures.py [--limit N]` | 真实库 |
| `download_hf_chunked.py` | 从 hf-mirror 分段下载大文件（镜像不支持全量 GET、连续请求会 429；支持断点续传） | `python scripts/oneoff/download_hf_chunked.py <url> <保存路径> <总字节数>` | 网络 |
| `verify_toolcall.py` | 验证本地 Ollama 模型能否返回结构化 `tool_calls`（Agent 能力前置检查） | `python scripts/oneoff/verify_toolcall.py [模型名，默认 qwen3.5:9b]` | 本机 Ollama |
| `diagnose_note_detail.py` | 诊断小红书笔记详情页 DOM（轮播图/视频选择器失效时用） | 先启动调试 Chrome（9222），再 `python scripts/oneoff/diagnose_note_detail.py <关键词>` | Playwright / 调试 Chrome |
| `verify_extension_extract.py` | 用 Playwright 模拟小红书笔记页，验证插件图片提取规则 | `python scripts/oneoff/verify_extension_extract.py` | Playwright / 网络 |
| `generate_icons.py` | 用 Pillow 生成浏览器插件占位图标（一次性资产生成） | `python scripts/oneoff/generate_icons.py` | Pillow |

## 已移出（2026-10-31）

R2/换姿势实验（`r2_*`、`make_depth_maps.py`、`pose_fidelity_ab.py`、`pose_prompt_ab_eval.py`）
与已完成使命的 `backfill_quality_reason_code.py` 已移到 `scripts/archive/`；该方向已结项，
结论见 `docs/换姿势方向结项报告-2026-10-31.md`。

## 使用约定

1. **只读优先**：诊断脚本一律以 `sqlite3 ... ?mode=ro` 打开库；要写库的脚本默认 dry-run，写库必须显式 `--apply`/`--delete`。
2. **不留常驻入口**：不要把它们接进 `scripts/ci/test.sh` / CI / Windows 任务计划；需要定期跑就升级到 `scripts/` 对应分类并补测试。
3. **删除优先于维护**：历史问题已彻底解决（字段已回填、模型已换掉）时直接删除，并在提交信息说明。
4. **路径**：本目录脚本用 `Path(__file__).resolve().parents[2]` 定位仓库根
   （`scripts/oneoff/x.py` → `oneoff` → `scripts` → 仓库根）；复制到别处时同步改层级。
