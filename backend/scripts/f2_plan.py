"""去重索引、博主匹配与导入计划（自 scripts/import_f2_downloads.py 拆出，行为不变）。"""

import hashlib
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# 与 backend/scripts 下其它脚本一致：把 backend 加入 sys.path，便于模块方式执行
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings  # noqa: E402

from .f2_common import (  # noqa: E402
    AUTO_BLOGGER_SOURCE,
    ParsedFile,
    group_works,
    normalize_author,
)
from .f2_hash_cache import (  # noqa: E402
    HashCache,
    _digest,
    open_hash_cache,
)


# ═══════════════════════════════════════════════════════════════
#  素材库侧读取（只读）
# ═══════════════════════════════════════════════════════════════


def library_db_path() -> Path:
    """素材库 SQLite 路径（与采集脚本、conftest 口径一致）。"""
    return settings.storage_root.parent / "fashion_inspo.db"


@dataclass(frozen=True)
class DedupIndex:
    """去重判据所需的库内标识集合（一次读取，见 :func:`load_dedup_index`）。

    区分「在库」与「垃圾桶」两类：都不该重复导入，但含义与处置不同——在库是重复，
    垃圾桶是**用户主动丢弃**（想恢复请用垃圾桶还原，而不是让它重新进来）。
    """

    live_hashes: set[str]
    trash_hashes: set[str]
    live_platform_ids: set[str]
    trash_platform_ids: set[str]


def load_dedup_index(db_path: Path | None = None) -> DedupIndex:
    """读取库内四类标识（未删除 / 垃圾桶 × content_hash / source_platform_id）。

    为什么把垃圾桶也算作「已存在」：垃圾桶素材全部作为负样本学习输入，是用户
    明确的取舍结果。历史上本模块只把未删除素材算作重复（`deleted_at IS NULL`），
    结果是用户丢进垃圾桶的素材会在下一次导入时被原样搬回库——手动时代只是偶尔
    撞上，开启「每日自动获取」后会变成每天自动复活一次，与「宁缺毋滥」冲突。

    Args:
        db_path: 数据库路径（缺省用 :func:`library_db_path`）。

    Returns:
        :class:`DedupIndex`；数据库不存在时四类集合均为空。
    """
    empty = DedupIndex(set(), set(), set(), set())
    path = db_path or library_db_path()
    if not path.exists():
        return empty
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        hashes = _split_by_trash(
            conn.execute(
                "SELECT content_hash, deleted_at IS NULL FROM inspirations "
                "WHERE content_hash IS NOT NULL"
            )
        )
        platform_ids = _split_by_trash(
            conn.execute(
                "SELECT source_platform_id, deleted_at IS NULL FROM inspirations "
                "WHERE source_platform_id IS NOT NULL"
            )
        )
    finally:
        conn.close()
    return DedupIndex(
        live_hashes=hashes[0],
        trash_hashes=hashes[1],
        live_platform_ids=platform_ids[0],
        trash_platform_ids=platform_ids[1],
    )


def _split_by_trash(rows: Iterable[tuple[str, int]]) -> tuple[set[str], set[str]]:
    """把 ``(值, 是否未删除)`` 行按「未删除 / 垃圾桶」拆成两个集合。"""
    live: set[str] = set()
    trash: set[str] = set()
    for value, alive in rows:
        (live if alive else trash).add(value)
    return live, trash


