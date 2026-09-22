"""f2 抖音下载目录 → 素材库：扫描与报表（本提交只做只读扫描，不写库）。

背景
----
本地用 f2（Johnserf-Seed/f2）下载抖音作品，产物落在：

    <f2 根目录>/Download/douyin/post/{作者目录}/

文件名由 f2 的命名模板生成，形如：

    {创建时间}_{正文}_image_{序号}.webp     ← 图文作品的第 N 张图
    {创建时间}_{正文}_video.mp4            ← 视频作品
    {创建时间}_{正文}_live_{序号}.mp4       ← live 实况的分段视频
    {创建时间}_{正文}_*.tmp                ← 未下载完的残file（必须跳过）

因为「同一作品的多个文件共享同一前缀」，不需要 aweme_id 也能把图集的多张图
归为一个作品——这是本模块做「作品级统计 / 按作品打标」的基础。

去重口径
--------
以**文件内容 SHA-256** 与库内 `inspirations.content_hash` 比对（实测库内
10,946 条素材的 content_hash 覆盖率 100%），不依赖路径或文件名。

**垃圾桶里的素材同样算「已存在」**：软删除（`deleted_at` 非空）的素材哈希也参与
判重，理由见 :func:`load_dedup_index`——垃圾桶是负样本来源，被丢弃的内容不该在
下次（尤其是每日自动）导入时被原样搬回来；要恢复请走垃圾桶还原。
注意：垃圾桶被**彻底清空**后行已不存在，此时若下载目录里文件还在，仍会被重新导入。

哈希成本
--------
判重依赖对整个下载目录算 SHA-256（实测 15,350 文件 / 7.28 GB 约 81 秒），因此
引入 :class:`HashCache`（落盘缓存，按路径 + size + mtime 判文件未变则复用哈希），
日常运行只算新增文件。

本模块当前只实现：
  - 目录扫描与文件名解析（作品分组、正文、#话题、创建时间）
  - 与素材库的重合面统计（净新增 / 已在库）
  - 待建博主、话题清单、打标成本预估等决策报表

本模块提供两件事：**看清会进来什么**（缺省只读报表）与**一键弄进来**
（`--apply` 入库；`--fetch` 连 f2 增量下载一起做）。

用法
----
    cd backend
    # 「一键获取素材」：增量下载 → 入库（推荐日常用这条）
    python -m scripts.import_f2_downloads --fetch --apply

    python -m scripts.import_f2_downloads --dry-run            # 只出报表（缺省行为，只读）
    python -m scripts.import_f2_downloads --apply              # 只入库（不调 f2）
    python -m scripts.import_f2_downloads --apply --authors 里香,娜娜瑜 --limit 500
    python -m scripts.import_f2_downloads --fetch --fetch-limit 2   # 只下载 2 个作者试跑
    python -m scripts.import_f2_downloads --apply --no-hash-cache    # 忽略哈希缓存重算

三条约定
--------
1. **导入不做标签分析**：本模块绝不调用 analyze_image，也不建向量。素材入库后
   处于「未打标」状态，由现有的批量分析任务（一键）按需补——一次性 1.2 万张
   要 8~41 小时 GPU，不该卡住「获取素材」这一步。
2. **必须去重**（见 :func:`build_import_plan` 的五层判据），且重复运行幂等；
   垃圾桶里的内容同样不重新导入（见 :func:`load_dedup_index`）。
3. **不写话题存档表**（``scraper_hashtags``）：正文里的 ``#话题`` 已经随
   ``caption`` 落库，而 caption 参与文本向量（TEXT_EMBEDDING_FORMULA_VERSION=2），
   语义搜索能命中，**零信息损失**；而话题库在 UI 侧只对小红书显示、唯一用途是
   给「定时采集计划」提供候选关键词，与抖音素材入库无交集。故本路径不写入，
   避免产生一批无处可用的数据。

模块拆分
--------
本文件原为 2891 行长文件，现只做**符号转发**：真正的实现分布在同目录的
``f2_common`` / ``f2_hash_cache`` / ``f2_plan`` / ``f2_report`` / ``f2_apply`` /
``f2_fetch`` / ``f2_rollback`` / ``f2_cli``。对外接口（模块名、函数名）保持不变。

⚠ monkeypatch 要打到**符号归属的子模块**（例如 ``scripts.f2_fetch.f2_available``、
``scripts.f2_common.DEFAULT_F2_DIR``）：本模块里的同名属性只是转发副本，
改它不会影响子模块内部的调用点。
"""

import sys

