"""独立脚本：负样本初筛器的数据集构建、训练、评估与回滚（方案阶段 0/2）。

用法（任意目录执行均可）：
    python scripts/quality_learner.py status   # 查看样本量与分类器状态
    python scripts/quality_learner.py train    # 构建数据集并训练分类器，输出指标
    python scripts/quality_learner.py reset    # 删除模型，回滚到纯 VLM 审核

说明:
    - 正样本 = quality_status=approved 且未删除的图片素材
    - 负样本 = quality_status=rejected（未删除）或 trash_reason=质量差（已删除）
    - 输入为 LanceDB 中已存储的 512 维 CLIP 图像向量（需已回填，见 backfill_vectors.py）
    - 训练使用 sklearn 逻辑回归，无需 GPU；阈值见 config.quality_classifier_threshold
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 添加 backend 目录到 Python 路径（与 scripts/backfill_vectors.py 一致）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.database import async_session, init_db  # noqa: E402
from app.db_migrations import ensure_schema  # noqa: E402
from app.services import quality_learner  # noqa: E402


async def cmd_status() -> int:
    """打印初筛器状态与当前正负样本统计。"""
    status = quality_learner.get_status()
    async with async_session() as db:
        _, _, stats = await quality_learner.collect_samples(db)

    print("── 负样本初筛器状态 ──")
    print(f"  已训练: {'是' if status['trained'] else '否'}")
    print(f"  阈值(垃圾置信度): {status['threshold']}")
    print(f"  正样本(approved): {stats.get('positive_total', 0)}")
    print(f"  负样本(rejected/质量差): {stats.get('negative_total', 0)}")
    print(
        f"  含图像向量样本: {stats.get('with_vector', 0)} "
        f"(正 {stats.get('positive_with_vector', 0)} / 负 {stats.get('negative_with_vector', 0)})"
    )
    if status.get("meta"):
        m = status["meta"].get("metrics", {})
        print("  最近训练指标:")
        for k in ("accuracy", "precision", "recall", "f1", "false_reject_rate"):
            print(f"    {k}: {m.get(k)}")
        print(f"    混淆矩阵(合格=1): {m.get('confusion')}")
    return 0


async def cmd_train() -> int:
    """构建数据集并训练分类器，输出指标。"""
    async with async_session() as db:
        result = await quality_learner.train(db)

    if "error" in result:
        print(f"[错误] {result['error']}")
        return 1

    print("── 训练完成 ──")
    print(f"  样本: 正 {result.get('positive')} / 负 {result.get('negative')}")
    m = result.get("metrics", {})
    for k in ("accuracy", "precision", "recall", "f1", "false_reject_rate"):
        print(f"  {k}: {m.get(k)}")
    print(f"  混淆矩阵(合格=1): {m.get('confusion')}")
    print("  模型已保存到 storage/quality_classifier/（训练后自动生效）")
    return 0


def cmd_reset() -> int:
    """删除模型，回滚到纯 VLM 审核。"""
    result = quality_learner.reset()
    print(f"已回滚: {result}")
    return 0


async def main() -> int:
    """脚本入口。"""
    await init_db()
    await ensure_schema()

    parser = argparse.ArgumentParser(description="负样本初筛器（CLIP 向量 + sklearn）")
    parser.add_argument("command", choices=["status", "train", "reset"])
    args = parser.parse_args()

    if args.command == "status":
        return await cmd_status()
    if args.command == "train":
        return await cmd_train()
    return cmd_reset()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
