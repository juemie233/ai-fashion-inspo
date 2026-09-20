"""扫描报表构建与打印（自 scripts/import_f2_downloads.py 拆出，行为不变）。"""

import sys
from collections import Counter, defaultdict
from pathlib import Path

# 与 backend/scripts 下其它脚本一致：把 backend 加入 sys.path，便于模块方式执行
sys.path.insert(0, str(Path(__file__).parent.parent))

from .f2_common import (  # noqa: E402
    BIG_GALLERY_THRESHOLD,
    DEFAULT_SEC_PER_TAG,
    ParsedFile,
    group_works,
    normalize_author,
)
from .f2_hash_cache import (  # noqa: E402
    HashCache,
    _digest,
)
from .f2_plan import DedupIndex  # noqa: E402


# ═══════════════════════════════════════════════════════════════
#  报表
# ═══════════════════════════════════════════════════════════════


def build_report(
    root: Path,
    files: list[ParsedFile],
    dedup: DedupIndex,
    bloggers: dict[str, list[dict]],
    hash_cache: HashCache | dict[Path, str] | None = None,
    sec_per_tag: float = DEFAULT_SEC_PER_TAG,
) -> dict:
    """构建扫描报表（不改动任何数据）。

    Args:
        root: 扫描根目录。
        files: :func:`scan_directory` 的结果。
        dedup: 库内标识集合（未删除 + 垃圾桶）。
        bloggers: :func:`load_douyin_bloggers` 的结果。
        hash_cache: 文件哈希缓存（:class:`HashCache` 落盘 / dict 进程内）。
        sec_per_tag: 打标耗时估算（秒/张）。

    Returns:
        报表字典（供 :func:`print_report` 打印或落 JSON）。
    """
    works = group_works(files)

    kind_stat: dict[str, Counter] = defaultdict(Counter)
    author_stat: dict[str, Counter] = defaultdict(Counter)
    work_new_count = 0
    work_all_old = 0
    works_with_trash = 0
    new_files = old_files = trash_files = 0
    new_bytes = old_bytes = trash_bytes = 0
    read_failed = 0
    hashtag_counter: Counter = Counter()
    gallery_dist: Counter = Counter()
    new_work_rows: list[dict] = []

    for work_key, items in works.items():
        new_items = []
        work_trash = 0
        for item in items:
            try:
                digest = _digest(item.path, hash_cache)
            except OSError:
                # 单文件读不出来（扫描后消失 / 权限问题）不该让整张报表崩掉：
                # 跳过该文件并单独计数，其余统计照常
                read_failed += 1
                continue
            is_live = digest in dedup.live_hashes
            is_trash = not is_live and digest in dedup.trash_hashes
            bucket = "已入库" if is_live else ("已在垃圾桶" if is_trash else "净新增")
            kind_stat[item.kind][bucket] += 1
            author_stat[item.author_dir][bucket] += 1
            if is_live:
                old_files += 1
                old_bytes += item.size
            elif is_trash:
                work_trash += 1
                trash_files += 1
                trash_bytes += item.size
            else:
                new_items.append(item)
                new_files += 1
                new_bytes += item.size
                for tag in item.hashtags:
                    hashtag_counter[tag] += 1
        if work_trash:
            works_with_trash += 1

        if new_items:
            work_new_count += 1
            gallery_imgs = sum(1 for f in items if f.kind == "image")
            if gallery_imgs == 0:
                gallery_dist["纯视频/实况（无图）"] += 1
            elif gallery_imgs == 1:
                gallery_dist["1 张"] += 1
            elif gallery_imgs <= 4:
                gallery_dist["2-4 张"] += 1
            elif gallery_imgs <= 9:
                gallery_dist["5-9 张"] += 1
            elif gallery_imgs <= 19:
                gallery_dist["10-19 张"] += 1
            else:
                gallery_dist["20+ 张"] += 1
            new_work_rows.append(
                {
                    "work_key": work_key,
                    "author_dir": new_items[0].author_dir,
                    "author_key": new_items[0].author_key,
                    "created": new_items[0].created,
                    "caption": new_items[0].caption[:60],
                    "new_files": len(new_items),
                    "total_files": len(items),
                    "gallery_imgs": gallery_imgs,
                }
            )
        else:
            work_all_old += 1

    # 作者维度：归一化后的匹配情况（同一作者被拆成多个目录时合并展示）
    authors: list[dict] = []
    for author_dir, counter in sorted(
        author_stat.items(),
        key=lambda x: -(x[1]["净新增"] + x[1]["已入库"] + x[1]["已在垃圾桶"]),
    ):
        key = normalize_author(author_dir)
        authors.append(
            {
                "author_dir": author_dir,
                "author_key": key,
                "new": counter["净新增"],
                "old": counter["已入库"],
                "trash": counter["已在垃圾桶"],
                "blogger_matched": [b["name"] for b in bloggers.get(key, [])],
                "blogger_candidates": len(bloggers.get(key, [])),
            }
        )

    # 打标成本三档
    per_file = new_files * sec_per_tag
    per_work = work_new_count * sec_per_tag
    big_rows = [r for r in new_work_rows if r["gallery_imgs"] >= BIG_GALLERY_THRESHOLD]
    big_imgs = sum(r["gallery_imgs"] for r in big_rows)
    hybrid = (work_new_count - len(big_rows)) * sec_per_tag + big_imgs * sec_per_tag

    matched_authors = {a["author_key"] for a in authors if a["blogger_candidates"]}
    return {
        "root": str(root),
        "files_total": len(files),
        "works_total": len(works),
        "works_with_new": work_new_count,
        "works_all_in_library": work_all_old,
        "works_with_trash": works_with_trash,
        "files_new": new_files,
        "files_in_library": old_files,
        "files_in_trash": trash_files,
        "files_read_failed": read_failed,
        "bytes_new": new_bytes,
        "bytes_in_library": old_bytes,
        "bytes_in_trash": trash_bytes,
        "kind_stat": {k: dict(v) for k, v in kind_stat.items()},
        "authors": authors,
        "unmatched_authors": sorted(
            {a["author_dir"] for a in authors if not a["blogger_candidates"]}
        ),
        "matched_author_keys": sorted(matched_authors),
        "gallery_dist": dict(gallery_dist),
        "top_hashtags": hashtag_counter.most_common(30),
        "hashtags_total": len(hashtag_counter),
        "tag_cost": {
            "per_file_count": new_files,
            "per_file_hours": round(per_file / 3600, 1),
            "per_work_count": work_new_count,
            "per_work_hours": round(per_work / 3600, 1),
            "hybrid_count": (work_new_count - len(big_rows)) + big_imgs,
            "hybrid_hours": round(hybrid / 3600, 1),
            "big_gallery_works": len(big_rows),
            "big_gallery_images": big_imgs,
        },
    }


