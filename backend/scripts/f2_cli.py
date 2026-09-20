"""命令行入口（自 scripts/import_f2_downloads.py 拆出，行为不变）。"""

import argparse
import json
import re
import sys
from pathlib import Path

# 与 backend/scripts 下其它脚本一致：把 backend 加入 sys.path，便于模块方式执行
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings  # noqa: E402

from .f2_apply import apply_import  # noqa: E402
from .f2_common import (  # noqa: E402
    DEFAULT_F2_DIR,
    DEFAULT_F2_ROOT,
    DEFAULT_SEC_PER_TAG,
    F2_DOWNLOAD_SUBDIR,
    normalize_author,
    scan_directory,
)
from .f2_fetch import (  # noqa: E402
    f2_available,
    load_f2_authors,
    profile_author,
    resolve_profile_nicknames,
    run_fetch,
)
from .f2_hash_cache import (  # noqa: E402
    HashCache,
    _finish_cache,
    open_hash_cache,
)
from .f2_plan import (  # noqa: E402
    IMPORT_BATCH_DIRNAME,
    build_import_plan,
    library_db_path,
    load_dedup_index,
    load_douyin_bloggers,
    select_known_authors,
)
from .f2_report import (  # noqa: E402
    build_report,
    print_report,
)
from .f2_rollback import (  # noqa: E402
    apply_rollback,
    batch_storage_root,
    latest_batch_file,
    plan_rollback,
)


