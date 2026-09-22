"""采集引擎管理的 REST API 路由。"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.scraper import ScraperSchedule
from app.schemas.scraper import (
    ScraperScheduleCreate,
    ScraperScheduleOut,
    ScraperScheduleUpdate,
    ScraperTaskCreate,
    ScraperTaskOut,
)
from app.services import scraper_service
from app.services.chrome_manager import chrome_manager

router = APIRouter(prefix="/api/scraper", tags=["scraper"])


@router.get("/sources")
async def scraper_sources() -> dict:
    """列出所有可用的采集源及其状态。"""
    return await scraper_service.get_scraper_sources()


@router.post("/f2-import")
async def create_f2_import(
    fetch: bool = Query(True, description="是否先调 f2 增量下载（否则只入库已下载文件）"),
    authors: str | None = Query(None, description="只处理这些作者（逗号分隔，归一化名）"),
    limit: int | None = Query(None, ge=1, description="最多导入多少个作品"),
    skip_live: bool = Query(False, description="跳过 live 实况的分段视频"),
    fetch_limit: int | None = Query(None, ge=1, description="下载阶段最多处理多少个作者"),
    make_thumbnails: bool = Query(True, description="是否生成缩略图"),
    since_days: int | None = Query(
        None, ge=0, le=3650, description="f2 日期窗口天数（0=全历史；缺省取配置）"
    ),
    include_unknown_authors: bool = Query(
        False, description="是否连未登记到博主库的 f2 账号一起处理（默认跳过）"
    ),
    mode: str = Query(
        "post",
        pattern="^(post|like|collection)$",
        description=(
            "post=博主主页作品（默认）；like=我的喜欢（点赞）；"
            "collection=我的收藏（抖音收藏列表，入库后自动聚合进「抖音收藏」合集）。"
            "后两者都需要 like_user"
        ),
    ),
    like_user: str | None = Query(
        None,
        description=(
            "mode=like/collection 时用我的主页链接 / sec_user_id"
            "（缺省取已保存的配置；点赞与收藏列表都只有本人可见）"
        ),
    ),
    register_bloggers: bool = Query(
        True,
        description=(
            "mode=like/collection 时把未登记的来源作者补建成抖音博主并绑定素材（默认开）"
        ),
    ),
    like_max_counts: int | None = Query(
        None,
        ge=0,
        le=100000,
        description=(
            "mode=like/collection 时最多翻多少条（0/缺省=全量翻到底）。"
            "**按收藏夹下载（带 collect_ids）时是「每个夹」的上限**：每个夹各取最近 N 件"
        ),
    ),
    profiles: str | None = Query(
        None,
        description=(
            "按博主全量下载：博主主页链接或 sec_user_id（逗号/空格分隔）。"
            "非空时只下这些博主，且不需要它们已在 f2 用户库里；首次采集自动翻全量"
        ),
    ),
    collect_ids: str | None = Query(
        None,
        description=(
            "mode=collection 时**只下这些收藏夹**（夹 ID，逗号分隔；来自 GET /f2-collects）。"
            "缺省/空 = 老口径：下平铺收藏列表（含所有收藏夹）"
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """一键获取素材：调 f2 增量下载抖音作品 → 去重 → 入库。

    与 CLI（``python -m scripts.import_f2_downloads --fetch --apply``）同一条链路，
    区别是走任务队列：创建任务后立即返回 task_id，由独立 worker 异步执行，
    前端轮询 ``GET /api/tasks/{task_id}`` 获取进度。

    两条约定：**导入不做标签分析/不建向量**（素材以未打标状态入库，打标请另行
    触发批量分析任务）；**入库前五层去重**（内容哈希 / 垃圾桶 / 批次内 /
    合成平台 ID / 参数过滤）。

    参数:
        fetch: 是否先调 f2 下载（需要本机已装 f2 且其用户库里有作者）。
        authors: 只处理这些作者（与下载、导入阶段共用同一归一化口径）。
        limit: 最多导入多少个作品（按作品计，一个图集只吃一个配额）。
        skip_live: 跳过 live 实况的分段视频。
        fetch_limit: 下载阶段最多处理多少个作者（试跑用）。
        make_thumbnails: 是否生成缩略图（关掉更快，但列表页缺预览图）。
        include_unknown_authors: 是否连「未登记到博主库」的 f2 账号一起处理。
            默认 False——f2 用户库存的是它见过的所有账号，实测混进过网易第五人格
            这类官方号并被原样入库；默认只处理能对上游记博主的账号。
        since_days: f2 只翻最近 N 天的作品。**别轻易用 0**：`-i all` 会让 f2 把
            作者全部历史翻完且每页固定等 timeout 秒（实测单作者 84% 的时间花在
            翻页等待上）；窗口会按「该作者上次下载时间」自动放大，长时间不跑
            也不会漏作品。
        mode: `post`=博主主页作品（默认）；`like`=我的喜欢（点赞）；
            `collection`=我的收藏（抖音收藏列表）。
            后两者（「我的列表」）都不逐作者、不给时间窗口，也不做作者白名单
            （点赞/收藏的作品天然跨作者），入库仍走五层判重；collection 模式在入库
            之后会把本批素材聚合进「抖音收藏」合集（收藏合计里直接看到数量与体积）。
            注：**f2 的点赞/收藏模式根本不读 `-i`**（源码实测），所以这里传不传日期
            窗口都一样——能收窄翻页量的只有 `like_max_counts`。
        like_user: mode=like/collection 时的「我的主页链接 / sec_user_id」
            （缺省取已保存配置；两种列表都只有本人可见）。
        register_bloggers: mode=like/collection 时，入库后是否把**未登记的来源作者**
            补建成抖音博主并绑定本批素材（默认开）。补建的博主标记为「自动登记」，
            不算已登记博主、不进「一键获取素材」的下载白名单；在博主列表点「纳入追踪」才进。
        like_max_counts: mode=like/collection 时最多翻多少条（0/缺省=全量翻到底）。
            列表最新在前，填 100~200 可把日常增量降到一两页；代价是两次运行之间
            新增超过该值时会漏。
        profiles: **按博主全量下载**——博主主页链接或 sec_user_id（逗号/空格分隔）。
            用途：给一个博主，下她**全部**作品。非空时只下这些博主，且**不需要它们
            已在 f2 用户库里**（f2 的下载目标只来自它自己的用户库，库里没有的账号
            跑不到，这正是那个限制的出口）；首次采集自动用 `-i all` 翻全量，
            入库范围就是这些博主的产物。抖音号与 v.douyin.com 短链不支持。
    """
    from app.services.task_runner import create_f2_import_task_if_idle, f2_import_status

    if fetch:
        status = f2_import_status()
        # 三个入口的可用性口径不同，别互相借用：发布模式可用性依赖「已登记博主」
        # 白名单（f2 用户库混进的无关账号默认跳过）；点赞/收藏只要求 f2 + 工作目录 +
        # 已配置我的主页链接（这些列表天然跨作者）。用发布模式的口径去挡它们，
        # 用户会被一个与点赞/收藏无关的理由（「没有一个账号对应到已登记的抖音博主」）拒绝。
        personal = mode in ("like", "collection")
        ready = status["like_available"] if personal else status["available"]
        if not ready:
            if mode == "collection":
                reason = status.get("collect_reason") or status.get("reason", "")
            elif mode == "like":
                reason = status["like_reason"]
            else:
                reason = status["reason"]
            return {"message": reason, "task_id": None}

    author_list = [a.strip() for a in (authors or "").split(",") if a.strip()]
    profile_list = [
        p.strip() for p in (profiles or "").replace(",", " ").split() if p.strip()
    ]
    collect_id_list = [c.strip() for c in (collect_ids or "").split(",") if c.strip()]

    # 并发保护：同一时刻只允许一个「一键获取素材」任务（连点会起多个任务 →
    # f2 子进程并发下载、同一平台 ID 撞唯一索引堆失败）。「查进行中 + 创建」
    # 在服务层由进程内锁串行化（自动调度循环也走同一入口），已有进行中任务时
    # 直接复用它，不新建。
    task, running_id = await create_f2_import_task_if_idle(
        db,
        authors=author_list,
        limit=limit,
        skip_live=skip_live,
        fetch=fetch,
        fetch_limit=fetch_limit,
        make_thumbnails=make_thumbnails,
        since_days=since_days,
        include_unknown_authors=include_unknown_authors,
        fetch_mode=mode,
        like_user=(like_user or "").strip() or None,
        register_bloggers=register_bloggers,
        like_max_counts=like_max_counts,
        profiles=profile_list,
        collect_ids=collect_id_list,
    )
    if task is None:
        return {
            "message": f"已有进行中的一键获取素材任务（#{running_id}），请等待完成或先取消",
            "task_id": running_id,
            "reused": True,
        }
    return {
        "message": "已提交「一键获取素材」任务",
        "task_id": task.id,
        "fetch": fetch,
        "authors": author_list,
    }


@router.get("/f2-status")
async def f2_status(db: AsyncSession = Depends(get_db)) -> dict:
    """「一键获取素材」状态：可用性 + 每日自动获取的配置与到期信息。

    供采集管理页的按钮置灰、开关与「上次/下次运行」提示使用。
    """
    from app.services.task_runner import f2_import_status, get_f2_auto_status

    info = f2_import_status()
    info["auto"] = await get_f2_auto_status(db)
    return info


@router.get("/f2-collects")
async def f2_collects() -> dict:
    """**先扫描**：列出抖音收藏夹（夹名 / 夹 ID / 夹内作品数），只读、不下载任何媒体。

    为什么要先扫描：平铺的「我的收藏」会把收藏夹里的作品一并下下来（实测 31 个夹、
    约 2000 件，里面混着「股票 / 哲学 / 历史」这类明显不想要的），用户在下载前需要
    看到清单并**勾掉不想要的夹**，只下勾选的。

    实现：走 f2 的收藏夹接口（`collects/list/`）只取元数据；Cookie 从 f2 配置整份
    YAML 解析（不是截 `cookie:` 那一行——那样只有 172 字符的残缺登录态，接口会返回
    「200 但内容为空」）。同步返回：收藏夹清单通常 1~2 页，2~5 秒。
    """
    from scripts import import_f2_downloads as f2

    try:
        data = await asyncio.to_thread(f2.list_collect_folders)
    except Exception as exc:  # noqa: BLE001 —— 风控/Cookie 失效都要给用户可读原因
        raise HTTPException(
            status_code=400,
            detail=(
                f"读取收藏夹失败：{exc}。"
                "常见原因：f2 配置里的 Cookie 失效（请手动跑一次 f2 重新登录）"
                "或接口风控（稍后重试）"
            ),
        ) from exc
    return data


@router.get("/f2-authors")
async def f2_authors(db: AsyncSession = Depends(get_db)) -> dict:
    """列出 f2 用户库里的账号，并标出哪些在「一键获取素材」的下载白名单里。

    为什么需要：卡片只报一句「可增量下载 19 个已登记博主」，用户看不到这 19 个
    到底是谁、各自已经带来多少素材，也看不到 f2 库里还有哪些账号会被跳过。
    判定口径与执行侧共用（见 services/scraper/f2_authors.py）。
    """
    return await scraper_service.get_f2_authors(db)


@router.get("/f2-tasks/{task_id}/results")
async def f2_task_results(
    task_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(60, ge=1, le=200),
    state: str = Query(
        "all", description="筛选：all/pending/approved/rejected/trash/gone"
    ),
    author: str = Query("", description="只保留该作者（source_author 精确匹配）"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """浏览某次「一键获取素材」任务产出的素材（供采集管理页的结果与审查面板）。

    归属以该任务落盘的**批次清单**为准（f2 素材没有 scraper_task_id 可关联）；
    每条素材的当前状态（是否在垃圾桶、质量审核状态、实际文件路径）以数据库为准。
    """
    return await scraper_service.get_f2_task_results(
        db, task_id, page=page, size=size, state=state, author=author
    )


@router.post("/f2-tasks/{task_id}/results/trash")
async def f2_task_results_trash(
    task_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """把本批素材移入垃圾桶（软删除，可在垃圾桶恢复；同时作为负样本）。

    请求体: {"ids": [...], "reason": "质量差"}
    """
    return await scraper_service.trash_f2_task_results(
        db, task_id, payload.get("ids") or [], payload.get("reason")
    )


@router.post("/f2-tasks/{task_id}/results/restore")
async def f2_task_results_restore(
    task_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """还原本批已在垃圾桶的素材（逐条复用单条恢复逻辑，单条失败不影响其余）。

    请求体: {"ids": [...]}
    """
    return await scraper_service.restore_f2_task_results(db, task_id, payload.get("ids") or [])


@router.post("/f2-tasks/{task_id}/results/delete")
async def f2_task_results_delete(
    task_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """彻底删除本批素材（不可恢复）：创建 batch_delete 任务，由 worker 执行。

    请求体: {"ids": [...]}
    """
    return await scraper_service.delete_f2_task_results(db, task_id, payload.get("ids") or [])


@router.post("/f2-tasks/{task_id}/results/register-bloggers")
async def f2_task_results_register_bloggers(
    task_id: int, db: AsyncSession = Depends(get_db)
) -> dict:
    """把本批未绑定博主的来源作者补登记为抖音博主并绑定素材（幂等，可重复点）。

    自动路径在「我的喜欢」入库后（创建任务时的 ``register_bloggers``）；这个入口
    用于手工回填：老批次、或建任务时关掉了自动登记。补建的博主标记为「自动登记」，
    不进「一键获取素材」的下载白名单。
    """
    return await scraper_service.register_f2_task_bloggers(db, task_id)


@router.put("/f2-auto")
async def set_f2_auto(
    enabled: bool = Query(..., description="是否开启每日自动增量入库"),
    interval_hours: int | None = Query(None, ge=1, le=720, description="最小间隔（小时）"),
    skip_live: bool | None = Query(None, description="自动获取是否跳过 live 实况分段"),
    mode: str | None = Query(
        None,
        pattern="^(post|like|collection)$",
        description=(
            "自动获取走哪个入口：post=已登记博主主页作品（默认）；"
            "like=我的喜欢；collection=我的收藏（入库后聚合进「抖音收藏」合集）。"
            "后两者需要先配置「我的主页链接」"
        ),
    ),
    persist: bool = Query(True, description="是否持久化写入 .env 文件"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """开关 f2 每日自动获取素材（f2 增量下载 → 去重 → 入库）。

    与手动入口同一条链路，区别是由后端调度循环按间隔自动创建任务；到期判定见
    `maybe_schedule_auto_import`（已有任务在跑或未到间隔则跳过）。
    ``mode`` 决定自动跑哪个入口（博主主页作品 / 我的喜欢 / 我的收藏）。
    配置写入 settings 并（默认）持久化到 .env，重启后保持。
    """
    from app.config import settings
    from app.routers.ai_shared import _update_env_file
    from app.services.task_runner import get_f2_auto_status

    settings.f2_import_auto_enabled = enabled
    if interval_hours is not None:
        settings.f2_import_interval_hours = interval_hours
    if skip_live is not None:
        settings.f2_import_auto_skip_live = skip_live
    if mode is not None:
        settings.f2_import_auto_mode = mode

    if persist:
        updates = {"F2_IMPORT_AUTO_ENABLED": "true" if enabled else "false"}
        if interval_hours is not None:
            updates["F2_IMPORT_INTERVAL_HOURS"] = str(interval_hours)
        if skip_live is not None:
            updates["F2_IMPORT_AUTO_SKIP_LIVE"] = "true" if skip_live else "false"
        if mode is not None:
            updates["F2_IMPORT_AUTO_MODE"] = mode
        await _update_env_file(updates)

    status = await get_f2_auto_status(db)
    mode_label = {
        "post": "博主主页作品",
        "like": "我的喜欢",
        "collection": "我的收藏",
    }.get(status["mode"], status["mode"])
    return {
        "message": f"每日自动获取素材已{'开启' if enabled else '关闭'}"
        f"（间隔 {status['interval_hours']} 小时，入口：{mode_label}）",
        "auto": status,
    }


@router.put("/f2-like-user")
async def set_f2_like_user(
    like_user: str = Query("", description="我的抖音主页链接或 sec_user_id（空=清除）"),
    persist: bool = Query(True, description="是否持久化写入 .env 文件"),
) -> dict:
    """保存「我的喜欢」用的主页链接。

    抖音的点赞列表只有本人可见，f2 的 `-M like` 要求 `-u` 填**你自己的**主页链接；
    这里接受完整链接（`https://www.douyin.com/user/…`）或纯 sec_user_id，
    归一成链接后写入 settings 与 .env（默认持久化），下次采集直接用。
    """
    from app.config import settings
    from app.routers.ai_shared import _update_env_file
    from scripts import import_f2_downloads as f2

    raw = (like_user or "").strip()
    if raw and (len(raw) < 4 or any(ch.isspace() for ch in raw)):
        raise HTTPException(
            status_code=400,
            detail="请填写抖音主页链接（https://www.douyin.com/user/…）或 sec_user_id（不含空格）",
        )

    normalized = f2.like_user_url(raw)
    settings.f2_like_user = normalized
    if persist:
        await _update_env_file({"F2_LIKE_USER": normalized})

    return {
        "message": (
            f"已保存「我的主页链接」：{normalized}" if normalized else "已清除「我的主页链接」"
        ),
        "like_user": normalized,
    }


@router.put("/f2-like-max-counts")
async def set_f2_like_max_counts(
    max_counts: int = Query(
        0, ge=0, le=100000, description="最多翻多少条点赞（0=全量翻到底）"
    ),
    persist: bool = Query(True, description="是否持久化写入 .env 文件"),
) -> dict:
    """保存「我的喜欢」每次最多翻多少条点赞作品。

    为什么需要：f2 的点赞分页**没有「遇到已下载就停」**——它从 `cursor=0` 一路翻到底，
    且每页固定 `asyncio.sleep(timeout)`（本机 10 秒）。点赞目录 919 个作品即 ≥46 页、
    ≥7.7 分钟纯等待，**每次运行都一样，哪怕零新增**；同时进度条按「已落盘文件数」
    计算，零新增时会全程停在 0，看起来像卡死。

    点赞列表最新在前，所以只翻最近 N 条即可覆盖新增（f2 的 `-o/--max-counts` 确实
    gate 住翻页循环）。代价：两次运行之间新增点赞超过 N 条会漏，故默认 0（全量，
    行为与改造前一致）。
    """
    from app.config import settings
    from app.routers.ai_shared import _update_env_file

    settings.f2_like_max_counts = max(0, int(max_counts or 0))
    if persist:
        await _update_env_file(
            {"F2_LIKE_MAX_COUNTS": str(settings.f2_like_max_counts)}
        )

    return {
        "message": (
            f"已保存：每次最多翻 {settings.f2_like_max_counts} 条点赞（增量模式）"
            if settings.f2_like_max_counts
            else "已保存：全量翻页到底"
        ),
        "like_max_counts": settings.f2_like_max_counts,
    }


@router.get("/hashtags")
async def scraper_hashtags(
    sort: str = Query("count", pattern="^(count|recent)$"),
    min_count: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """采集话题标签存档：详情页提取的 #话题 全局去重统计。

    供「话题 → 定时采集关键词」闭环：定时计划配置页按热度勾选话题
    写入计划 keywords。sort=count（按出现次数）/ recent（按最近出现）。
    """
    from sqlalchemy import select

    from app.models.person import Blogger
    from app.models.scraper import ScraperHashtag

    stmt = (
        select(
            ScraperHashtag.name,
            ScraperHashtag.seen_count,
            ScraperHashtag.last_seen_at,
            ScraperHashtag.source_kind,
            ScraperHashtag.source_id,
            Blogger.name.label("blogger_name"),
        )
        .outerjoin(Blogger, Blogger.id == ScraperHashtag.source_id)
        .where(ScraperHashtag.seen_count >= min_count)
    )
    if sort == "recent":
        stmt = stmt.order_by(
            ScraperHashtag.last_seen_at.desc(), ScraperHashtag.seen_count.desc()
        )
    else:
        stmt = stmt.order_by(
            ScraperHashtag.seen_count.desc(), ScraperHashtag.last_seen_at.desc()
        )
    stmt = stmt.limit(limit)
    rows = (await db.execute(stmt)).all()
    return {
        "items": [
            {
                "name": r[0],
                "seen_count": r[1],
                "last_seen_at": r[2].isoformat() if r[2] else None,
                "source_kind": r[3],
                "source_id": r[4],
                "blogger_name": r[5],
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.get("/stats")
async def scraper_stats(days: int = 30) -> dict:
    """采集任务统计看板：近 N 天的总量/成功率/按平台与按日分布。"""
    if days < 1 or days > 365:
        days = 30
    return await scraper_service.get_scraper_stats(days)


@router.get("/collection-roi")
async def collection_roi(
    days: int = Query(180, ge=1, le=3650, description="统计窗口（天）"),
    limit: int = Query(20, ge=1, le=200, description="每个维度最多返回多少行"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """采集 ROI 漏斗：按关键词 / 博主拆解「采集量 → 入库量 → 质量合格率」。

    两个维度的取数口径不同，都不猜、不摊派：CDP 采集走任务级 `items_found`/`items_added`
    并用素材的 `scraper_task_id` 归属合格数；f2 通道没有该关联，按素材的 `source_author`
    聚合。样本为 0 时合格率返回 `null`（前端显示「—」），具体边界见 `coverage.notes`。
    """
    return await scraper_service.get_collection_roi(db, days=days, limit=limit)


@router.get("/cdp-check/{port}")
async def check_cdp_endpoint(port: int) -> dict:
    """检查指定端口的 Chrome 调试连接是否就绪。"""
    return await scraper_service.check_cdp(port)


# ============ Chrome 生命周期管理 ============


@router.post("/chrome/start")
async def chrome_start() -> dict:
    """由后端拉起采集专用 Chrome（调试模式）。

    启动流程含子进程拉起与最长 chrome_startup_timeout 秒的就绪轮询
    （同步 sleep），放入线程池执行，避免阻塞整条事件循环。
    """
    return await asyncio.to_thread(chrome_manager.start)


@router.post("/chrome/stop")
async def chrome_stop() -> dict:
    """停止由后端拉起的采集专用 Chrome（含 taskkill 与等待，走线程池）。"""
    return await asyncio.to_thread(chrome_manager.stop)


@router.get("/chrome/status")
async def chrome_status() -> dict:
    """查询采集专用 Chrome 的连接状态（端口探测含 socket 超时，走线程池）。"""
    return await asyncio.to_thread(chrome_manager.status)


# ============ Cookie 管理 ============


@router.get("/cookie-status")
async def cookie_status(platform: str = "xiaohongshu") -> dict:
    """检查指定平台的 Cookie 文件状态。"""
    return await scraper_service.get_cookie_status(platform)


@router.post("/cookie-verify/{platform}")
async def cookie_verify(platform: str) -> dict:
    """真实校验平台 Cookie 登录态（携带 Cookie 请求平台轻量登录态接口）。

    强制探测（不走缓存）；无 Cookie 文件返回 no_file，网络/风控等
    不确定因素返回 unknown，只有确定性证据才判 invalid。
    """
    return await scraper_service.verify_platform_cookie(platform, force=True)


@router.post("/cookie-import")
async def cookie_import(payload: dict) -> dict:
    """导入平台 Cookie（JSON 格式，自动校验平台合法性）。"""
    return await scraper_service.import_cookies(payload)


@router.delete("/cookie/{platform}")
async def delete_cookie(platform: str) -> dict:
    """删除指定平台的 Cookie 文件。"""
    return await scraper_service.delete_cookies(platform)


# ============ 定时采集计划 ============


@router.get("/schedules", response_model=list[ScraperScheduleOut])
async def list_schedules(db: AsyncSession = Depends(get_db)) -> list[ScraperSchedule]:
    """列出全部定时采集计划。"""
    return await scraper_service.list_schedules(db)


@router.post("/schedules", response_model=ScraperScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(data: ScraperScheduleCreate, db: AsyncSession = Depends(get_db)) -> ScraperSchedule:
    """创建定时采集计划。"""
    return await scraper_service.create_schedule(db, data)


@router.patch("/schedules/{schedule_id}", response_model=ScraperScheduleOut)
async def update_schedule(schedule_id: int, data: ScraperScheduleUpdate, db: AsyncSession = Depends(get_db)) -> ScraperSchedule:
    """更新定时采集计划（启用/停用/改间隔/改关键词等）。"""
    return await scraper_service.update_schedule(db, schedule_id, data)


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """删除定时采集计划。"""
    return await scraper_service.delete_schedule(db, schedule_id)


@router.post("/schedules/{schedule_id}/run")
async def run_schedule_now(schedule_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """立即执行一次定时采集计划。"""
    return await scraper_service.run_schedule_now(db, schedule_id)


# ============ 浏览器插件任务记录 ============


@router.post("/extension-tasks", status_code=status.HTTP_201_CREATED)
async def create_extension_task(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    """浏览器插件采集会话开始：创建任务记录并返回 task_id。"""
    task = await scraper_service.create_extension_task(db, payload)
    return {"id": task.id}


@router.post("/extension-tasks/{task_id}/complete")
async def complete_extension_task(task_id: int, payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    """浏览器插件采集会话结束：汇总发现/入库数量并标记任务完成。"""
    return await scraper_service.complete_extension_task(db, task_id, payload)


# ============ 任务日志 ============


@router.get("/tasks/{task_id}/log")
async def task_log(task_id: int) -> dict:
    """获取采集任务的日志内容（最近 200 行）。"""
    return await scraper_service.get_task_log(task_id)


# ============ 任务取消 ============


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """取消运行中或等待中的采集任务（发送终止信号给子进程）。"""
    return await scraper_service.cancel_scraper_task(db, task_id)


# ============ 创建任务 ============


@router.post(
    "/tasks", response_model=ScraperTaskOut, status_code=status.HTTP_201_CREATED
)
async def create_scraper_task(
    data: ScraperTaskCreate,
    db: AsyncSession = Depends(get_db),
) -> ScraperTaskOut:
    """创建并启动一个新的采集任务。

    CDP 模式下会预先检测 Chrome 调试端口，不可用时返回明确的错误提示。
    """
    task = await scraper_service.create_scraper_task(db, data)
    return ScraperTaskOut.model_validate(task)


@router.get("/tasks")
async def list_scraper_tasks(
    platform: str | None = None,
    status: str | None = None,
    sort: str = "newest",  # newest | oldest | most_found | most_added
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取采集任务列表，支持筛选、排序与分页（返回 items + total + stats）。"""
    tasks, total, stats = await scraper_service.list_scraper_tasks(
        db, platform, status, sort, page, size
    )
    return {
        "items": [ScraperTaskOut.model_validate(t) for t in tasks],
        "total": total,
        "page": page,
        "size": size,
        "stats": stats,
    }


