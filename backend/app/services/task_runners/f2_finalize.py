"""f2 导入的收尾阶段：收藏夹归位 + 批次结算（切片 F）。

从 `task_runners/f2_import.py` 拆出的第六片（f2 导入的最后一块）。两件事：

1. ``_aggregate_collect_stage``：「我的收藏」把本批作品归位到**两级**收藏夹（一级
   「抖音入库自动收藏」是纯分类节点，永远不装素材；没有归属信息的进二级「未分类收藏」），
   并把「库里早就有」的作品一并补进合集；
2. ``_finalize_import``：写批次清单、按外部状态（取消/暂停）决定终态、把「成功但有话
   要说」的提示写入 ``result.notices``。

至此 f2_import.py 只剩任务创建 / 状态查询 / 自动调度 / 执行编排四件事。
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import TaskQueue
from app.services.task_runners.common import utcnow
from app.services.task_runners.f2_library_ids import collect_library_ids_by_aweme
from app.services.task_runners.f2_personal import (
    DOUYIN_AUTO_SOURCE,
    DOUYIN_ROOT_COLLECTION_NAME,
    UNCLASSIFIED_COLLECTION_NAME,
    _append_notice,
    _brief_reason,
    ensure_collection,
)

logger = logging.getLogger(__name__)


async def _aggregate_collect_stage(
    db: AsyncSession,
    task: TaskQueue,
    result: dict,
    extra_ids: list[str] | None = None,
    folder_map: dict[str, list[str]] | None = None,
) -> dict | None:
    """阶段 3c：「我的收藏」把作品归位到**两级**收藏夹。

    结构（用户口径，见 models/collection.py）：

    - 一级「抖音入库自动收藏」是**纯分类节点，永远不装素材**（用户明确要求：一级
      不允许有素材加进来）；点进去看到的是收藏夹卡片；
    - 二级：名字与抖音侧收藏夹一一对应（归属来自按夹下载时写的
      ``_collect_folders.json``，见 :func:`load_collect_folder_map`）；
    - **没有归属信息的作品**（平铺 ``-M collection`` 下载、清单缺失、清单里没记到的）
      落进二级「未分类收藏」——它们没有夹可归，但也不能塞进一级，否则一级又变成
      「几千件混在一起」的列表。正常按夹下载时这个夹不会出现。

    为什么用合集而不是标签：合集的语义就是「一批素材的集合」（合集里直接看到数量
    与体积），而标签是 AI/手动语义、会进入标签治理（去重/合并/健康扫描）——收藏来源是
    事实而非语义，不该污染标签体系。

    **合集要收的是「你的收藏」，不是「本批新入库的那几条」**：所以除了本批入库的素材，
    还要带上 ``extra_ids``——本次扫描/枚举到、但**已在库**因而没重新入库的素材
    （见 :func:`collect_library_ids_by_aweme`）。少了它，凡是「先喜欢/采集过、后来又
    收藏」的作品都会永远缺席合集。

    幂等：合集不存在则创建，已加入的素材不会重复（collection_items 有唯一约束）。
    失败不影响已入库素材，只写进任务 error 提示人工处理。
    """
    imported_ids = [str(i) for i in (result.get("ids") or []) if i]
    target_ids = list(dict.fromkeys(imported_ids + [str(i) for i in (extra_ids or []) if i]))
    folder_map = folder_map or {}
    if not target_ids and not folder_map:
        return None
    # 先把「本批入库」的集合算出来：下面统计「已在库补入」要按元素判定，
    # 写成 `i not in set(imported_ids)` 会每条都重建一次集合（O(n²)）
    imported_set = set(imported_ids)
    # 记下 id 备用于失败分支：rollback 会把 session 内对象全部 expire，届时连
    # ``task.id`` 都要重新 IO（在同步上下文里读过期属性会直接抛 MissingGreenlet）
    task_id = int(task.id)
    from app.services import collection_service

    try:
        root_id, root_created = await ensure_collection(
            db,
            DOUYIN_ROOT_COLLECTION_NAME,
            auto_source=DOUYIN_AUTO_SOURCE,
            description="由「获取我的收藏」自动聚合：二级收藏夹与抖音侧的收藏夹一一对应",
        )

        # 二级：逐个抖音收藏夹归位（空夹不建——里头的作品可能还没入库）
        folder_stats: list[dict] = []
        attributed: set[str] = set()
        for folder_name in sorted(folder_map):
            ids = await collect_library_ids_by_aweme(db, folder_map[folder_name])
            if not ids:
                continue
            child_id, child_created = await ensure_collection(
                db,
                folder_name,
                parent_id=root_id,
                auto_source=DOUYIN_AUTO_SOURCE,
                description=f"抖音收藏夹「{folder_name}」（随「获取我的收藏」自动同步）",
            )
            child_added = await collection_service.add_inspirations(db, child_id, ids)
            attributed.update(ids)
            folder_stats.append(
                {
                    "id": child_id,
                    "name": folder_name,
                    "created": child_created,
                    "added": int(child_added.get("added") or 0),
                    "skipped": int(child_added.get("skipped") or 0),
                    # 该夹里「已在库、本次没重新入库但补进来」的条数（与一级同口径，
                    # 便于解释「本夹数量 > 本批新入库」）
                    "from_existing": sum(1 for i in ids if i not in imported_set),
                }
            )

        # 没有归属的作品（平铺收藏 / 清单缺失）落二级「未分类收藏」——
        # **一级永远不装素材**（用户口径：一级只允许是分类节点）。早先把它们塞进
        # 一级，结果就是「一级又是 2658 件混在一起」。
        unclassified_ids = [insp_id for insp_id in target_ids if insp_id not in attributed]
        unclassified_stats: dict | None = None
        if unclassified_ids:
            unc_id, unc_created = await ensure_collection(
                db,
                UNCLASSIFIED_COLLECTION_NAME,
                parent_id=root_id,
                auto_source=DOUYIN_AUTO_SOURCE,
                description="没有收藏夹归属的收藏作品（平铺下载/清单缺失时才会出现）",
            )
            unc_added = await collection_service.add_inspirations(db, unc_id, unclassified_ids)
            unclassified_stats = {
                "id": unc_id,
                "name": UNCLASSIFIED_COLLECTION_NAME,
                "created": unc_created,
                "added": int(unc_added.get("added") or 0),
                "skipped": int(unc_added.get("skipped") or 0),
                "from_existing": sum(1 for i in unclassified_ids if i not in imported_set),
            }
            folder_stats.append(unclassified_stats)

        stats = {
            "id": root_id,
            "name": DOUYIN_ROOT_COLLECTION_NAME,
            "created": root_created,
            # 一级恒为 0：素材只能进二级收藏夹（分类节点不装素材）
            "added": 0,
            "skipped": 0,
            # 二级收藏夹（抖音收藏夹维度 + 可能的「未分类收藏」）
            "folders": folder_stats,
            "folder_count": len(folder_stats),
            "attributed": len(attributed),
            # 没有归属、落进「未分类收藏」的条数
            "unclassified": len(unclassified_ids),
            "unclassified_collection": unclassified_stats,
        }
        task.result = {**task.result, "collection": stats}
        await db.commit()
        logger.info(
            f"f2「我的收藏」已归位到「{DOUYIN_ROOT_COLLECTION_NAME}」#{root_id}："
            f"二级收藏夹 {len(folder_stats)} 个（按夹归位 {len(attributed)} 条，"
            f"未分类 {len(unclassified_ids)} 条）；一级不装素材"
        )
        return stats
    except Exception as exc:  # noqa: BLE001 —— 素材已入库，聚合失败不该让任务失败
        await db.rollback()
        # rollback 之后原 task 实例已过期：重新取一份再写提示（见上面的 task_id 注释）
        fresh = await db.get(TaskQueue, task_id)
        if fresh is not None:
            _append_notice(fresh, f"收藏夹归位失败（素材已入库）：{_brief_reason(exc)}")
            fresh.updated_at = utcnow()
            await db.commit()
        logger.warning(f"f2 收藏合集归位失败（素材已入库，任务仍成功）：{exc}")
        return None


async def _finalize_import(db: AsyncSession, task: TaskQueue, result: dict, holder: dict,
                 bloggers_stats: dict | None, interrupted: bool,
                 status_now: str) -> None:
    """收尾：写终态与结果（被中断时尊重外部状态，不覆盖为 success）。"""

    task.result = {
        **task.result,
        "stage": "done",
        "import": {key: value for key, value in result.items() if key != "ids"},
    }
    if bloggers_stats is not None:
        task.result["bloggers"] = bloggers_stats
    # 被中断的任务进度停在当前值：写 100% 会让「已取消/已暂停」看起来像跑完了
    if not interrupted:
        task.progress = 100
    task.done = holder["done"]
    task.total = holder["total"]
    task.updated_at = utcnow()
    if result.get("batch_error"):
        # 清单缺失 = 本批无法回滚。任务仍算成功（素材确实已入库），但这条必须让
        # 用户看得见——所以进 result.notices 而不是 task.error（后者会被 worker 清掉）
        _append_notice(task, "批次清单写入失败：本批无法回滚")
    if interrupted:
        # 尊重外部状态：worker 见 status != running 不会覆盖为 success
        task.status = status_now
        logger.info(
            f"f2 导入被中断（{status_now}）：已入库 {result['imported']}，"
            f"批次清单 {result['batch_file']}"
        )
    await db.commit()
    logger.info(f"f2 导入完成：入库 {result['imported']}，失败 {result['failed']}")
