"""f2「我的列表」助手：点赞 / 收藏两种模式共用的常量与工具（切片 B）。

从 `task_runners/f2_import.py` 拆出的第二片。这里只放**不看任务状态、不写库流程**的部分：
两种「我的列表」模式常量、收藏夹归属清单的读取、按（名字 + 父）幂等建合集，以及
「任务成功但有事要告知用户」的提示追加与短原因格式化。执行阶段（扫描 / 入库）仍在
``f2_import.py``。

为什么「点赞」与「收藏」共处一个模块：两者同形——都是 f2 拉**登录账号自己**的列表
（只有本人可见，故必须填「我的主页链接」）、作品全下在「我的昵称」目录下、原作者与
作品 ID 只存在于文件名里；因此共用同一套模式常量、进度口径与扫描/入库逻辑，差别只有
f2 的 ``-M`` 取值、产物根目录与界面文案（文案由 ``fetch_mode`` 决定，见
web/src/utils/taskPresentation.ts）。

对外公共入口仍是 `task_runners/f2_import.py`（原样再导出），既有调用方与测试无需改动。
"""

import logging
from pathlib import Path
from types import ModuleType

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collection import (
    DOUYIN_AUTO_SOURCE as COLLECTION_DOUYIN_AUTO_SOURCE,
    DOUYIN_ROOT_COLLECTION_NAME as COLLECTION_DOUYIN_ROOT_NAME,
    Collection,
)
from app.models.task import TaskQueue

logger = logging.getLogger(__name__)


"""「我的列表」类模式：点赞（我的喜欢）与收藏（我的收藏）。

两者同形——都是 f2 拉**登录账号自己**的列表（只有本人可见，故必须填「我的主页链接」）、
作品全下在「我的昵称」目录下、原作者与作品 ID 只存在于文件名里；因此共用同一套下载阶段、
进度口径（``download_progress``）与扫描/入库逻辑，差别只有 f2 的 ``-M`` 取值、产物根目录
与界面文案（文案由 ``fetch_mode`` 决定，见 web/src/utils/taskPresentation.ts）。
"""
PERSONAL_FETCH_MODES = ("like", "collection")

"""收藏模式的一级收藏夹：抖音侧自动收藏的总入口（二级是各个抖音收藏夹）。

名字与来源标记的唯一口径在 ``app.models.collection``（迁移里也建同名节点）。
"""
DOUYIN_ROOT_COLLECTION_NAME = COLLECTION_DOUYIN_ROOT_NAME

"""一级自动收藏夹的维护来源标记（自动合集禁止改名，可删除后由同步重建）。"""
DOUYIN_AUTO_SOURCE = COLLECTION_DOUYIN_AUTO_SOURCE

"""收藏夹归属清单文件名（与 scripts/f2_collects.py 的常量保持一致）。"""
_COLLECT_FOLDER_MAP_NAME = "_collect_folders.json"

"""没有收藏夹归属的作品落在哪个二级收藏夹。

一级「抖音入库自动收藏」是**纯分类节点，永远不装素材**（用户口径：一级不允许有素材
加进来）。所以「平铺收藏」这类没有归属信息的作品需要一个二级去处，取名对齐抖音侧
的「未分类」。正常按夹下载时这个夹不会出现。
"""
UNCLASSIFIED_COLLECTION_NAME = "未分类收藏"


def personal_registers_bloggers(fetch_mode: str, requested: bool) -> bool:
    """本次「我的列表」导入是否补建来源作者博主（纯函数，唯一口径）。

    **收藏模式永远不补建**（2026-10-01 用户口径）：收藏是个「先收起来」的池子，天然跨主题
    （实测夹里有股票/哲学/公考/游戏），自动建号只会在库里堆出与穿搭无关的空壳——实测
    451 位自动登记博主里只有 1% 名字与穿搭相关，用户还得手工清理一遍。**「我的喜欢」保留**
    自动登记：那是主要素材来源，素材需要归属人。

    收藏批次若确实要归属人，仍可在「抖音采集历史」的结果面板手工点「登记博主」补
    （见 ``POST /api/scraper/f2-tasks/{id}/results/register-bloggers``）。

    Args:
        fetch_mode: ``post`` / ``like`` / ``collection``。
        requested: 调用方是否要求登记（接口参数，缺省 True）。

    Returns:
        归一化后的开关；建任务与执行都用它，保证任务记录与实际行为一致。
        **只有 like 可能为 True**：发布模式那条链路的作者本来就受白名单约束（不建号，
        两条链路口径不同不混用），收藏恒不补建（见上）。
    """
    return fetch_mode == "like" and bool(requested)


def _personal_scan_root(f2: ModuleType, fetch_mode: str) -> Path:
    """「我的列表」模式的产物根目录（点赞 / 收藏）。"""
    return (
        f2.DEFAULT_F2_COLLECT_ROOT
        if fetch_mode == "collection"
        else f2.DEFAULT_F2_LIKE_ROOT
    )