# ═══════════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════════


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 参数列表（缺省取 sys.argv[1:]）。

    Returns:
        进程退出码。
    """
    parser = argparse.ArgumentParser(
        description="f2 抖音下载目录 → 素材库：扫描报表（缺省，只读）或真导入（--apply）"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=f"f2 的 post 目录（缺省跟随 --f2-dir，即 {DEFAULT_F2_ROOT}）",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="先调 f2 增量下载（各作者主页新作品），再按所选模式处理；"
        "与 --apply 组合即为「一条命令从下载到入库」",
    )
    parser.add_argument(
        "--f2-dir",
        type=Path,
        default=DEFAULT_F2_DIR,
        help=f"f2 工作目录（其下有 Download/ 与 douyin_users.db），默认 {DEFAULT_F2_DIR}",
    )
    parser.add_argument(
        "--naming",
        default=None,
        help="传给 f2 的 -n 命名模板；缺省用含 {aweme_id} 的模板（勿去掉作品 ID：去掉后素材无法追溯原帖）",
    )
    parser.add_argument(
        "--auto-cookie",
        default=None,
        help="传给 f2 的 --auto-cookie 浏览器名（chrome/chromium/edge…），需先关闭该浏览器",
    )
    parser.add_argument(
        "--include-unknown-authors",
        action="store_true",
        help="连「未登记到博主库」的 f2 账号一起处理（默认跳过）。f2 用户库存的是"
        "它见过的所有账号，混进来的无关账号（如游戏官方号）默认不下载也不入库",
    )
    parser.add_argument(
        "--fetch-limit",
        type=int,
        default=None,
        help="--fetch 时最多下载多少个作者（试跑用）",
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=settings.f2_fetch_since_days,
        help=f"只让 f2 翻最近 N 天的作品（默认 {settings.f2_fetch_since_days} 天；"
        "0 表示翻全历史）。窗口会按「该作者上次下载时间」自动放大，长时间不跑也不漏；"
        "不给窗口时 f2 会把作者全部历史翻完且每页固定等 timeout 秒，日常增量会非常慢",
    )
    parser.add_argument(
        "--rollback",
        default=None,
        help="按批次清单回滚：传清单路径或 latest（取最新一批）。"
        "缺省只预览，加 --apply 才真删",
    )
    parser.add_argument(
        "--rollback-force",
        action="store_true",
        help="回滚时连「已被改动」的素材（有标签/收藏/评分/非 pending）一起删",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真导入（写库：入库 + 博主关联）；缺省只出报表、不写库",
    )
    parser.add_argument(
        "--authors",
        default=None,
        help="只导入这些作者（归一化名或目录名，逗号分隔），缺省全部",
    )
    parser.add_argument(
        "--profiles",
        default=None,
        help=(
            "按博主全量下载：博主主页链接或 sec_user_id（逗号/空格分隔）。"
            "只下这些博主、且不需要它们已在 f2 用户库里；首次采集自动用 -i all 翻全量"
        ),
    )
    parser.add_argument("--limit", type=int, default=None, help="最多导入多少个作品")
    parser.add_argument(
        "--no-hash-cache",
        action="store_true",
        help="不用哈希缓存（每次都重新读盘算 SHA-256；日常不必加，用于核对缓存正确性）",
    )
    parser.add_argument(
        "--skip-live", action="store_true", help="跳过 live 实况的分段视频"
    )
    parser.add_argument(
        "--no-thumbnails",
        action="store_true",
        help="不生成缩略图（更快；列表页将缺预览图）",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="素材库路径（缺省取 settings；可指向库副本先试跑）",
    )
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=None,
        help="存储根目录（缺省取 settings.storage_root；试跑时可指向临时目录）",
    )
    parser.add_argument("--json", type=Path, default=None, help="把报表/计划另存为 JSON")
    parser.add_argument("--top-hashtags", type=int, default=15, help="展示的热门话题条数")
    parser.add_argument(
        "--sec-per-tag",
        type=float,
        default=DEFAULT_SEC_PER_TAG,
        help=f"打标耗时估算，秒/张（默认 {DEFAULT_SEC_PER_TAG}）",
    )
    args = parser.parse_args(argv)

    db_path = args.db or library_db_path()
    root = args.root or (args.f2_dir / F2_DOWNLOAD_SUBDIR)

    # ── 回滚模式（只操作批次清单，不扫描 f2 目录）──
    if args.rollback:
        batch_dir = (args.storage_root or settings.storage_root) / IMPORT_BATCH_DIRNAME
        batch_file = (
            latest_batch_file(batch_dir)
            if args.rollback == "latest"
            else Path(args.rollback)
        )
        if batch_file is None or not Path(batch_file).exists():
            print(f"未找到批次清单：{batch_file or batch_dir}")
            return 1
        if not db_path.exists():
            print(f"素材库不存在: {db_path}")
            return 1
        deletable, kept = plan_rollback(
            Path(batch_file), db_path, force=args.rollback_force
        )
        print(f"\n=== 回滚预览（清单: {batch_file}）===")
        print(f"可删除 {len(deletable)} 条；保留 {len(kept)} 条")
        for item in kept[:10]:
            print(f"   保留 {item.get('file_path') or item.get('source_file')}：{item['keep_reason']}")
        if len(kept) > 10:
            print(f"   …另有 {len(kept) - 10} 条被保留")
        if not args.apply:
            print("\n提示：本命令只预览，未删除任何数据。确认后加 --apply 执行回滚。")
            return 0
        # 存储根优先级：显式传参 > 清单里记录的导入期存储根 > 当前配置。
        # 导入用过 --storage-root 时，只有清单记录的那个根才找得到文件
        recorded_root = batch_storage_root(Path(batch_file))
        if args.storage_root:
            rollback_root = args.storage_root
            root_source = "命令行 --storage-root"
        elif recorded_root:
            rollback_root = recorded_root
            root_source = "批次清单记录"
        else:
            rollback_root = settings.storage_root
            root_source = "当前配置（清单未记录）"
        print(f"  存储根: {rollback_root}（{root_source}）")
        result = apply_rollback(
            deletable, db_path=db_path, storage_root=rollback_root
        )
        print("\n=== 回滚完成 ===")
        print(
            f"删除素材 {result['deleted']} 条，删除文件 {result['removed_files']} 个，"
            f"失败 {result['failed']} 条"
        )
        for err in result["errors"][:10]:
            print(f"   ✗ {err['inspiration_id']}: {err['error']}")
        print(
            "\n注意：回滚只撤销本批入库；同样的文件在下次导入时会被**重新收录**"
            "（去重依据是库内是否还有该内容）。若要长期排除，请从 f2 目录删除对应文件，"
            "或用 --authors 收窄导入范围。"
        )
        return 0

    authors_filter = (
        {a.strip() for a in args.authors.split(",") if a.strip()}
        if args.authors
        else None
    )
    # --profiles：按博主全量下载（主页链接或 sec_user_id，逗号/空格/换行分隔）
    profile_filter = [
        p.strip()
        for p in re.split(r"[,\s]+", args.profiles or "")
        if p.strip()
    ]

    # ── 可选：先调 f2 增量下载（--fetch）──
    if args.fetch:
        if not f2_available():
            print(
                "未检测到 f2（python -m f2 不可用）：先安装并配置好 cookie —— "
                "pip install f2；本命令只做增量，首次全量请手动跑一次 f2"
            )
            return 1
        print("=== 调 f2 增量下载（逐作者串行，失败不阻断）===")
        fetch = run_fetch(
            f2_dir=args.f2_dir,
            authors=authors_filter,
            download_root=args.f2_dir / "Download",
            naming=args.naming,
            auto_cookie=args.auto_cookie,
            limit=args.fetch_limit,
            since_days=args.since_days,
            include_unknown=args.include_unknown_authors,
            profiles=profile_filter,
        )
        if fetch.get("error"):
            print(f"  ⚠ {fetch['error']}")
        else:
            print(
                f"  下载汇总：成功 {fetch['ok']} / 失败 {fetch['failed']}"
                f"（共 {fetch['total']} 个作者）"
            )
            if fetch.get("invalid_profiles"):
                print(
                    f"  ⚠ 无法识别的博主 {len(fetch['invalid_profiles'])} 个："
                    f"{'、'.join(fetch['invalid_profiles'])}"
                    "（请填完整主页链接或 sec_user_id，抖音号/短链不支持）"
                )
            if fetch.get("skipped_authors"):
                print(
                    f"  跳过未登记账号 {len(fetch['skipped_authors'])} 个："
                    f"{'、'.join(fetch['skipped_authors'])}"
                )
            if fetch["failed"]:
                print("  ⚠ 有作者下载失败（多为风控/cookie 失效），已跳过，稍后可重跑")
        print()

    files = scan_directory(root)
    if not files:
        print(f"未在 {root} 找到可识别的 f2 产物文件")
        return 1

    hash_cache: HashCache | None = None
    cache_error = ""
    if not args.no_hash_cache:
        hash_cache, cache_error = open_hash_cache()
        if hash_cache is None:
            print(f"⚠ {cache_error}")
    dedup = load_dedup_index(db_path)
    bloggers = load_douyin_bloggers(db_path)

    # ── 缺省：只读报表 ──
    if not args.apply:
        report = build_report(
            root=root,
            files=files,
            dedup=dedup,
            bloggers=bloggers,
            hash_cache=hash_cache,
            sec_per_tag=args.sec_per_tag,
        )
        cache_stats = _finish_cache(hash_cache)
        print_report(report, top_hashtags=args.top_hashtags, cache_stats=cache_stats)
        if args.json:
            args.json.write_text(
                json.dumps(
                    {**report, "hash_cache": cache_stats},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"\n报表已写入 {args.json}")
        return 0

    # ── 真导入 ──
    if not db_path.exists():
        print(f"素材库不存在: {db_path}")
        return 1

    # 未显式指定作者时把导入范围收窄到「库里已登记的博主」：下载目录里可能留着
    # 无关账号的产物（f2 用户库混进来的账号），默认不让它们入库
    # （与 web 一键获取同一条口径，见 select_known_authors）。库内一个抖音博主都
    # 没有时没有白名单依据，退回旧口径（全部导入）。
    #
    # --profiles 优先于上面两条：用户显式点名了博主，导入范围就该是「这些博主的
    # 产物」——名字要等下完 f2 把新账号写进用户库后才查得到（见
    # resolve_profile_nicknames），查不到就是没下成，交给调用方报错。
    if profile_filter:
        resolved = fetch.get("resolved_profiles") or {} if args.fetch else {}
        if not resolved:
            resolved = resolve_profile_nicknames(
                args.f2_dir,
                [a["sec_user_id"] for a in (profile_author(p) or {} for p in profile_filter) if a],
            )
        plan_authors = {normalize_author(n) for n in resolved.values() if n} or None
        if not plan_authors:
            print(
                "  ⚠ 无法确认这些博主的昵称（可能没下成）："
                f"{'、'.join(profile_filter)}"
            )
            return 1
    elif authors_filter:
        plan_authors: set[str] | None = authors_filter
    elif args.include_unknown_authors or not bloggers:
        plan_authors = None
    else:
        known, unknown = select_known_authors(load_f2_authors(args.f2_dir), bloggers)
        plan_authors = {normalize_author(a["nickname"]) for a in known}
        if unknown:
            print(
                f"   ⏭ 未登记账号的产物不入库：{'、'.join(a['nickname'] for a in unknown)}"
                "（要一起导入请加 --include-unknown-authors）"
            )

    decisions, skipped, deferred_works = build_import_plan(
        files=files,
        dedup=dedup,
        bloggers=bloggers,
        authors=plan_authors,
        limit=args.limit,
        skip_live=args.skip_live,
        hash_cache=hash_cache,
    )
    cache_stats = _finish_cache(hash_cache)
    to_import = [d for d in decisions if d.action == "import"]
    work_count = len({d.item.work_key for d in to_import})

    print(f"\n=== 导入计划（库: {db_path}）===")
    print(f"待导入 {len(to_import)} 个文件 / {work_count} 个作品")
    for reason, count in sorted(skipped.items(), key=lambda x: -x[1]):
        print(f"   跳过 {count:>6}  {reason}")
    if deferred_works:
        print(f"   因 --limit 未处理的作品 {deferred_works} 个")
    if cache_stats:
        print(
            f"   哈希缓存复用 {cache_stats['hit']} 次，本次实算 {cache_stats['computed']} 个"
            f"文件 / {cache_stats['hash_seconds']} 秒"
        )
        if cache_stats.get("error"):
            print(f"   ⚠ 哈希缓存写入失败（不影响导入）：{cache_stats['error']}")
    if not to_import:
        print("没有需要导入的文件（全部已在库或已被过滤）")
        return 0

    result = apply_import(
        to_import,
        db_path=db_path,
        storage_root=args.storage_root or settings.storage_root,
        make_thumbnails=not args.no_thumbnails,
    )
    print("\n=== 导入完成 ===")
    print(f"入库 {result['imported']} 个，失败 {result['failed']} 个")
    if result["batch_file"]:
        print(f"批次清单: {result['batch_file']}（可用于审计与回滚）")
    if result.get("batch_error"):
        # 清单缺失 = 这批无法回滚，必须醒目（此前被混进「失败」计数里）
        print(
            f"\n⚠ 批次清单写入失败：{result['batch_error']}\n"
            f"  素材已入库，但**本批无法用 --rollback 撤销**；如需撤销请手工按 "
            f"storage/import_batches 下相邻批次或素材创建时间处理。"
        )
    for err in result["errors"][:10]:
        print(f"   ✗ {err['source_file']}: {err['error']}")
    print(
        "\n注意：按约定本次导入**不做标签分析、不建向量**——素材已入库但未打标，"
        "语义搜索/相似推荐要等打标后才可用；打标请用现有「批量分析任务」一键触发。"
    )

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "db": str(db_path),
                    "planned": len(to_import),
                    "works": work_count,
                    "skipped": skipped,
                    "deferred_works": deferred_works,
                    "hash_cache": cache_stats,
                    "result": {
                        k: v for k, v in result.items() if k != "ids"
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n导入计划与结果已写入 {args.json}")
    return 0