from .f2_common import (  # noqa: F401
    AUTO_BLOGGER_SOURCE,
    BIG_GALLERY_THRESHOLD,
    COLLECT_NAMING_TEMPLATE,
    DEFAULT_F2_COLLECT_ROOT,
    DEFAULT_F2_DIR,
    DEFAULT_F2_LIKE_ROOT,
    DEFAULT_F2_ROOT,
    DEFAULT_FETCH_SINCE_DAYS,
    DEFAULT_SEC_PER_TAG,
    F2_AUTHOR_DB,
    F2_COLLECT_SUBDIR,
    F2_DOWNLOAD_SUBDIR,
    F2_LIKE_SUBDIR,
    HASHTAG_RE,
    KIND_RE,
    KIND_RE_WITH_ID,
    KIND_TO_MEDIA,
    LIKE_NAMING_TEMPLATE,
    MERGE_TMP_SUFFIX,
    POST_NAMING_TEMPLATE,
    ParsedFile,
    TMP_EXT,
    WINDOW_MARGIN_DAYS,
    download_tree_stats,
    group_works,
    merge_personal_duplicates,
    normalize_author,
    parse_media_filename,
    scan_directory,
)
from .f2_hash_cache import (  # noqa: F401
    HASH_CACHE_DBNAME,
    HashCache,
    _digest,
    _finish_cache,
    default_hash_cache_path,
    open_hash_cache,
    sha256_file,
)
from .f2_plan import (  # noqa: F401
    AUTHOR_MATCH_CONFIDENCE,
    DedupIndex,
    IMPORT_BATCH_DIRNAME,
    ImportDecision,
    TRASH_SKIP_REASON,
    _skip_reason,
    _split_by_trash,
    build_import_plan,
    build_plan_with_cache,
    legacy_platform_id_for,
    library_db_path,
    load_dedup_index,
    load_douyin_bloggers,
    match_authors,
    platform_id_for,
    platform_ids_for,
    select_known_authors,
    source_url_for,
    work_hash,
)
from .f2_report import (  # noqa: F401
    build_report,
    print_report,
)
from .f2_apply import (  # noqa: F401
    INSERT_F2_SQL,
    apply_import,
)
from .f2_fetch import (  # noqa: F401
    _SEC_USER_ID_RE,
    _default_runner,
    author_last_download,
    build_f2_command,
    build_f2_collect_command,
    build_f2_like_command,
    compute_fetch_interval,
    compute_profile_interval,
    f2_available,
    like_user_url,
    load_f2_authors,
    load_f2_profiles,
    profile_author,
    profile_display,
    resolve_profile_nicknames,
    run_fetch,
    run_fetch_collects,
    run_fetch_likes,
)
from .f2_rollback import (  # noqa: F401
    _table_exists,
    apply_rollback,
    batch_storage_root,
    latest_batch_file,
    load_batch_manifest,
    plan_rollback,
)
from .f2_cli import (  # noqa: F401
    main,
)
from app.config import settings  # noqa: F401
from app.services.file_service import _generate_image_thumbnail_sync, validate_media  # noqa: F401
from .scraper_common import utcnow  # noqa: F401
from .scraper_download import extract_video_thumbnail_sync  # noqa: F401

__all__ = [
    "AUTHOR_MATCH_CONFIDENCE",
    "AUTO_BLOGGER_SOURCE",
    "BIG_GALLERY_THRESHOLD",
    "DEFAULT_F2_COLLECT_ROOT",
    "DEFAULT_F2_DIR",
    "DEFAULT_F2_LIKE_ROOT",
    "DEFAULT_F2_ROOT",
    "DEFAULT_FETCH_SINCE_DAYS",
    "DEFAULT_SEC_PER_TAG",
    "DedupIndex",
    "F2_AUTHOR_DB",
    "F2_DOWNLOAD_SUBDIR",
    "F2_COLLECT_SUBDIR",
    "F2_LIKE_SUBDIR",
    "HASHTAG_RE",
    "HASH_CACHE_DBNAME",
    "HashCache",
    "IMPORT_BATCH_DIRNAME",
    "INSERT_F2_SQL",
    "ImportDecision",
    "KIND_RE",
    "KIND_RE_WITH_ID",
    "KIND_TO_MEDIA",
    "COLLECT_NAMING_TEMPLATE",
    "LIKE_NAMING_TEMPLATE",
    "MERGE_TMP_SUFFIX",
    "POST_NAMING_TEMPLATE",
    "ParsedFile",
    "TMP_EXT",
    "TRASH_SKIP_REASON",
    "WINDOW_MARGIN_DAYS",
    "apply_import",
    "apply_rollback",
    "author_last_download",
    "batch_storage_root",
    "build_f2_collect_command",
    "build_f2_command",
    "build_f2_like_command",
    "build_import_plan",
    "build_plan_with_cache",
    "build_report",
    "compute_fetch_interval",
    "compute_profile_interval",
    "default_hash_cache_path",
    "download_tree_stats",
    "f2_available",
    "group_works",
    "latest_batch_file",
    "legacy_platform_id_for",
    "library_db_path",
    "like_user_url",
    "load_batch_manifest",
    "load_dedup_index",
    "load_douyin_bloggers",
    "load_f2_authors",
    "load_f2_profiles",
    "main",
    "match_authors",
    "merge_personal_duplicates",
    "normalize_author",
    "open_hash_cache",
    "parse_media_filename",
    "plan_rollback",
    "platform_id_for",
    "platform_ids_for",
    "print_report",
    "profile_author",
    "profile_display",
    "resolve_profile_nicknames",
    "run_fetch",
    "run_fetch_collects",
    "run_fetch_likes",
    "scan_directory",
    "select_known_authors",
    "sha256_file",
    "source_url_for",
    "work_hash",
]

if __name__ == "__main__":
    sys.exit(main())