def print_report(report: dict, top_hashtags: int = 15, cache_stats: dict | None = None) -> None:
    """打印人类可读的报表。

    Args:
        report: :func:`build_report` 的结果。
        top_hashtags: 展示的热门话题条数。
        cache_stats: :meth:`HashCache.stats` 的结果（说明本次哈希成本）。
    """
    gib = 1024**3
    print("\n=== f2 下载目录扫描报表（只读，未改动素材库）===")
    print(f"扫描根目录: {report['root']}")
    print(
        f"文件 {report['files_total']} 个 / 作品 {report['works_total']} 个"
        f"（含净新增的作品 {report['works_with_new']} 个，"
        f"全部已在库 {report['works_all_in_library']} 个）"
    )
    hit = report["files_in_library"]
    total = report["files_total"] or 1
    print(
        f"净新增 {report['files_new']} 个文件（{report['bytes_new']/gib:.2f} GB）"
        f"；已在库 {hit} 个（{report['bytes_in_library']/gib:.2f} GB，命中率 {hit/total*100:.1f}%）"
    )
    if report["files_in_trash"]:
        print(
            f"已在垃圾桶 {report['files_in_trash']} 个文件"
            f"（{report['bytes_in_trash']/gib:.2f} GB，涉及 {report['works_with_trash']} 个作品）"
            "——不会重新导入；如需恢复请到「垃圾桶」还原"
        )
    if report.get("files_read_failed"):
        print(
            f"⚠ {report['files_read_failed']} 个文件读取失败（扫描后消失或权限问题），已跳过"
            "——不计入上面的净新增/已在库统计"
        )
    if cache_stats:
        print(
            f"哈希缓存：复用 {cache_stats['hit']} 次"
            f"（缓存 {cache_stats['cached_rows']} 条），本次实算 {cache_stats['computed']} 个"
            f"文件 / {cache_stats['hash_seconds']} 秒"
        )
        if cache_stats.get("error"):
            print(f"   ⚠ 哈希缓存写入失败（不影响导入，下次会重算）：{cache_stats['error']}")

    print("\n-- 文件类型分布 --")
    for kind, counter in sorted(report["kind_stat"].items()):
        new = counter.get("净新增", 0)
        old = counter.get("已入库", 0)
        trash = counter.get("已在垃圾桶", 0)
        print(
            f"   {kind:<7} 净新增 {new:>6}   已在库 {old:>6}   垃圾桶 {trash:>6}"
            f"   合计 {new+old+trash:>6}"
        )

    print("\n-- 净新增作品的文件构成 --")
    for label, count in sorted(report["gallery_dist"].items(), key=lambda x: -x[1]):
        print(f"   {label:<20} {count}")

    print("\n-- 作者（前 25）--")
    print(f"   {'作者目录':<18}{'净新增':>7}{'已在库':>7}{'垃圾桶':>7}  库内博主匹配")
    for author in report["authors"][:25]:
        matched = "、".join(author["blogger_matched"]) or "❌ 未匹配（待确认）"
        print(
            f"   {author['author_dir'][:17]:<18}{author['new']:>7}{author['old']:>7}"
            f"{author.get('trash', 0):>7}  {matched}"
        )
    if report["unmatched_authors"]:
        print(
            f"   ⚠ 有 {len(report['unmatched_authors'])} 个作者目录匹配不到库内抖音博主："
            f"{'、'.join(report['unmatched_authors'][:10])}"
        )

    cost = report["tag_cost"]
    print(f"\n-- 打标成本预估（按实测 {DEFAULT_SEC_PER_TAG} 秒/张）--")
    print(f"   甲 逐文件打标:            {cost['per_file_count']:>6} 次 ≈ {cost['per_file_hours']:>5} 小时")
    print(f"   丙 按作品打一次(复制标签): {cost['per_work_count']:>6} 次 ≈ {cost['per_work_hours']:>5} 小时")
    print(
        f"   丙+ 例外(≥{BIG_GALLERY_THRESHOLD} 张作品逐图): {cost['hybrid_count']:>6} 次 ≈ {cost['hybrid_hours']:>5} 小时"
        f"（其中 {cost['big_gallery_works']} 个作品、{cost['big_gallery_images']} 张图）"
    )

    print(f"\n-- 话题（共 {report['hashtags_total']} 个，Top {top_hashtags}）--")
    for tag, count in report["top_hashtags"][:top_hashtags]:
        print(f"   #{tag}  {count}")
    print(
        "\n提示：本命令不写库（只读报表）。确认无误后执行导入："
        "\n    python -m scripts.import_f2_downloads --apply"
        "（要连增量下载一起做：--fetch --apply）"
    )
