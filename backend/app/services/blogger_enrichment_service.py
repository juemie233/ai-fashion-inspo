"""博主资料补全服务：小红书按关注列表解析 uid，抖音走 f2 用户库离线回填。

**小红书**（为缺失 profile_url / platform_user_id 的博主补全）策略（本地优先，
不联网搜用户）：
1. 本地互推：profile_url ↔ platform_user_id 可互相推导（主页 URL 含用户 ID）——
   「有 URL 无 ID」从 URL 提取，「有 ID 无 URL」直接拼接，均无需请求；
2. 两者都缺：任务执行器先拉一次「我关注的用户」列表（一次请求拿到全部关注账号的
   uid），这里按**昵称归一化**匹配取 uid 拼主页 URL（归一化后重名的昵称整条剔除，
   宁可不填也不写错人）。为什么不用搜索：实测小红书的用户搜索无法按「小红书号」
   定位用户（返回名称相近的无关用户），而素材库里的博主正是从这份关注列表导入的，
   昵称可以一一对应；
3. 单博主失败不阻塞整体；不覆盖已有 platform_user_id；
4. 结果三态：
   - updated：成功补全
   - skipped：确定性无法补全（缺小红书号 / 不在关注列表 / 主页 URL 无法解析）
     ——自动写入跳过表，不再出现在缺失列表，可解除后重试
   - failed：临时性问题（Cookie 缺失/登录墙/网络异常等）——不跳过，
     保留在缺失列表，问题解决后重试

**抖音**（为缺失 IP 属地的博主回填）策略：直接读 f2 用户库
（``douyin_users.db`` → ``user_info_web.ip_location``），**离线、零网络、零风控**：
- 只按 ``bloggers.platform_user_id`` ↔ ``sec_user_id`` **精确匹配**（不猜昵称，
  避免把别人的属地写进来）；
- **只补空缺**：已有 IP 属地一律不动（用户手填/CSV 导入的值优先）；
- 三种「条件未具备」（缺 ID / f2 库里没有该账号 / f2 库该账号没有属地）**只记在本轮
  结果明细里，不写跳过表**——它们全都可重试（补 ID、让 f2 采一次主页），博主继续留在
  补全列表里等条件具备。跳过表从此只由小红书路径写入（缺小红书号 / 不在关注列表，
  那才是确定性无法获取的）。

  ⚠️ 2026-09-27 修：原先这三种情况也写跳过表，而补全列表会排除跳过博主 → 跑过一次
  补全后列表被清空（实测缺口抖音博主 439 个、跳过表 450 行、列表 0 个），
  「一键补全」从此不出现在界面上（用户问的就是「一朵芝士为什么不在一键补全列表里」）。

**抖音：按需解析主页标识**（:func:`resolve_douyin_profile`，博主详情页点「下载所有作品」
时触发，不进批量补全任务）：从「我的喜欢 / 我的收藏」采回来的素材只带作者昵称、目录名
却是「我」，补建出的博主没有主页链接 → 按钮点不动。该函数按「本地互推 → f2 用户库按昵称
唯一命中（离线）→ 拿素材的真实作品 ID 跑一次 ``f2 -M one`` 反查作者（联网）」逐级尝试。
联网那一步**必须交叉验证昵称**且不抢别人已占用的 ID，不一致就不写库（详见函数说明）。
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import and_, case, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models.person import Blogger, BloggerEnrichmentSkip

logger = logging.getLogger(__name__)

# 小红书主页 URL 中的用户 ID（路径 /user/profile/<id>）
PROFILE_ID_RE = re.compile(r"/user/profile/([a-zA-Z0-9_-]+)")


def _normalize_name(s: str) -> str:
    """昵称归一化（仅用于匹配比较，不落库）。

    strip + 全角转半角 + 小写 + 去除空白与 emoji/符号（保留中英文与数字）。
    小红书昵称常带 emoji/空格/全角字符，且候选解析可能混入「小红书号：xxx」
    后缀，精确 == 匹配会大面积失败；归一化后比较可显著提升「唯一确认」命中率。
    """
    out: list[str] = []
    for ch in (s or "").strip().lower():
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))  # 全角 → 半角
        elif code == 0x3000:
            out.append(" ")  # 全角空格 → 半角
        else:
            out.append(ch)
    # \W 匹配非「单词字符」（中文/字母/数字之外的 emoji、标点、空白等）
    return re.sub(r"[\s\W_]+", "", "".join(out))


def extract_user_id_from_url(url: str) -> str | None:
    """从主页 URL 提取平台用户 ID（无法解析返回 None）。"""
    m = PROFILE_ID_RE.search(url)
    return m.group(1) if m else None


def build_profile_url(user_id: str) -> str:
    """由平台用户 ID 拼接主页 URL。"""
    return f"https://www.xiaohongshu.com/user/profile/{user_id}"


# 抖音主页 URL 前缀（与 scraper/f2_authors.py 的 _DOUYIN_USER_URL 同口径）：
# 回填 sec_user_id 时一并补出主页链接，博主立刻就能用「下载所有作品」
_DOUYIN_PROFILE_URL = "https://www.douyin.com/user/"


async def backfill_douyin_ids_from_f2(
    db: AsyncSession, profiles: dict[str, dict], blogger_ids: list[int] | None = None
) -> dict:
    """按「归一化昵称唯一命中」给抖音博主回填 ``sec_user_id``（顺带补主页链接）。

    为什么需要：抖音侧的补全（回填 IP 属地）必须先在 f2 用户库里按 ``sec_user_id``
    定位，而实测 439 个缺口抖音博主里有 437 个**连 sec_user_id 都没有**——不解决这
    一步，「一键补全」对他们永远只能回一句「缺少平台用户 ID」。f2 用户库里有昵称，
    昵称归一化后能与博主名对应（「下载白名单」本来就是这么匹配的，见
    ``scripts/f2_plan.match_authors``），这里把同一口径复用过来，反方向把 ID 写回库。

    **安全前提（为什么不是「有昵称就写」）**：写错 ID 会把别人的作品与 IP 属地认到
    这个人头上，所以只在**两侧都唯一**时才写：
    1. f2 用户库里该昵称只能有 1 个账号（多个同昵称账号 → 整条剔除）；
    2. 库内待回填的博主里该昵称也只能出现 1 次（同名博主 → 整条剔除；与小红书侧
       「归一化后重名的昵称整条剔除」同一原则）；
    3. 候选 ``sec_user_id`` 未被别的博主占用（不抢别人的 ID）；
    4. 只补空缺：已有 ``platform_user_id`` 的博主不动，``profile_url`` 仅在为空时补。

    Args:
        db: 数据库会话。
        profiles: :func:`load_f2_profile_map` 的结果（``{sec_user_id: {nickname, ...}}``）。
        blogger_ids: 限定范围（None = 全部缺 ID 的抖音博主）。

    Returns:
        ``{"filled", "ambiguous_blogger", "ambiguous_f2", "not_found", "already_used",
        "details"}``；``details`` 逐条给出 filled / skipped（含原因）。
        **跳过只写在本轮明细里，不写跳过表**——这些原因都可重试（见模块 docstring）。
    """
    from collections import Counter, defaultdict

    from scripts import import_f2_downloads as f2

    stmt = select(Blogger).where(
        Blogger.platform == "douyin",
        or_(
            Blogger.platform_user_id.is_(None),
            Blogger.platform_user_id == "",
        ),
    )
    if blogger_ids:
        stmt = stmt.where(Blogger.id.in_(blogger_ids))
    candidates = list((await db.execute(stmt)).scalars().all())

    stats: dict = {
        "filled": 0,
        "ambiguous_blogger": 0,
        "ambiguous_f2": 0,
        "not_found": 0,
        "already_used": 0,
        "details": [],
    }
    if not candidates:
        return stats

    # 昵称 → f2 账号（同一昵称可能有多个账号，交给下面的「唯一」判定剔除）
    by_nickname: dict[str, list[str]] = defaultdict(list)
    for sec_user_id, profile in profiles.items():
        key = f2.normalize_author(profile.get("nickname") or "")
        if key:
            by_nickname[key].append(sec_user_id)

    # 库内重名（只看待回填的这批）：同名博主整条剔除，宁可不填也不写错
    nickname_counts = Counter(f2.normalize_author(b.name or "") for b in candidates)
    # 已被别的博主占用的 ID：不抢
    occupied = {
        uid
        for uid in (
            await db.execute(
                select(Blogger.platform_user_id).where(
                    Blogger.platform == "douyin",
                    Blogger.platform_user_id.is_not(None),
                    Blogger.platform_user_id != "",
                )
            )
        ).scalars().all()
        if uid
    }

    for blogger in candidates:
        detail = {"blogger_id": blogger.id, "name": blogger.name}
        key = f2.normalize_author(blogger.name or "")
        if not key:
            stats["not_found"] += 1
            stats["details"].append(
                {**detail, "status": "skipped", "reason": "博主语义名为空，无法按昵称匹配"}
            )
            continue
        if nickname_counts[key] > 1:
            stats["ambiguous_blogger"] += 1
            stats["details"].append(
                {
                    **detail,
                    "status": "skipped",
                    "reason": "库内有多个同名博主，无法确定是哪一个（宁可不填，避免写错 ID）",
                }
            )
            continue
        matches = by_nickname.get(key) or []
        if not matches:
            stats["not_found"] += 1
            stats["details"].append(
                {
                    **detail,
                    "status": "skipped",
                    "reason": "f2 用户库里没有同名账号（让 f2 采一次 TA 的主页后再试）",
                }
            )
            continue
        if len(matches) > 1:
            stats["ambiguous_f2"] += 1
            stats["details"].append(
                {
                    **detail,
                    "status": "skipped",
                    "reason": (
                        f"f2 用户库里有 {len(matches)} 个同名账号，无法确定是哪一个"
                        "（宁可不填，避免把别人的作品算到 TA 头上）"
                    ),
                }
            )
            continue

        sec_user_id = matches[0]
        if sec_user_id in occupied:
            stats["already_used"] += 1
            stats["details"].append(
                {
                    **detail,
                    "status": "skipped",
                    "reason": "该账号已绑定到另一位博主，不重复绑定",
                }
            )
            continue

        blogger.platform_user_id = sec_user_id
        if not (blogger.profile_url or "").strip():
            blogger.profile_url = f"{_DOUYIN_PROFILE_URL}{sec_user_id}"
        occupied.add(sec_user_id)
        stats["filled"] += 1
        stats["details"].append(
            {**detail, "status": "filled", "sec_user_id": sec_user_id}
        )
        logger.info(
            f"抖音博主 #{blogger.id}「{blogger.name}」按昵称唯一命中回填 sec_user_id"
        )

    await db.commit()
    return stats


async def list_missing_profile_bloggers(
    db: AsyncSession, blogger_ids: list[int] | None = None
) -> list[Blogger]:
    """查询「资料有缺口」的博主：小红书缺主页信息、抖音缺 IP 属地。

    缺口定义：
    - 小红书：``profile_url`` 或 ``platform_user_id`` 为空（在线搜索补全）
    - 抖音：``ip_location`` 为空（从 f2 用户库离线回填）

    排除已被「跳过」的博主（确定性无法补全，避免每次重复失败）；
    临时性问题（Cookie 等）不跳过，仍在列表中供重试。

    参数:
        blogger_ids: 限定范围（None/空 = 全部有缺口且未跳过的博主）
    """
    stmt = select(Blogger).where(
        or_(
            and_(
                Blogger.platform == "xiaohongshu",
                or_(
                    Blogger.profile_url.is_(None),
                    Blogger.platform_user_id.is_(None),
                ),
            ),
            and_(
                Blogger.platform == "douyin",
                or_(
                    Blogger.ip_location.is_(None),
                    Blogger.ip_location == "",
                ),
            ),
        ),
        ~Blogger.id.in_(select(BloggerEnrichmentSkip.blogger_id)),
    )
    if blogger_ids:
        stmt = stmt.where(Blogger.id.in_(blogger_ids))
    # 处理顺序：抖音离线回填零成本先做（立刻见效）；小红书里优先「两项信息都缺失」
    # 的博主（完全无法定位采集，最需要补全），其次「缺一项」的（本地互推即可补全）
    stmt = stmt.order_by(
        case((Blogger.platform == "douyin", 0), else_=1),
        case(
            (
                Blogger.profile_url.is_(None) & Blogger.platform_user_id.is_(None),
                0,
            ),
            else_=1,
        ),
        Blogger.id,
    )
    return list((await db.execute(stmt)).scalars().all())


def load_f2_profile_map() -> dict[str, dict]:
    """读取 f2 用户库的账号资料（{sec_user_id: {nickname, ip_location}}）。

    阻塞式文件读取（sqlite 只读）：调用方放线程池；f2 目录/库缺失时返回空 dict，
    由调用方按「f2 用户库不可用」处理（全部记为跳过并说明原因）。
    """
    from scripts import import_f2_downloads as f2

    try:
        return f2.load_f2_profiles(f2.DEFAULT_F2_DIR)
    except Exception as exc:  # noqa: BLE001 —— 读不了 f2 库不该让整批补全失败
        logger.warning(f"读取 f2 用户库失败（抖音 IP 属地回填将跳过）：{exc}")
        return {}


async def backfill_douyin_from_f2(
    db: AsyncSession, blogger: Blogger, profiles: dict[str, dict]
) -> dict:
    """按 f2 用户库回填单个抖音博主的 IP 属地（离线；只补空缺）。

    Args:
        db: 数据库会话。
        blogger: 抖音博主行。
        profiles: :func:`load_f2_profile_map` 的结果（按 sec_user_id 索引）。

    Returns:
        与 :func:`enrich_one` 同形的明细：{"blogger_id", "name", "status",
        "reason"?, "ip_location"?}；status ∈ updated / skipped。
        **三种「条件未具备」只返回 skipped 明细、不写跳过表**（见模块 docstring）：
        博主仍留在补全列表，补了 ID 或让 f2 采过一次后再跑即可。
    """
    detail = {"blogger_id": blogger.id, "name": blogger.name}

    if blogger.ip_location:
        # 只补空缺：已有值（手填 / CSV 导入）一律不动
        return {
            **detail,
            "status": "skipped",
            "reason": f"已有 IP 属地（{blogger.ip_location}），按「只补空缺」策略跳过",
        }

    uid = (blogger.platform_user_id or "").strip()
    if not uid:
        return {
            **detail,
            "status": "skipped",
            "reason": (
                "缺少平台用户 ID（sec_user_id），本轮无法在 f2 用户库里定位；"
                "补上 TA 的主页链接/sec_user_id 后再跑即可（不写跳过表，仍留在补全列表）"
            ),
        }

    profile = profiles.get(uid)
    if profile is None:
        return {
            **detail,
            "status": "skipped",
            "reason": (
                "f2 用户库里没有这个账号：让 f2 采一次 TA 的主页后再跑即可"
                "（不写跳过表，仍留在补全列表）"
            ),
        }

    ip_location = str(profile.get("ip_location") or "").strip()
    if not ip_location:
        return {
            **detail,
            "status": "skipped",
            "reason": (
                "f2 用户库里该账号没有 IP 属地：让 f2 采一次 TA 的主页后再跑即可"
                "（不写跳过表，仍留在补全列表）"
            ),
        }

    blogger.ip_location = ip_location
    await db.commit()
    logger.info(f"抖音博主 #{blogger.id}「{blogger.name}」IP 属地已回填：{ip_location}")
    return {**detail, "status": "updated", "ip_location": ip_location}


# ═══════════════════════════════════════════════════════════════
#  抖音：按「作品 ID」反查作者 sec_user_id（按需，单博主一次）
# ═══════════════════════════════════════════════════════════════

"""抖音作品 ID 形态：19 位数字（与 ``scripts.f2_plan.platform_id_for`` 的新口径一致）。