@router.delete("/tasks/{task_id}", status_code=status.HTTP_200_OK)
async def delete_single_task(task_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """物理删除单条采集任务（素材的 scraper_task_id 自动置 NULL，不删除素材）。"""
    return await scraper_service.delete_single_scraper_task(db, task_id)


@router.delete("/tasks", status_code=status.HTTP_200_OK)
async def clear_all_scraper_tasks(db: AsyncSession = Depends(get_db)) -> dict:
    """物理删除所有采集任务历史记录。"""
    return await scraper_service.clear_all_scraper_tasks(db)


@router.post("/tasks/retry-failed")
async def retry_failed_scraper_tasks(db: AsyncSession = Depends(get_db)) -> dict:
    """重试所有失败的采集任务，使用相同配置重新创建任务。"""
    return await scraper_service.retry_failed_scraper_tasks(db)


@router.post("/tasks/{task_id}/retry")
async def retry_single_task(task_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """重试单个失败任务，沿用断点续采（不重复采集已处理内容）。"""
    return await scraper_service.retry_single_task(db, task_id)


@router.get("/tasks/{task_id}/results")
async def task_results(
    task_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取指定采集任务产出的素材列表（缩略图网格）。"""
    return await scraper_service.get_task_results(db, task_id, page, size)


@router.post("/tasks/{task_id}/results/batch-delete")
async def task_results_batch_delete(
    task_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """批量将采集任务产出的指定素材移入垃圾桶（软删除，可恢复）。

    请求体: {"ids": ["id1", "id2", ...], "reason": "不喜欢"}
    reason 为空时按素材状态自动推断（质量审核被拒 → 质量差，其余 → 不喜欢）。
    """
    ids = payload.get("ids", [])
    reason = payload.get("reason")
    return await scraper_service.batch_delete_task_results(db, task_id, ids, reason)
