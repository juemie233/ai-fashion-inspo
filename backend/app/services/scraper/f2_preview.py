"""f2 入库前预览：算「新增多少 / 跳过多少（含原因）」而**不动素材库**。

背景（用户偏好「宁缺毋滥、手动确认」）：CLI 默认只出报表、加 `--apply` 才入库，
而 Web 端点一下就直接建任务，用户看不到「这一批会新增多少、跳过了什么」。
本模块把同一条「扫描 → 五层判重」链路跑一遍，只回统计，不写入。

与执行阶段的一致性：
- 扫描根目录按 ``fetch_mode`` 取同一组常量（点赞 / 收藏 / 发布）；
- 作者口径与 ``execute_f2_import`` 一致：「我的列表」不做作者白名单（天然跨作者），
  发布模式默认只认**已登记博主**，除非显式包含未登记账号；
- 判重直接复用 ``build_import_plan``（入库用的就是它的结果），因此**预览数字与
  实际入库一致**。

性能：判重走 `build_plan_with_cache`（与后台任务同一份哈希缓存）。实测 15,350 文件
首次约 81 秒，之后文件没变时 1~2 秒——所以界面上可以放心多点几次预览。

**这是 GET 接口唯一的写副作用**：刷新哈希缓存（`storage/f2_hash_cache.db`）。
缓存只存「路径+size+mtime → sha256」，不含任何业务语义，写它不会改变导入结果；
之所以接受：不写缓存的话每次预览都要重算 15k 文件的哈希（81 秒），按钮将不可用。

已知边界（必须让调用方知道）：预览的是**下载目录里当前已有的产物**。任务参数带
`fetch=true` 时会先增量下载，新下下来的作品不在预览里——所以实际入库可能更多，
预览是「下限」而不是承诺值。
"""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _scan_root(f2, fetch_mode: str) -> Path:
    """本次要扫描的产物根目录（与执行阶段同一口径）。"""
    if fetch_mode == "collection":
        return f2.DEFAULT_F2_COLLECT_ROOT
    if fetch_mode == "like":
        return f2.DEFAULT_F2_LIKE_ROOT
    return f2.DEFAULT_F2_ROOT


def _resolve_authors(f2, bloggers: dict, explicit: list[str], include_unknown: bool) -> set[str] | None:
    """解析「本次要计哪些作者」的产物（None = 不过滤）。

    与 ``execute_f2_import`` 的白名单口径一致：库内一个抖音博主都没有时没有依据，
    退回「全部」；显式点名作者时以点名为准（并集）。
    """
    scope = {a.strip() for a in explicit if a.strip()}
    if include_unknown or not bloggers:
        return scope or None
    try:
        authors = f2.load_f2_authors(f2.DEFAULT_F2_DIR)
    except Exception as exc:  # noqa: BLE001 —— 读不到 f2 用户库就退化为「不限作者」
        # 预览是只读的辅助功能：f2 用户库损坏/缺失时给个全量估算，好过整个接口 500
        logger.warning(f"读取 f2 用户库失败，预览退化为不限作者: {exc}")
        return scope or None
    known, _unknown = f2.select_known_authors(authors, bloggers)
    known_keys = {f2.normalize_author(a["nickname"]) for a in known}
    return (known_keys | scope) if scope else known_keys


def preview_import_sync(
    *,
    authors: list[str] | None = None,
    limit: int | None = None,
    skip_live: bool = False,
    include_unknown_authors: bool = False,
    fetch_mode: str = "post",
    root: Path | None = None,
    db_path: Path | None = None,
) -> dict:
    """同步预览（CPU/磁盘密集，调用方应放线程池）。

    Args:
        authors: 只看这些作者（归一化名；空 = 按白名单口径）。
        limit: 最多算多少个作品（与入库参数一致）。
        skip_live: 跳过 live 实况分段。
        include_unknown_authors: 是否连「未登记博主」的账号一起算。
        fetch_mode: ``post`` / ``like`` / ``collection``（决定扫描根目录）。
        root: 覆盖扫描根（测试用；缺省按 fetch_mode 取配置）。
        db_path: 覆盖素材库路径（测试用；缺省与入库一致）。

    Returns:
        ``{"root", "files", "files_to_import", "works", "skipped", "deferred_works",
        "authors_scope", "has_any"}``；``skipped`` 是 ``{原因: 数量}``。
    """
    from scripts import import_f2_downloads as f2

    scan_root = root or _scan_root(f2, fetch_mode)
    files = f2.scan_directory(scan_root)
    empty = {
        "root": str(scan_root),
        "files": 0,
        "files_to_import": 0,
        "works": 0,
        "skipped": {},
        "deferred_works": 0,
        "authors_scope": None,
        "has_any": False,
    }
    if not files:
        return empty

    db = db_path or f2.library_db_path()
    bloggers = f2.load_douyin_bloggers(db)
    dedup = f2.load_dedup_index(db)
    # 「我的列表」天然跨作者，不做白名单（与执行阶段一致）
    plan_authors = (
        None
        if fetch_mode in ("like", "collection")
        else _resolve_authors(f2, bloggers, list(authors or []), include_unknown_authors)
    )

    # build_plan_with_cache 在本线程内建缓存并落盘：15,350 文件首次约 80 秒，
    # 之后（文件没变）1~2 秒 —— 界面上的「预览」按钮才敢多按几次。
    decisions, skipped, deferred_works, cache_stats = f2.build_plan_with_cache(
        files,
        dedup,
        bloggers=bloggers,
        authors=plan_authors,
        limit=limit,
        skip_live=skip_live,
    )
    to_import = [d for d in decisions if d.action == "import"]
    return {
        "root": str(scan_root),
        "files": len(files),
        "files_to_import": len(to_import),
        "works": len({d.item.work_key for d in to_import}),
        "skipped": dict(skipped),
        "deferred_works": deferred_works,
        "authors_scope": sorted(plan_authors) if plan_authors else None,
        "has_any": True,
        "hash_cache": cache_stats,
    }


async def preview_f2_import(
    *,
    authors: list[str] | None = None,
    limit: int | None = None,
    skip_live: bool = False,
    include_unknown_authors: bool = False,
    fetch_mode: str = "post",
) -> dict:
    """异步入口：把 :func:`preview_import_sync` 放线程池（几千张的哈希实算会阻塞）。"""
    return await asyncio.to_thread(
        preview_import_sync,
        authors=authors,
        limit=limit,
        skip_live=skip_live,
        include_unknown_authors=include_unknown_authors,
        fetch_mode=fetch_mode,
    )