**必须是数字**：历史素材存的是 12 位十六进制短哈希（``f2:184ef89706bf#image2``），
那是文件名合成的，追不回作者——按位数挡掉，别拿去给 f2 当作品 ID。
"""
_DOUYIN_AWEME_IN_PLATFORM_ID_RE = re.compile(r"^f2:(\d{15,})#")
_DOUYIN_AWEME_IN_URL_RE = re.compile(r"/(?:note|video)/(\d{15,})")


def aweme_id_from_source(
    source_platform_id: str | None, source_url: str | None
) -> str | None:
    """从素材的来源字段提取抖音作品 ID（纯函数；取不到返回 None）。

    先看 ``source_platform_id``（``f2:{aweme_id}#image1``，抖音作品本身是权威身份），
    再退回 ``source_url``（``https://www.douyin.com/note/{aweme_id}``）——两者其一
    有真实作品 ID 即可反查作者。

    Args:
        source_platform_id: 素材的平台 ID。
        source_url: 素材的原始链接。

    Returns:
        19 位作品 ID；两处都没有（或只有历史哈希）时返回 None。
    """
    m = _DOUYIN_AWEME_IN_PLATFORM_ID_RE.match((source_platform_id or "").strip())
    if m:
        return m.group(1)
    m = _DOUYIN_AWEME_IN_URL_RE.search((source_url or "").strip())
    return m.group(1) if m else None


async def find_blogger_awemes(
    db: AsyncSession, blogger_id: int, limit: int = 3
) -> list[tuple[str, str]]:
    """挑出可用于反查作者的抖音作品：``[(aweme_id, source_url)]``（最新优先）。

    为什么取最新几条而不是固定第一条：反查要真跑一次 f2（约 20~40 秒），而被删 /
    转私密的作品反查不到作者——素材越新越可能还在。同一作品的多个素材只留一条。

    Args:
        db: 数据库会话。
        blogger_id: 博主 ID。
        limit: 最多返回多少个作品。

    Returns:
        去重后的 ``(aweme_id, source_url)`` 列表（``source_url`` 可能为空串）；
        该博主没有任何带真实作品 ID 的素材时返回空列表。
    """
    from app.models.inspiration import NOT_DELETED, Inspiration
    from app.models.person import InspirationBlogger

    rows = (
        await db.execute(
            select(Inspiration.source_platform_id, Inspiration.source_url)
            .join(
                InspirationBlogger,
                InspirationBlogger.inspiration_id == Inspiration.id,
            )
            .where(InspirationBlogger.blogger_id == blogger_id, NOT_DELETED)
            .order_by(Inspiration.created_at.desc())
            .limit(max(limit * 8, 24))
        )
    ).all()

    works: list[tuple[str, str]] = []
    seen: set[str] = set()
    for platform_id, url in rows:
        aweme_id = aweme_id_from_source(platform_id, url)
        if not aweme_id or aweme_id in seen:
            continue
        seen.add(aweme_id)
        works.append((aweme_id, (url or "").strip()))
        if len(works) >= limit:
            break
    return works


async def _sec_id_taken(
    db: AsyncSession, sec_user_id: str, exclude_blogger_id: int
) -> bool:
    """该 ``sec_user_id`` 是否已绑定到**别的**抖音博主（不抢别人的 ID）。"""
    row = (
        await db.execute(
            select(Blogger.id).where(
                Blogger.platform == "douyin",
                Blogger.platform_user_id == sec_user_id,
                Blogger.id != exclude_blogger_id,
            )
        )
    ).first()
    return row is not None


async def _apply_resolved_profile(
    db: AsyncSession, blogger: Blogger, sec_user_id: str, ip_location: str = ""
) -> None:
    """把反查到的作者信息写回博主（**只补空缺**，已有值一律不动）。

    - ``platform_user_id``：有了它，「下载所有作品」与 IP 属地回填都能定位账号；
    - ``profile_url``：仅为空时按抖音主页模板补；
    - ``ip_location``：f2 顺手带回来的属地，同样只补空缺（手填/CSV 导入的值优先）。
    """
    blogger.platform_user_id = sec_user_id
    if not (blogger.profile_url or "").strip():
        blogger.profile_url = f"{_DOUYIN_PROFILE_URL}{sec_user_id}"
    if ip_location and not (blogger.ip_location or "").strip():
        blogger.ip_location = ip_location
    await db.commit()
    logger.info(
        f"抖音博主 #{blogger.id}「{blogger.name}」已解析出主页（sec_user_id={sec_user_id}）"
    )


async def resolve_douyin_profile(
    db: AsyncSession, blogger: Blogger, max_works: int = 3
) -> dict:
    """按需解析一个抖音博主的主页标识（``sec_user_id`` + 主页链接）。

    用户场景：抖音「我的喜欢 / 我的收藏」采回来的素材只带了**作者昵称**（f2 把原作者
    写进文件名，目录名却是「我」），补建出来的博主没有主页链接 → 博主详情页的
    「下载所有作品」点不动，只能手工去补链接。这里给出**不用手填**的三条路径，按
    成本从低到高依次尝试：

    1. **本地互推**：已有 ``platform_user_id`` 只缺链接 → 直接拼（零网络、零风险）；
    2. **f2 用户库按昵称唯一命中**（离线）：昵称归一化后两侧都唯一才用（安全前提与
       :func:`backfill_douyin_ids_from_f2` 完全一致：重名整条剔除 + 不抢别人已占用的 ID）；
    3. **按作品 ID 让 f2 反查**（联网，约 20~40 秒）：拿库里这个博主某个素材的真实
       作品 ID 跑一次 ``f2 -M one``，f2 会拉到作品详情（内含作者 ``sec_user_id``）并把
       作者资料写进它自己的用户库，我们把新增的那一行取回来（细节见
       ``scripts.f2_fetch.resolve_f2_author_by_aweme``）。

    ⚠ **第 3 条必须交叉验证昵称**：f2 反查回来的是「这个作品的作者」，如果它与博主名
    归一化后不一致，说明素材归属本身可能有问题（或作品已失效被替换）——此时**不写库**，
    宁可报错让用户看到，也不能把一个陌生账号绑到这个博主头上。同一条保护也用在
    「该 sec_user_id 已被别的博主占用」时。

    Args:
        db: 数据库会话。
        blogger: 抖音博主行（其它平台直接返回失败原因）。
        max_works: 联网反查最多尝试几个作品（每个约 20~40 秒）。

    Returns:
        ``{"ok", "blogger_id", "name", "profile_url", "platform_user_id",
        "ip_location", "source", "attempts", "reason"}``；``source`` ∈
        ``local`` / ``f2_nickname`` / ``f2_aweme``（``ok=False`` 时为空串）。
        失败只回原因，**不改库**。
    """
    import asyncio

    from scripts import import_f2_downloads as f2

    detail: dict = {
        "ok": False,
        "blogger_id": blogger.id,
        "name": blogger.name,
        "profile_url": blogger.profile_url or "",
        "platform_user_id": blogger.platform_user_id or "",
        "ip_location": blogger.ip_location or "",
        "source": "",
        "attempts": [],
        "reason": "",
    }

    if blogger.platform != "douyin":
        return {**detail, "reason": "仅抖音博主支持：作品下载走 f2 的抖音通道"}

    key = f2.normalize_author(blogger.name or "")
    uid = (blogger.platform_user_id or "").strip()
    url = (blogger.profile_url or "").strip()

    # ── 1. 本地互推：有 ID 缺链接，直接拼（零网络）──
    if uid and not url:
        await _apply_resolved_profile(db, blogger, uid)
        return {
            **detail,
            "ok": True,
            "profile_url": blogger.profile_url or "",
            "platform_user_id": uid,
            "source": "local",
            "reason": "",
        }
    if uid and url:
        return {
            **detail,
            "ok": True,
            "source": "local",
            "reason": "该博主已有主页链接，无需解析",
        }

    # ── 2. f2 用户库按昵称唯一命中（离线）──
    if key:
        profiles = await asyncio.to_thread(load_f2_profile_map)
        matches = [
            sec
            for sec, profile in profiles.items()
            if f2.normalize_author(profile.get("nickname") or "") == key
        ]
        if len(matches) == 1 and not await _sec_id_taken(db, matches[0], blogger.id):
            sec_user_id = matches[0]
            ip_location = str((profiles.get(sec_user_id) or {}).get("ip_location") or "")
            await _apply_resolved_profile(db, blogger, sec_user_id, ip_location)
            return {
                **detail,
                "ok": True,
                "profile_url": blogger.profile_url or "",
                "platform_user_id": sec_user_id,
                "ip_location": blogger.ip_location or "",
                "source": "f2_nickname",
                "reason": "",
            }

    # ── 3. 按作品 ID 让 f2 反查（联网；每个作品一次 f2 运行）──
    works = await find_blogger_awemes(db, blogger.id, limit=max_works)
    if not works:
        return {
            **detail,
            "reason": (
                "这位博主的素材里没有可用的抖音作品 ID（历史素材只有文件名哈希，"
                "追不回作者）：请在博主编辑弹窗里手工填一次抖音主页链接"
            ),
        }

    attempts: list[dict] = []
    for aweme_id, source_url in works:
        result = await asyncio.to_thread(
            f2.resolve_f2_author_by_aweme,
            f2.DEFAULT_F2_DIR,
            aweme_id,
            source_url or None,
        )
        sec_user_id = str(result.get("sec_user_id") or "")
        f2_nickname = str(result.get("nickname") or "")
        attempts.append(
            {
                "aweme_id": aweme_id,
                "ok": bool(result.get("ok")),
                "sec_user_id": sec_user_id,
                "nickname": f2_nickname,
                "reason": str(result.get("reason") or ""),
            }
        )
        if not result.get("ok") or not sec_user_id:
            continue
        if f2.normalize_author(f2_nickname) != key:
            # 反查到的作者 ≠ 这位博主：不写库（见 docstring 的交叉验证说明）
            attempts[-1]["reason"] = (
                f"反查到的作者是「{f2_nickname}」，与博主名「{blogger.name}」不一致，"
                "已放弃写入（宁可不填，也不把别人的账号绑到 TA 头上）"
            )
            continue
        if await _sec_id_taken(db, sec_user_id, blogger.id):
            attempts[-1]["reason"] = "该账号已绑定到另一位博主，不重复绑定"
            continue

        await _apply_resolved_profile(
            db, blogger, sec_user_id, str(result.get("ip_location") or "")
        )
        return {
            **detail,
            "ok": True,
            "profile_url": blogger.profile_url or "",
            "platform_user_id": sec_user_id,
            "ip_location": blogger.ip_location or "",
            "source": "f2_aweme",
            "attempts": attempts,
            "reason": "",
        }

    return {
        **detail,
        "attempts": attempts,
        "reason": (
            "自动解析没成功："
            + "；".join(
                f"作品 {a['aweme_id']}：{a['reason'] or '未知原因'}" for a in attempts
            )
            + "。可在博主编辑弹窗里手工填抖音主页链接后重试"
        ),
    }


async def mark_skipped(db: AsyncSession, blogger_ids: list[int], reason: str) -> int:
    """批量标记跳过（幂等：已跳过的更新原因）。返回实际处理数。"""
    ids = list(dict.fromkeys(blogger_ids))
    if not ids:
        return 0
    stmt = sqlite_insert(BloggerEnrichmentSkip).values(
        [{"blogger_id": bid, "reason": reason} for bid in ids]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["blogger_id"], set_={"reason": reason}
    )
    await db.execute(stmt)
    await db.commit()
    return len(ids)


async def unskip(db: AsyncSession, blogger_ids: list[int]) -> int:
    """解除跳过（博主重新纳入补全范围）。返回实际解除数。"""
    ids = list(dict.fromkeys(blogger_ids))
    if not ids:
        return 0
    result = await db.execute(
        delete(BloggerEnrichmentSkip).where(
            BloggerEnrichmentSkip.blogger_id.in_(ids)
        )
    )
    await db.commit()
    return result.rowcount


async def list_skipped(db: AsyncSession) -> list[dict]:
    """已跳过博主列表（含博主名与原因，供前端管理/解除）。"""
    rows = await db.execute(
        select(BloggerEnrichmentSkip, Blogger.name)
        .join(Blogger, Blogger.id == BloggerEnrichmentSkip.blogger_id)
        .order_by(BloggerEnrichmentSkip.created_at.desc())
    )
    return [
        {
            "blogger_id": skip.blogger_id,
            "name": name,
            "reason": skip.reason,
            "created_at": skip.created_at.isoformat(),
        }
        for skip, name in rows.all()
    ]


async def enrich_one(
    db: AsyncSession, blogger: Blogger, following: dict[str, str] | None = None
) -> dict:
    """补全单个小红书博主的主页信息（profile_url / platform_user_id），返回处理明细。

    参数:
        blogger: 博主记录（须为小红书平台且缺主页信息）
        following: 「关注列表」解析结果 {归一化昵称: uid}（任务执行器一次拉全，
            形如 :func:`build_following_index` 的输出）；缺省视为空（本地互推仍可用）

    返回:
        {"blogger_id", "name", "status": "updated"|"skipped",
         "reason"?, "profile_url"?, "platform_user_id"?}

    状态语义：
    - updated：成功补全
    - skipped：确定性无法补全（URL 无法解析 / 不在关注列表里 / 缺小红书号），
      自动写入跳过表（不再出现在缺失列表，可解除后重试）

    路径（都不联网搜用户）：
    1. 本地互推：URL ↔ ID 互相推导（主页 URL 里就带着用户 ID）；
    2. 关注列表：按昵称归一化命中 → 用 uid 拼主页 URL。为什么用关注列表而不是搜索：
       实测小红书用户搜索无法按「小红书号」定位用户（返回名称相近的无关用户），
       而素材库的博主正是从这份关注列表导入的，昵称可一一对应。
    """
    blog_id = blogger.id
    name = blogger.name
    url = blogger.profile_url
    uid = blogger.platform_user_id

    # ── 1. 本地互推（缺一补一，无需任何请求）──
    if url and not uid:
        extracted = extract_user_id_from_url(url)
        if extracted:
            uid = extracted
        else:
            # 确定性失败：URL 存在但无法解析 → 跳过
            await mark_skipped(db, [blog_id], f"主页 URL 无法解析用户 ID: {url}")
            return {
                "blogger_id": blog_id,
                "name": name,
                "status": "skipped",
                "reason": f"主页 URL 无法解析用户 ID: {url}",
            }
    elif uid and not url:
        url = build_profile_url(uid)
    if url and uid:
        await _update(db, blogger, url, uid)
        return {
            "blogger_id": blog_id,
            "name": name,
            "status": "updated",
            "profile_url": url,
            "platform_user_id": uid,
        }

    # ── 2. 两者都缺 → 从关注列表按昵称解析 uid ──
    matched_uid = (following or {}).get(_normalize_name(name)) or ""
    if not matched_uid:
        # 确定性失败：不在关注列表里（改过名 / 已取关 / 本来是手工建的）→ 跳过
        reason = (
            "不在你的小红书关注列表里，无法解析用户 ID"
            "（需先关注 TA 或在编辑弹窗里手工填写平台用户 ID）"
        )
        await mark_skipped(db, [blog_id], reason)
        return {
            "blogger_id": blog_id,
            "name": name,
            "status": "skipped",
            "reason": reason,
        }

    url = build_profile_url(matched_uid)
    await _update(db, blogger, url, matched_uid)
    return {
        "blogger_id": blog_id,
        "name": name,
        "status": "updated",
        "profile_url": url,
        "platform_user_id": matched_uid,
    }


def build_following_index(rows: list[dict]) -> dict[str, str]:
    """把关注列表解析结果转成 {归一化昵称: uid}（供 :func:`enrich_one` 匹配）。

    Args:
        rows: ``XiaohongshuScraper.list_following_sync`` 的返回值
            （每项含 nickname / uid）。

    Returns:
        归一化昵称 → uid；缺昵称或缺 uid 的行跳过。

    「归一化后重名」的昵称**整条剔除**：不同 uid 归一化到同一昵称时无法确定是谁，
    宁可落入「不在关注列表」的跳过分支由用户手工填 ID，也不能随便挑一个把错误的
    uid 永久写进库（如实测关注列表里的 `oo` 与 `oo-` 都会归一化成 `oo`）。
    """
    index: dict[str, str] = {}
    ambiguous: set[str] = set()
    for row in rows or []:
        nickname = str(row.get("nickname") or "").strip()
        uid = str(row.get("uid") or "").strip()
        if not nickname or not uid:
            continue
        key = _normalize_name(nickname)
        if not key:
            continue
        if key in index and index[key] != uid:
            ambiguous.add(key)
            continue
        index[key] = uid
    for key in ambiguous:
        index.pop(key, None)
    if ambiguous:
        logger.warning(f"关注列表有 {len(ambiguous)} 个归一化重名昵称，已排除不参与匹配")
    return index


async def _update(
    db: AsyncSession, blogger: Blogger, url: str, uid: str
) -> None:
    """更新博主主页信息（不覆盖已有 platform_user_id，仅补缺）。"""
    if blogger.profile_url is None:
        blogger.profile_url = url
    if blogger.platform_user_id is None:
        blogger.platform_user_id = uid
    await db.commit()
    logger.info(f"博主 #{blogger.id}「{blogger.name}」主页信息已补全")
