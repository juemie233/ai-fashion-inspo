"""博主主页信息补全服务：为缺失 profile_url / platform_user_id 的小红书博主自动补全。

策略（本地互推优先，减少搜索与风控暴露）：
1. 本地互推：profile_url ↔ platform_user_id 可互相推导（主页 URL 含用户 ID）——
   「有 URL 无 ID」从 URL 提取，「有 ID 无 URL」直接拼接，均无需搜索；
2. 两者都缺：使用小红书 CDP/Playwright 采集引擎按 xhs_id 搜索用户——
   唯一候选直接采纳；多候选时昵称完全匹配才采纳；否则标记失败（需人工核对）；
3. 单博主失败不阻塞整体；不覆盖已有 platform_user_id；
4. 结果三态：
   - updated：成功补全
   - skipped：确定性无法补全（缺小红书号 / 搜索无结果 / 无法唯一确认 /
     主页 URL 无法解析）——自动写入跳过表，不再出现在缺失列表，可解除后重试
   - failed：临时性问题（Cookie 缺失/登录墙/网络异常等）——不跳过，
     保留在缺失列表，问题解决后重试
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import case, delete, or_, select
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


def _normalize_candidate_name(name: str) -> str:
    """候选昵称归一化（先截断「小红书号：xxx」噪声后缀，再归一化）。"""
    return _normalize_name(name.split("小红书号")[0])


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
    """查询缺失主页信息的小红书博主（profile_url 或 platform_user_id 为空）。

    排除已被「跳过」的博主（确定性无法补全，避免每次重复失败）；
    临时性问题（Cookie 等）不跳过，仍在列表中供重试。

    参数:
        blogger_ids: 限定范围（None/空 = 全部缺失且未跳过的博主）
    """
    stmt = select(Blogger).where(
        Blogger.platform == "xiaohongshu",
        or_(Blogger.profile_url.is_(None), Blogger.platform_user_id.is_(None)),
        ~Blogger.id.in_(
            select(BloggerEnrichmentSkip.blogger_id)
        ),
    )
    if blogger_ids:
        stmt = stmt.where(Blogger.id.in_(blogger_ids))
    # 处理顺序：优先「两项信息都缺失」的博主（完全无法定位采集，最需要补全），
    # 其次「缺一项」的（本地互推即可补全，随时可处理）
    stmt = stmt.order_by(
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
    db: AsyncSession, blogger: Blogger, search_users=None
) -> dict:
    """补全单个博主主页信息，返回处理明细。

    参数:
        blogger: 博主记录（须为小红书平台且缺主页信息）
        search_users: 用户搜索函数（默认 XiaohongshuScraper.search_users；
            测试可注入假实现）。签名: async (keyword) -> list[dict]

    返回:
        {"blogger_id", "name", "status": "updated"|"skipped"|"failed",
         "reason"?, "profile_url"?, "platform_user_id"?}

    状态语义：
    - updated：成功补全
    - skipped：确定性无法补全（缺小红书号/搜索无结果/无法唯一确认/URL 解析失败），
      自动写入跳过表（不再出现在缺失列表，可解除后重试）
    - failed：临时性问题（Cookie/登录墙/网络异常），不跳过，保留重试
    """
    blog_id = blogger.id
    name = blogger.name
    url = blogger.profile_url
    uid = blogger.platform_user_id

    # ── 1. 本地互推（缺一补一，无需搜索）──
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

    # ── 2. 两者都缺 → 按小红书号搜索用户 ──
    if not blogger.xhs_id:
        # 确定性失败：无小红书号无法定位 → 跳过
        await mark_skipped(db, [blog_id], "缺少小红书号（xhs_id），无法搜索定位")
        return {
            "blogger_id": blog_id,
            "name": name,
            "status": "skipped",
            "reason": "缺少小红书号（xhs_id），无法搜索定位",
        }
    if search_users is None:
        from app.scrapers.xiaohongshu import XiaohongshuScraper

        search_users = XiaohongshuScraper(
            headless=True, cookie_file=None
        ).search_users
    try:
        candidates = await search_users(blogger.xhs_id)
    except Exception as e:  # noqa: BLE001 浏览器/网络/Cookie 异常属临时性问题：不跳过
        logger.warning(f"博主 #{blog_id} 用户搜索异常: {e}")
        return {
            "blogger_id": blog_id,
            "name": name,
            "status": "failed",
            "reason": f"用户搜索失败: {e}",
        }

    matched: dict | None = None
    if len(candidates) == 1:
        # 唯一候选直接采纳（搜索词即小红书号，单候选大概率是号主；
        # 多候选才需要严格校验，避免误采纳无关用户）
        matched = candidates[0]
    else:
        # 匹配优先级：小红书号精确匹配（搜索即按号发起，最可靠）→
        # 昵称精确匹配 → 昵称归一化匹配（容忍大小写/全角/空格/emoji 差异）
        norm_name = _normalize_name(name)
        norm_xhs_id = _normalize_name(blogger.xhs_id or "")
        for candidate in candidates:
            # 小红书号匹配：大小写不敏感（号通常不区分大小写，如 Softrin/softrin）
            cand_xhs = _normalize_name(candidate.get("xhs_id") or "")
            if norm_xhs_id and cand_xhs == norm_xhs_id:
                matched = candidate
                break
        if matched is None:
            for candidate in candidates:
                cand_name = candidate.get("name") or ""
                if cand_name == name or (
                    cand_name and _normalize_candidate_name(cand_name) == norm_name
                ):
                    matched = candidate
                    break
    if matched is None:
        # 确定性失败：无结果/无法唯一确认 → 跳过（可解除后重试）
        reason = (
            "搜索无结果"
            if not candidates
            else f"搜索结果 {len(candidates)} 个无法唯一确认（需人工核对）"
        )
        await mark_skipped(db, [blog_id], reason)
        return {
            "blogger_id": blog_id,
            "name": name,
            "status": "skipped",
            "reason": reason,
        }

    await _update(db, blogger, matched["profile_url"], matched["platform_user_id"])
    return {
        "blogger_id": blog_id,
        "name": name,
        "status": "updated",
        "profile_url": matched["profile_url"],
        "platform_user_id": matched["platform_user_id"],
    }


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
