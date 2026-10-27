"""生成失败原因分类诊断（只读）。

用途：判断「出图老失败」到底是哪一类问题 —— 显存 OOM、工作流/参数错误、参考图问题、
ComfyUI 连接失败，还是其它。**先有数据再决定换不换模型**：如果是 OOM，换更大的模型只会更糟。

只读：仅 SELECT，不写库、不改文件。

用法：
    python scripts/oneoff/diagnose_generation_failures.py           # 最近 200 个失败的批次
    python scripts/oneoff/diagnose_generation_failures.py --limit 0   # 全量
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import func, select  # noqa: E402

from app.database import async_session  # noqa: E402
from app.models.generation import GenerationRun  # noqa: E402

"""失败原因分类规则：命中即归类（顺序即优先级，越具体越靠前）。

为什么这样分：这四类对应的处置完全不同 ——
- oom：降分辨率/换小模型/等显存，跟 prompt 无关；
- workflow：模板或参数不对，要看 ComfyUI 的节点报错；
- reference：参考图本身有问题（透明通道/过小/解码失败）；
- connection：ComfyUI 没开或端口不通，属于环境问题。
"""

_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("oom", ("out of memory", "cuda oom", "cuda error", "allocation", "显存")),
    ("timeout", ("timeout", "timed out", "超时")),
    ("connection", ("connection", "connect", "refused", "无法连接", "未启动", "comfyui 未")),
    ("workflow", ("workflow", "node", "prompt_outputs_failed", "invalid prompt", "value not in list")),
    ("reference", ("reference", "参考", "image decode", "unidentifiedimage", "cannot identify")),
    ("cancelled", ("cancel", "取消", "中止")),
)


def classify(error: str | None) -> str:
    """把错误文本归类到一个桶名（无法归类返回 ``other``）。"""
    text = (error or "").strip().lower()
    if not text:
        return "no_error_field"
    for name, keywords in _RULES:
        if any(k in text for k in keywords):
            return name
    return "other"


async def run(limit: int) -> int:
    """统计失败批次的分类分布，并给出每类样例。"""
    async with async_session() as db:
        total = (await db.execute(select(func.count(GenerationRun.id)))).scalar() or 0
        failed_q = select(GenerationRun.id, GenerationRun.status, GenerationRun.error).where(
            GenerationRun.status.in_(("failed", "cancelled"))
        )
        if limit > 0:
            failed_q = failed_q.order_by(GenerationRun.id.desc()).limit(limit)
        rows = (await db.execute(failed_q)).all()

    print(f"批次总数：{total}；本次分析失败/取消批次：{len(rows)}")
    if not rows:
        print("没有失败批次，无需诊断。")
        return 0

    buckets: collections.Counter[str] = collections.Counter()
    samples: dict[str, list[str]] = collections.defaultdict(list)
    for _rid, status, error in rows:
        name = classify(error) if status == "failed" else "cancelled"
        buckets[name] += 1
        if len(samples[name]) < 3 and error:
            samples[name].append(str(error).replace("\n", " ")[:100])

    print("\n失败原因分布：")
    for name, count in buckets.most_common():
        pct = count / len(rows) * 100
        print(f"  {count:4d}  ({pct:5.1f}%)  {name}")
        for sample in samples[name]:
            print(f"         例：{sample}")

    print("\n建议：")
    if buckets.get("oom"):
        print("  · 有 OOM：先把分辨率降到 1024² 以下试一档；换更大的模型只会更糟（显存需求更高）")
    if buckets.get("workflow"):
        print("  · 有 workflow 类失败：对照 ComfyUI 控制台的节点报错，检查 workflows/*.json 与 MODEL_SPECS.defaults")
    if buckets.get("reference"):
        print("  · 有 reference 类失败：检查参考图（透明通道、极小图、无法解码）")
    if buckets.get("connection"):
        print("  · 有 connection 类失败：ComfyUI 没启动或端口不通（环境问题，不是模型/内容问题）")
    if buckets.get("other") or buckets.get("no_error_field"):
        print("  · 其它/无错误文本：需要看 ComfyUI 原始日志（本脚本只能按 error 字段归类）")
    return 0


def main() -> int:
    """解析参数并执行。"""
    parser = argparse.ArgumentParser(description="生成失败原因分类诊断（只读）")
    parser.add_argument("--limit", type=int, default=200, help="分析最近 N 个失败批次，0=全量")
    args = parser.parse_args()
    return asyncio.run(run(limit=args.limit))


if __name__ == "__main__":
    sys.exit(main())