def load_collect_folder_entries(root: str | Path) -> dict[str, dict]:
    """读「收藏夹 ID → {name, 作品 ID}」清单（原始结构，供按夹限定入库范围用）。

    清单由按夹下载时写出（见 ``scripts/f2_collects.py``）：文件平铺在昵称目录下，
    路径里不带收藏夹名，只有这份清单能回答「这件作品属于哪个夹」。

    多个昵称目录（换过账号/改过昵称）的清单会合并；同一夹 ID 取并集。清单缺失
    （平铺 ``-M collection`` 下载、旧批次）时返回空字典。

    Args:
        root: 收藏产物根目录（…/douyin/collection）。

    Returns:
        ``{收藏夹 ID: {"name": str, "aweme_ids": [作品 ID, ...]}}``。
    """
    import json

    base = Path(root)
    if not base.is_dir():
        return {}
    # 清单落在「昵称」目录下：既认根目录直下，也认一层子目录（f2 的产物结构）
    candidates = [base / _COLLECT_FOLDER_MAP_NAME]
    candidates += [
        child / _COLLECT_FOLDER_MAP_NAME for child in sorted(base.iterdir()) if child.is_dir()
    ]
    merged: dict[str, dict] = {}
    for path in candidates:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError) as exc:
            logger.warning(f"收藏夹清单读取失败（忽略该文件）：{path} -> {exc}")
            continue
        for folder_id, entry in (payload.get("folders") or {}).items():
            ids = {str(a) for a in (entry.get("aweme_ids") or []) if a}
            if not ids:
                continue
            prev = merged.setdefault(str(folder_id), {"name": "", "aweme_ids": set()})
            prev["name"] = prev["name"] or str(entry.get("name") or "").strip()
            prev["aweme_ids"] |= ids
    return {
        fid: {"name": entry["name"], "aweme_ids": sorted(entry["aweme_ids"])}
        for fid, entry in merged.items()
    }


def load_collect_folder_map(root: str | Path) -> dict[str, list[str]]:
    """读「作品 ID → 抖音收藏夹」清单，返回 ``{收藏夹名: [作品 ID, ...]}``（按名聚合）。

    建二级收藏夹用这个视图（二级的名字就是收藏夹名）；限定入库范围请用
    :func:`load_collect_folder_entries`（那份按夹 ID 索引，勾选状态是夹 ID）。

    Args:
        root: 收藏产物根目录（…/douyin/collection）。

    Returns:
        收藏夹名 → 作品 ID 列表；清单缺失时为空字典。
    """
    merged: dict[str, set[str]] = {}
    for entry in load_collect_folder_entries(root).values():
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        merged.setdefault(name, set()).update(entry["aweme_ids"])
    return {name: sorted(ids) for name, ids in merged.items() if ids}


async def ensure_collection(
    db: AsyncSession,
    name: str,
    parent_id: int | None = None,
    auto_source: str | None = None,
    description: str | None = None,
) -> tuple[int, bool]:
    """按（名字 + 父）取合集，不存在则建；返回 ``(id, 是否新建)``。

    幂等：同一批/多批同步反复调用只会建一次。并发下靠唯一索引兜底——两个任务同时
    建同一个夹时，插入会撞 `UNIQUE constraint failed: collections.name`（**抛的是
    IntegrityError，不是 HTTPException**，实测），这里回退成「查询已有」而不是让
    整个任务失败。
    """
    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError

    from app.services import collection_service

    query = select(Collection).where(Collection.name == name)
    query = (
        query.where(Collection.parent_id.is_(None))
        if parent_id is None
        else query.where(Collection.parent_id == parent_id)
    )
    existing = (await db.execute(query)).scalars().first()
    if existing is not None:
        return int(existing.id), False
    try:
        data = await collection_service.create_collection(
            db,
            name=name,
            description=description,
            query_json=None,
            parent_id=parent_id,
            auto_source=auto_source,
        )
    except IntegrityError:
        # 竞态：SELECT 到 INSERT 之间被别人建出来了（唯一索引是最终裁决者）
        await db.rollback()
        again = (await db.execute(query)).scalars().first()
        if again is None:
            raise
        return int(again.id), False
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        await db.rollback()
        again = (await db.execute(query)).scalars().first()
        if again is None:
            raise
        return int(again.id), False
    return int(data["id"]), True


def _personal_label(fetch_mode: str) -> str:
    """「我的列表」模式的中文名（日志与错误提示用）。"""
    return "我的收藏" if fetch_mode == "collection" else "我的喜欢"


def _append_notice(task: TaskQueue, message: str) -> None:
    """把「任务成功、但有事要说」的提示追加到 ``task.result["notices"]``。

    为什么不写 ``task.error``：worker 在任务正常返回时会把它清成 None
    （``app/worker.py`` 成功分支 ``task.error = None``），写在那里等于没写——
    实测过：任务终态是 success、error 为 None、用户什么都看不到。
    这里的提示由前端 ``summarizeResult`` 渲染，所以**保持一句话以内**。
    """
    notices = [str(n) for n in ((task.result or {}).get("notices") or [])]
    if message not in notices:
        notices.append(message)
    task.result = {**(task.result or {}), "notices": notices}


def _brief_reason(exc: BaseException, limit: int = 60) -> str:
    """把异常压成一句能给用户看的原因（HTTPException 取 detail，其余取首行）。"""
    from fastapi import HTTPException

    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
    text = " ".join(str(detail).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
