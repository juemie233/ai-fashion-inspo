"""博主资料补全服务：小红书按关注列表解析 uid，抖音走 f2 用户库离线回填。

**小红书**（为缺失 profile_url / platform_user_id 的博主补全）策略（本地优先，
不联网搜用户）：
1. 本地互推：profile_url ↔ platform_user_id 可互相推导（主页 URL 含用户 ID）——
   「有 URL 无 ID」从 URL 提取，「有 ID 无 URL」直接拼接，均无需请求；
2. 两者都缺：任务执行器先拉一次「我关注的用户」列表（一次请求拿到全部关注账号的
   uid），这里按**昵称归一化**匹配取 uid 拼主页 URL。为什么不用搜索：实测小红书的
   用户搜索无法按「小红书号」定位用户（返回名称相近的无关用户），而素材库里的博主
   正是从这份关注列表导入的，昵称可以一一对应；
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
- 缺 ID、f2 库没有该账号、f2 库该账号没有属地 → 记 skipped 并写跳过表（可在
  人物管理页解除跳过后重试，例如补了 ID 或让 f2 采过一次主页之后）。
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
        「确定性无法补全」的三种情况（缺 ID / f2 库无此账号 / f2 库无属地）会写入
        跳过表，用户补了 ID 或让 f2 采过一次后可在界面解除跳过重试。
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
        reason = "缺少平台用户 ID（sec_user_id），无法在 f2 用户库里定位"
        await mark_skipped(db, [blogger.id], reason)
        return {**detail, "status": "skipped", "reason": reason}

    profile = profiles.get(uid)
    if profile is None:
        reason = "f2 用户库里没有这个账号（需让 f2 采一次 TA 的主页后再试）"
        await mark_skipped(db, [blogger.id], reason)
        return {**detail, "status": "skipped", "reason": reason}

    ip_location = str(profile.get("ip_location") or "").strip()
    if not ip_location:
        reason = "f2 用户库里该账号没有 IP 属地（需让 f2 采一次 TA 的主页后再试）"
        await mark_skipped(db, [blogger.id], reason)
        return {**detail, "status": "skipped", "reason": reason}

    blogger.ip_location = ip_location
    await db.commit()
    logger.info(f"抖音博主 #{blogger.id}「{blogger.name}」IP 属地已回填：{ip_location}")
    return {**detail, "status": "updated", "ip_location": ip_location}


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
    """
    index: dict[str, str] = {}
    for row in rows or []:
        nickname = str(row.get("nickname") or "").strip()
        uid = str(row.get("uid") or "").strip()
        if nickname and uid:
            index[_normalize_name(nickname)] = uid
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
