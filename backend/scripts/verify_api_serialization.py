"""真实数据自检：把「读素材 → 序列化」的接口路径在**真实库**上撒网跑一遍。

为什么需要这个脚本（2026-09-27 的教训）：
「素材 4ee7f266 没有相似推荐」的真因是**候选序列化**时的人物关联懒加载
（`MissingGreenlet` → 接口 500 → 前端静默吞成「暂无相似素材」）。这类 bug 有三个特点，
单元测试结构上就抓不到：
  1. **只在特定数据下发生**：候选里只要有一个带博主/模特关联就崩；测试库素材没有关联，
     所以永远绿（那条「相似推荐链路」用例还因为相似列表恒为空而根本没跑到候选序列化）；
  2. **失败被伪装**：异常被前端 catch 成「没有数据」，症状指向错误方向；
  3. **日志看不到**：500 的 traceback 不落在服务日志里。
本脚本针对第 1 点在**真实数据**上撒网：每条素材都用**独立会话**（与一次 HTTP 请求同构，
避免同一个 session 的 identity map 把已加载的关系掩盖掉），跑详情序列化 + 相似推荐
（含每个候选的序列化）。任何异常都会打印素材 ID、步骤、异常类型与首行信息。

用法：
    cd backend
    python -m scripts.verify_api_serialization                # 随机抽 200 条
    python -m scripts.verify_api_serialization --limit 2000   # 抽 2000 条
    python -m scripts.verify_api_serialization --sample 20    # 只打印前 20 个失败

副作用（与接口行为一致，不是本脚本引入的）：缺图像向量的素材会在检索时现场 CLIP 编码
并写回 LanceDB——这正是 `/api/search/similar/{id}` 自己的行为。不删改任何素材记录。

退出码：0 = 全部通过；1 = 有失败（或覆盖不足）。
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 与 backend/scripts 下其它脚本一致：把 backend 加入 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.database import async_session  # noqa: E402
from app.models.inspiration import Inspiration  # noqa: E402
from app.routers.search import _load_inspiration  # noqa: E402
from app.schemas.inspiration import inspiration_to_out  # noqa: E402
from app.services.vector.similarity import find_similar_hybrid  # noqa: E402

# 每条素材的相似推荐取多少条候选（与接口默认一致）
TOP_K = 10


async def _sample_ids(limit: int) -> list[str]:
    """随机抽 limit 条未删除素材的 ID（随机以覆盖不同数据结构）。"""
    async with async_session() as db:
        result = await db.execute(
            select(Inspiration.id)
            .where(Inspiration.deleted_at.is_(None))
            .order_by(func.random())
            .limit(limit)
        )
        return [row[0] for row in result.all()]


async def _check_one(insp_id: str) -> tuple[list[str], int, int]:
    """检查一条素材（独立会话，与一次 HTTP 请求同构）。

    每一步都自带 try：**任何一条素材出问题都只记为一条失败**，不能让整轮抽样中断
    （否则「跑 2000 条」可能在第 3 条就崩掉，且看不出是哪条）。

    返回 (失败描述列表, 序列化过的候选数, 其中带人物关联的候选数)。
    """
    failures: list[str] = []
    candidates = 0
    linked_candidates = 0

    async with async_session() as db:
        try:
            source = await _load_inspiration(db, insp_id)
        except Exception as e:
            return [_describe(insp_id, "加载素材", e)], 0, 0
        if source is None:
            return failures, 0, 0

        try:
            inspiration_to_out(source)
        except Exception as e:
            failures.append(_describe(insp_id, "详情序列化", e))

        try:
            hits = await find_similar_hybrid(db, source, TOP_K)
        except Exception as e:
            failures.append(_describe(insp_id, "相似检索", e))
            return failures, 0, 0

        for hit in hits:
            # 结果结构变了（不是 {"inspiration": ...}）时记一条失败继续跑，别整轮崩
            cand = hit.get("inspiration") if isinstance(hit, dict) else None
            if cand is None:
                failures.append(
                    f"{insp_id}  [候选结构]  相似结果缺少 inspiration 字段"
                    f"（{type(hit).__name__}）"
                )
                continue
            candidates += 1
            if cand.bloggers or cand.models:
                linked_candidates += 1
            try:
                inspiration_to_out(cand)
            except Exception as e:
                failures.append(_describe(cand.id, f"候选序列化（来源 {insp_id[:8]}）", e))

    return failures, candidates, linked_candidates


def _describe(insp_id: str, step: str, exc: Exception) -> str:
    first_line = str(exc).splitlines()[0] if str(exc) else "（无详情）"
    return f"{insp_id}  [{step}]  {type(exc).__name__}: {first_line}"


async def main() -> int:
    parser = argparse.ArgumentParser(description="真实数据自检：素材序列化 + 相似推荐")
    parser.add_argument("--limit", type=int, default=200, help="抽样素材条数（默认 200）")
    parser.add_argument("--sample", type=int, default=10, help="最多打印多少个失败样例")
    args = parser.parse_args()

    ids = await _sample_ids(args.limit)
    if not ids:
        print("库里没有可检查的素材（或被 --limit 过滤空）")
        return 1

    print(f"抽样 {len(ids)} 条素材，逐条走「详情序列化 + 相似推荐（含候选序列化）」...")

    all_failures: list[str] = []
    total_candidates = 0
    total_linked = 0
    for i, insp_id in enumerate(ids, start=1):
        failures, candidates, linked = await _check_one(insp_id)
        all_failures.extend(failures)
        total_candidates += candidates
        total_linked += linked
        if i % 50 == 0:
            print(f"  已检查 {i}/{len(ids)}，累计失败 {len(all_failures)}")

    print("\n===== 结果 =====")
    print(f"素材: {len(ids)} 条")
    print(f"候选序列化: {total_candidates} 个，其中带博主/模特关联 {total_linked} 个")
    print(f"失败: {len(all_failures)} 个")

    if all_failures:
        print(f"\n失败样例（最多 {args.sample} 个）：")
        for line in all_failures[: args.sample]:
            print(f"  - {line}")
        return 1

    # 覆盖自检：这次没扫到「带人物关联的候选」时，说明数据不足以证明这条路径没问题
    # （老用例就是这么绿的：相似列表恒为空，候选序列化从未被执行）
    if total_candidates == 0:
        print("\n[警告] 本次抽样没有产生任何候选，等于没验证候选序列化路径（覆盖不足）")
        return 1
    if total_linked == 0:
        print(
            "\n[警告] 本次抽样的候选里没有一个是带博主/模特关联的——"
            "正是这条路径曾 500，请加大 --limit 再跑一次（覆盖不足）"
        )
        return 1

    print("\n全部通过：没有素材在序列化/相似推荐路径上抛错")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