def load_douyin_bloggers(
    db_path: Path | None = None, include_auto: bool = False
) -> dict[str, list[dict]]:
    """读取库内抖音博主，按归一化名索引（用于作品绑定博主 / 下载白名单）。

    Args:
        db_path: 数据库路径（缺省用 :func:`library_db_path`）。
        include_auto: 是否连「自动登记」的博主（``source=AUTO_BLOGGER_SOURCE``，
            由「我的喜欢」入库时补建）一起返回。默认 False：自动登记的账号**不算
            已登记博主**，不进「一键获取素材」的下载白名单（见
            :data:`AUTO_BLOGGER_SOURCE`）；用户确认后改回 manual 即视为已登记。

    Returns:
        归一化名 → [{"id", "name", "platform_user_id"}]（同名可能多条，故用列表）。
        ``platform_user_id`` 是博主的 sec_user_id，回填后作为「f2 账号 ↔ 博主」的
        权威对应关系（见 :func:`select_known_authors`）。
    """
    path = db_path or library_db_path()
    if not path.exists():
        return {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        where = "platform = 'douyin'"
        if not include_auto:
            # source 为 NULL 的旧记录按「已登记」处理（自动登记一定会写这个标记）
            where += f" AND (source IS NULL OR source != '{AUTO_BLOGGER_SOURCE}')"
        try:
            rows = conn.execute(
                f"SELECT id, name, platform_user_id FROM bloggers WHERE {where}"
            ).fetchall()
        except sqlite3.OperationalError:
            # 迁移前的库副本 / 最小化测试库可能缺少 source 列：退回不过滤自动登记
            # 的口径（此时无法区分自动登记，全部按已登记处理）
            try:
                rows = conn.execute(
                    "SELECT id, name, platform_user_id FROM bloggers "
                    "WHERE platform = 'douyin'"
                ).fetchall()
            except sqlite3.OperationalError:
                # 连 platform_user_id 都没有：只剩 id/name（匹配只能用昵称口径）
                rows = [
                    (row[0], row[1], None)
                    for row in conn.execute(
                        "SELECT id, name FROM bloggers WHERE platform = 'douyin'"
                    ).fetchall()
                ]
    finally:
        conn.close()
    indexed: dict[str, list[dict]] = defaultdict(list)
    for blogger_id, name, platform_user_id in rows:
        indexed[normalize_author(name)].append(
            {"id": blogger_id, "name": name, "platform_user_id": platform_user_id}
        )
    return dict(indexed)


def match_authors(
    authors: list[dict], bloggers: dict[str, list[dict]]
) -> list[tuple[dict, dict | None]]:
    """把 f2 账号逐条对上库内已登记博主：``[(f2 账号, 博主 | None)]``。

    ``None`` 表示该账号未登记到博主库。判定口径与 :func:`select_known_authors`
    完全一致（本函数就是它的实现，两处共用同一段判断，避免「下载白名单」与
    「博主清单展示」各写一套后漂移）；同名多候选时取第一条（白名单只看是否命中）。

    Args:
        authors: :func:`load_f2_authors` 的结果。
        bloggers: :func:`load_douyin_bloggers` 的结果。

    Returns:
        与 ``authors`` 等长、同顺序的配对列表（供展示每行对应的博主 id/名称）。
    """
    by_uid: dict[str, dict] = {}
    for group in bloggers.values():
        for blogger in group:
            uid = blogger.get("platform_user_id")
            if uid:
                by_uid.setdefault(uid, blogger)
    matched: list[tuple[dict, dict | None]] = []
    for author in authors:
        sec_user_id = author.get("sec_user_id")
        # 1) sec_user_id 精确命中（权威：同 ID 必同人）
        blogger = by_uid.get(sec_user_id) if sec_user_id else None
        # 2) 归一化昵称命中（兜底：覆盖没回填 sec_user_id 的博主）
        if blogger is None:
            group = bloggers.get(normalize_author(author.get("nickname") or "")) or []
            blogger = group[0] if group else None
        matched.append((author, blogger))
    return matched


def select_known_authors(
    authors: list[dict], bloggers: dict[str, list[dict]]
) -> tuple[list[dict], list[dict]]:
    """把 f2 用户库里的账号拆成「库里已登记的博主」与「未登记账号」两拨。

    为什么需要：f2 的 ``user_info_web`` 装的是**它见过/登录过的所有账号**，
    不等于我们素材库里登记的博主。实测里面混进过「网易第五人格」这类与穿搭
    无关的官方号，被一键获取原样下载并入库（142 条，0 条博主绑定）。
    默认只处理能对上库内博主的账号，剩下的显式列出来。

    判定口径（任一命中即算已登记）：
      1. ``sec_user_id`` 精确命中某博主的 ``platform_user_id``（权威：同 ID 必同人）
      2. 归一化昵称命中博主名（兜底：覆盖没回填 sec_user_id 的博主）

    Args:
        authors: :func:`load_f2_authors` 的结果。
        bloggers: :func:`load_douyin_bloggers` 的结果。

    Returns:
        ``(已登记账号, 未登记账号)``，两者都保持入参顺序。
    """
    known: list[dict] = []
    unknown: list[dict] = []
    for author, blogger in match_authors(authors, bloggers):
        (known if blogger is not None else unknown).append(author)
    return known, unknown


# ═══════════════════════════════════════════════════════════════
#  导入计划（去重判据）与落库
# ═══════════════════════════════════════════════════════════════

"""导入批次清单目录名（落在 storage/ 下，便于回滚与审计）。"""
IMPORT_BATCH_DIRNAME = "import_batches"

"""博主关联置信度：按作者目录名匹配（非人脸匹配），低于人脸匹配的可信度。"""
AUTHOR_MATCH_CONFIDENCE = 0.9

"""f2 导入的入库 INSERT。

与采集路径的 INSERT_INSPIRATION_IMAGE_SQL 形状一致，两处差异说明：
  - thumbnail_path 可写（本路径图片也生成缩略图），不再固定 NULL
  - content_hash 对视频同样写入（f2 是本地文件，可低成本算哈希；采集路径的
    视频走 URL 下载，历史实现留空）
⚠ 列清单 / 值 / 占位符必须一一对应：历史事故（任务 #46）曾因漏占位符导致
整条采集链路静默颗粒无收，本模块用测试锁死三者数量。
"""


def work_hash(work_key: str) -> str:
    """作品键 → 12 位短哈希（合成平台 ID 用，规避中文与长度超限）。"""
    return hashlib.sha1(work_key.encode("utf-8")).hexdigest()[:12]


def platform_id_for(item: ParsedFile) -> str:
    """构造素材的 source_platform_id：``f2:{真实作品ID}#{序号}``。

    两套口径（新文件有作品 ID，历史文件没有）：

    - **新**（文件名含 `{aweme_id}`）：``f2:{aweme_id}#image1``——身份来自抖音
      作品本身，与文件名无关；重命名/移动文件不再影响幂等，也无法造伪。
    - **旧**（历史文件）：``f2:{作品短哈希}#image1``——沿用文件名合成哈希。

    两套 ID 必须并存：已入库的历史素材不会自动获得新 ID，去重判据要同时认
    （见 :func:`platform_ids_for`）。两套不会撞车——短哈希固定 12 位十六进制，
    作品 ID 固定 19 位数字，字符集与长度都不同。

    ⚠ 为什么必须带**类型前缀**的序号：``ix_inspirations_source_platform_id``
    是**全局唯一**索引（仅约束未删除素材，见 models/inspiration.py），同一
    作品的多张图/多段视频不能共用一个平台 ID。序号还必须带类型——同一作品
    常同时有静态图与 live 分段（``..._image_1.webp`` 与 ``..._live_1.mp4``），
    只用数字会让两者都变成 ``#1`` 而撞唯一索引（试跑实测：live 分段整批失败）。

    Args:
        item: 已解析文件。

    Returns:
        稳定的平台 ID（同一文件重复计算结果一致 → 天然幂等）；
        仍可按前缀聚合出「同一作品」的全部素材。
    """
    suffix = (
        f"{item.kind}{item.index}" if item.kind in ("image", "live") else item.kind
    )
    if item.aweme_id:
        return f"f2:{item.aweme_id}#{suffix}"
    return f"f2:{work_hash(item.work_key)}#{suffix}"


def legacy_platform_id_for(item: ParsedFile) -> str:
    """旧口径平台 ID（文件名合成哈希）——新文件判重时也要一起认。

    为什么需要：同一作品先前用旧模板下载入库过（库里存的是哈希 ID），改用新
    模板重下时算出来的是作品 ID，只比对新 ID 会漏判 → 重复入库。内容哈希虽是
    主判据，但重下可能字节不同，平台 ID 这一层必须两套都查。

    Args:
        item: 已解析文件。

    Returns:
        旧口径平台 ID。
    """
    suffix = (
        f"{item.kind}{item.index}" if item.kind in ("image", "live") else item.kind
    )
    return f"f2:{work_hash(item.work_key)}#{suffix}"


def platform_ids_for(item: ParsedFile) -> list[str]:
    """该文件可能对应的全部平台 ID（用于「两套口径并存」的判重）。

    Args:
        item: 已解析文件。

    Returns:
        ``[主口径, ...]``；新文件含两个（作品 ID + 旧哈希），旧文件只有一个。
    """
    primary = platform_id_for(item)
    legacy = legacy_platform_id_for(item)
    return [primary] if primary == legacy else [primary, legacy]


def source_url_for(item: ParsedFile, is_video_work: bool = False) -> str | None:
    """构造素材的 source_url（抖音原帖地址）；无作品 ID 时返回 None。

    路径按**作品类型**决定，不是按单个文件的 ``media_type``：

    - 视频作品 → ``/video/{aweme_id}``
    - 图集作品 → ``/note/{aweme_id}``（图集里的 live 实况分段入库后也是 video，
      但它所属的作品仍是图集）

    ``kind`` 单独判断不够：视频作品的**封面**（f2 的 ``-v`` 开关，kind=``cover``）
    属于视频作品，只按 kind 会写成 ``/note/`` 而打不开。因此调用方应把「同一作品
    下是否存在 video 文件」传进来（:func:`apply_import` 已按作品分组计算）；不传时
    退化为只按 kind 判断。

    **没有作品 ID 就返回 None，绝不造伪链接**：历史素材宁可留空，也不能写一个
    打不开的地址（前端据此决定是否显示「原始链接」）。

    Args:
        item: 已解析文件。
        is_video_work: 该文件所属作品是否为视频作品（同组内有 video 文件）。

    Returns:
        抖音原帖 URL；无作品 ID 返回 None。
    """
    if not item.aweme_id:
        return None
    is_video = is_video_work or item.kind == "video"
    return f"https://www.douyin.com/{'video' if is_video else 'note'}/{item.aweme_id}"


@dataclass
class ImportDecision:
    """单个文件的导入决策。"""

    item: ParsedFile
    action: str  # import / skip
    reason: str  # 跳过原因（action=import 时为空串）
    platform_id: str
    content_hash: str
    blogger_id: int | None = None  # 匹配到的库内博主（未匹配为 None）


TRASH_SKIP_REASON = "已在垃圾桶（不重新导入）"


def _skip_reason(
    item: ParsedFile,
    digest: str,
    dedup: DedupIndex,
    seen_hashes: set[str],
    skip_live: bool,
) -> str:
    """返回跳过原因（空串＝可以导入）；判据顺序即优先级。"""
    if skip_live and item.kind == "live":
        return "按 --skip-live 跳过 live 分段"
    if digest in dedup.live_hashes:
        return "已在库（内容相同）"
    if digest in dedup.trash_hashes:
        return TRASH_SKIP_REASON
    if digest in seen_hashes:
        return "批次内重复（同内容已处理）"
    # 平台 ID 判重必须认「两套口径」：库里的历史素材存的是文件名哈希 ID，
    # 新模板重下算出的是真实作品 ID，只比对新 ID 会漏判 → 重复入库
    for platform_id in platform_ids_for(item):
        if platform_id in dedup.live_platform_ids:
            return "已在库（平台 ID 命中）"
        if platform_id in dedup.trash_platform_ids:
            return TRASH_SKIP_REASON
    return ""


def build_import_plan(
    files: list[ParsedFile],
    dedup: DedupIndex,
    bloggers: dict[str, list[dict]] | None = None,
    authors: set[str] | None = None,
    limit: int | None = None,
    skip_live: bool = False,
    hash_cache: HashCache | dict[Path, str] | None = None,
) -> tuple[list[ImportDecision], dict[str, int], int]:
    """决定每个文件「导入 / 跳过」——去重判据集中于此（纯函数，便于单测）。

    去重分层（顺序即优先级，命中即跳过）：

    1. **内容判重**：文件 SHA-256 命中库内 ``inspirations.content_hash``
       （库内素材哈希覆盖率 100%，这是主判据，跨来源也有效）
    2. **垃圾桶判重**：同一内容已被用户丢进垃圾桶 → 跳过（要恢复请用垃圾桶还原）
    3. **批次内判重**：同一批次里相同内容只入一次（重复下载 / 多目录同一文件）
    4. **平台 ID 判重**：合成平台 ID 命中库内 ``source_platform_id``
       （幂等兜底：即使哈希口径变化，重复运行也不会重复入库）
    5. **参数过滤**：``--authors`` 只导指定作者、``--limit`` 限制作品数、
       ``--skip-live`` 跳过 live 分段

    作品维度：只要该作品还有待导入文件，就消耗一个 ``--limit`` 配额；
    同一作品的全部文件共用同一博主关联。

    Args:
        files: 已解析文件列表。
        dedup: 库内标识集合（未删除 + 垃圾桶，见 :func:`load_dedup_index`）。
        bloggers: 归一化博主名 → 博主列表（用于绑定；仅唯一候选才自动绑）。
        authors: 只导入这些作者（归一化名或目录名），None 表示全部。
        limit: 最多导入多少个作品。
        skip_live: 是否跳过 live 实况分段视频。
        hash_cache: 文件哈希缓存（:class:`HashCache` 落盘 / dict 进程内）。

    Returns:
        (决策列表, 跳过原因计数, 因超出 limit 未处理的作品数)
    """
    bloggers = bloggers or {}
    # 注意：authors 用 `is not None` 判定——空集合表示「一个都不要」，不是「不过滤」
    # （过滤链路会用空集合表达「白名单里没有可处理的作者」）
    wanted = {normalize_author(a) for a in authors} if authors is not None else None

    decisions: list[ImportDecision] = []
    skipped: Counter = Counter()
    seen_hashes: set[str] = set()
    accepted_works = 0
    deferred_works = 0

    for _work_key, items in group_works(files).items():
        # 单文件哈希失败（扫描后被删除/移动、权限问题）只跳过该文件，不让整批
        # 计划崩溃：下载目录是「活的」，扫描与哈希之间文件消失是可能发生的，
        # 而一个文件读不出来不该让整次导入失败
        digests: dict[Path, str] = {}
        for item in items:
            try:
                digests[item.path] = _digest(item.path, hash_cache)
            except OSError:
                skipped["文件读取失败（扫描后消失？）"] += 1
        items = [item for item in items if item.path in digests]
        if not items:
            continue

        first = items[0]
        if wanted is not None and first.author_key not in wanted and first.author_dir not in wanted:
            skipped["作者不在指定范围（--authors / 已登记博主）"] += len(items)
            continue

        reasons = [
            _skip_reason(item, digests[item.path], dedup, seen_hashes, skip_live)
            for item in items
        ]
        if not any(not r for r in reasons):
            for item, reason in zip(items, reasons, strict=False):
                skipped[reason] += 1
                decisions.append(ImportDecision(
                    item=item, action="skip", reason=reason,
                    platform_id=platform_id_for(item), content_hash=digests[item.path],
                ))
            continue

        if limit is not None and accepted_works >= limit:
            for _ in items:
                skipped["超出 --limit 未处理"] += 1
            deferred_works += 1
            continue
        accepted_works += 1

        matched = bloggers.get(first.author_key) or []
        blogger_id = matched[0]["id"] if len(matched) == 1 else None

        for item, reason in zip(items, reasons, strict=False):
            digest = digests[item.path]
            if reason:
                skipped[reason] += 1
                decisions.append(ImportDecision(
                    item=item, action="skip", reason=reason,
                    platform_id=platform_id_for(item), content_hash=digest,
                    blogger_id=blogger_id,
                ))
                continue
            seen_hashes.add(digest)
            decisions.append(ImportDecision(
                item=item, action="import", reason="",
                platform_id=platform_id_for(item), content_hash=digest,
                blogger_id=blogger_id,
            ))

    return decisions, dict(skipped), deferred_works


def build_plan_with_cache(
    files: list[ParsedFile],
    dedup: DedupIndex,
    hash_cache_path: Path | None = None,
    use_cache: bool = True,
    **plan_kwargs,
) -> tuple[list[ImportDecision], dict[str, int], int, dict]:
    """在**当前线程内**建缓存并跑 :func:`build_import_plan`（返回缓存统计）。

    抽成函数是为线程安全：:class:`HashCache` 持有 sqlite 连接，不能跨线程使用，
    因此后台任务在执行线程里调用本函数，缓存的创建 / 写入 / 落盘都发生在同一线程。

    Args:
        files: 已解析文件列表。
        dedup: 库内标识集合。
        hash_cache_path: 缓存文件路径（缺省 :func:`default_hash_cache_path`）。
        use_cache: False 时完全不用缓存（每次全量重算，用于核对）。
        **plan_kwargs: 透传给 :func:`build_import_plan` 的其余参数。

    Returns:
        (决策列表, 跳过原因计数, 未处理作品数, 缓存统计)。
    """
    cache, cache_error = (None, "") if not use_cache else open_hash_cache(hash_cache_path)
    if cache is None:
        decisions, skipped, deferred = build_import_plan(
            files, dedup, hash_cache=None, **plan_kwargs
        )
        stats = {"error": cache_error} if cache_error else {}
        return decisions, skipped, deferred, stats
    try:
        decisions, skipped, deferred = build_import_plan(
            files, dedup, hash_cache=cache, **plan_kwargs
        )
        cache.flush()  # 先显式落盘：写入失败才会出现在 stats 里
        stats = cache.stats()
    finally:
        cache.close()
    return decisions, skipped, deferred, stats
