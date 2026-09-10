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

本模块当前只实现：
  - 目录扫描与文件名解析（作品分组、正文、#话题、创建时间）
  - 与素材库的重合面统计（净新增 / 已在库）
  - 待建博主、话题清单、打标成本预估等决策报表

真导入（写库、绑定博主、话题存档）在下一个提交实现。

用法
----
    cd backend
    py scripts/import_f2_downloads.py --dry-run
    py scripts/import_f2_downloads.py --dry-run --json report.json
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

# 与 backend/scripts 下其它脚本一致：把 backend 加入 sys.path，便于直接执行
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings  # noqa: E402

# ═══════════════════════════════════════════════════════════════
#  常量
# ═══════════════════════════════════════════════════════════════

"""f2 默认下载根目录（可用 --root 覆盖）。"""
DEFAULT_F2_ROOT = Path(r"C:\Users\Administrator\Desktop\f2\Download\douyin\post")

"""文件名尾部类型标记：_video / _image_1 / _live_2（f2 命名模板决定）。"""
KIND_RE = re.compile(
    r"^(?P<created>\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2})_"
    r"(?P<body>.+?)_(?P<kind>video|image|live|music|cover|lyric)"
    r"(?:_(?P<index>\d+))?$",
    re.I,
)

"""f2 下载未完成时留下的临时文件扩展名（含 0 字节残file，必须跳过）。"""
TMP_EXT = ".tmp"

"""作品类型 → 素材库 media_type。"""
KIND_TO_MEDIA = {
    "image": "image",
    "video": "video",
    "live": "video",
    "cover": "image",
    "music": None,  # 原声不入素材库
    "lyric": None,
}

"""正文里的 #话题（f2 用下划线分隔话题，故以 _ 或空白作边界）。"""
HASHTAG_RE = re.compile(r"#([^\s#_，,。！？.!?]{1,30})")

"""实测打标平均耗时（秒/张），取自 ai_analysis_log；可用 --sec-per-tag 覆盖。"""
DEFAULT_SEC_PER_TAG = 12.5

"""图集「逐图打标」还是「按作品打一次」的分档阈值（张）。"""
BIG_GALLERY_THRESHOLD = 10


# ═══════════════════════════════════════════════════════════════
#  文件名解析（纯函数，便于单测）
# ═══════════════════════════════════════════════════════════════


@dataclass
class ParsedFile:
    """一个已解析的 f2 产物文件。"""

    path: Path
    author_dir: str  # 作者目录名（原始，可能带 √ / 数字后缀）
    author_key: str  # 归一化作者名（用于匹配库内博主、合并被拆分的目录）
    work_key: str  # 作品键（作者 + 创建时间 + 正文）——同一作品的多个文件共享
    created: str  # 创建时间（YYYY-MM-DD HH-MM-SS）
    body: str  # 正文原文（含 #话题，f2 用 _ 替代非法字符）
    kind: str  # image / video / live / ...
    index: int  # 图集序号（无序号为 0）
    media_type: str  # 入库用 media_type（image / video）；空串表示不入库
    size: int = 0

    @property
    def caption(self) -> str:
        """正文转成 caption：下划线还原为空格，压缩连续空白。"""
        return re.sub(r"\s+", " ", self.body.replace("_", " ")).strip()

    @property
    def hashtags(self) -> list[str]:
        """正文里的 #话题列表（去重保序）。"""
        seen: list[str] = []
        for tag in HASHTAG_RE.findall(self.body):
            tag = tag.strip()
            if tag and tag not in seen:
                seen.append(tag)
        return seen


def normalize_author(name: str) -> str:
    """归一化作者目录名，用于跨目录合并与库内博主匹配。

    f2 遇到同名作者会拆出 `里香1√` / `里香2√` 这样的目录，并在下载完成的
    账号后加 `√`；库里的博主名则是 `里香`。规则：去掉尾部 `√`、数字、
    下划线、点与空白，其余保留。

    Args:
        name: 作者目录名（或库内博主名）。

    Returns:
        归一化后的名字（可能为空串）。
    """
    return re.sub(r"[√\s_\.0-9]+$", "", (name or "").strip())


def parse_media_filename(path: Path, author_dir: str = "") -> ParsedFile | None:
    """解析 f2 产物文件名；不符合命名规则（残file等）返回 None。

    Args:
        path: 文件路径。
        author_dir: 所属作者目录名（缺省取 path 的父目录名）。

    Returns:
        ParsedFile；文件名不含 f2 类型标记时返回 None。
    """
    if path.suffix.lower() == TMP_EXT:
        return None
    match = KIND_RE.match(path.stem)
    if not match:
        return None
    kind = match.group("kind").lower()
    media_type = KIND_TO_MEDIA.get(kind)
    if media_type is None:
        return None  # 原声/歌词等不属素材
    author = author_dir or path.parent.name
    created = match.group("created")
    body = match.group("body")
    return ParsedFile(
        path=path,
        author_dir=author,
        author_key=normalize_author(author),
        work_key=f"{author}||{created}||{body}",
        created=created,
        body=body,
        kind=kind,
        index=int(match.group("index") or 0),
        media_type=media_type,
        size=path.stat().st_size if path.exists() else 0,
    )


def scan_directory(root: Path) -> list[ParsedFile]:
    """递归扫描 f2 下载目录，返回可入库的已解析文件列表。

    Args:
        root: f2 的 post 目录（…/Download/douyin/post）。

    Returns:
        解析成功的文件列表（残file、非媒体文件、无法识别命名的文件被跳过）。
    """
    parsed: list[ParsedFile] = []
    if not root.exists():
        return parsed
    for author_path in sorted(p for p in root.iterdir() if p.is_dir()):
        for file_path in sorted(author_path.iterdir()):
            if not file_path.is_file():
                continue
            item = parse_media_filename(file_path, author_path.name)
            if item is not None:
                parsed.append(item)
    return parsed


def group_works(files: list[ParsedFile]) -> dict[str, list[ParsedFile]]:
    """按作品键分组（同一作品的多图/多段视频归到一组）。

    Args:
        files: 已解析文件列表。

    Returns:
        作品键 → 该作品的文件列表（组内按类型/序号排序，保证首图稳定）。
    """
    works: dict[str, list[ParsedFile]] = defaultdict(list)
    for item in files:
        works[item.work_key].append(item)
    for items in works.values():
        items.sort(key=lambda f: (f.kind != "image", f.index, f.path.name))
    return dict(works)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """流式计算文件 SHA-256（分块读取，避免整文件驻留内存）。

    Args:
        path: 文件路径。
        chunk: 分块大小（字节）。

    Returns:
        十六进制哈希字符串。
    """
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(chunk):
            hasher.update(block)
    return hasher.hexdigest()


# ═══════════════════════════════════════════════════════════════
#  素材库侧读取（只读）
# ═══════════════════════════════════════════════════════════════


def library_db_path() -> Path:
    """素材库 SQLite 路径（与采集脚本、conftest 口径一致）。"""
    return settings.storage_root.parent / "fashion_inspo.db"


def load_library_hashes(db_path: Path | None = None) -> set[str]:
    """读取库内未删除素材的 content_hash 集合（去重判据）。

    Args:
        db_path: 数据库路径（缺省用 :func:`library_db_path`）。

    Returns:
        content_hash 集合；数据库不存在时返回空集合。
    """
    path = db_path or library_db_path()
    if not path.exists():
        return set()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT content_hash FROM inspirations "
                "WHERE deleted_at IS NULL AND content_hash IS NOT NULL"
            )
        }
    finally:
        conn.close()


def load_douyin_bloggers(db_path: Path | None = None) -> dict[str, list[dict]]:
    """读取库内抖音博主，按归一化名索引（用于作品绑定博主）。

    Args:
        db_path: 数据库路径（缺省用 :func:`library_db_path`）。

    Returns:
        归一化名 → [{"id", "name"}]（同名可能多条，故用列表）。
    """
    path = db_path or library_db_path()
    if not path.exists():
        return {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, name FROM bloggers WHERE platform = 'douyin'"
        ).fetchall()
    finally:
        conn.close()
    indexed: dict[str, list[dict]] = defaultdict(list)
    for blogger_id, name in rows:
        indexed[normalize_author(name)].append({"id": blogger_id, "name": name})
    return dict(indexed)


# ═══════════════════════════════════════════════════════════════
#  报表
# ═══════════════════════════════════════════════════════════════


def build_report(
    root: Path,
    files: list[ParsedFile],
    library_hashes: set[str],
    bloggers: dict[str, list[dict]],
    hash_cache: dict[Path, str] | None = None,
    sec_per_tag: float = DEFAULT_SEC_PER_TAG,
) -> dict:
    """构建扫描报表（不改动任何数据）。

    Args:
        root: 扫描根目录。
        files: :func:`scan_directory` 的结果。
        library_hashes: 库内 content_hash 集合。
        bloggers: :func:`load_douyin_bloggers` 的结果。
        hash_cache: 文件哈希缓存（重复调用时复用，避免重复读盘）。
        sec_per_tag: 打标耗时估算（秒/张）。

    Returns:
        报表字典（供 :func:`print_report` 打印或落 JSON）。
    """
    cache = hash_cache if hash_cache is not None else {}
    works = group_works(files)

    kind_stat: dict[str, Counter] = defaultdict(Counter)
    author_stat: dict[str, Counter] = defaultdict(Counter)
    work_new_count = 0
    work_all_old = 0
    new_files = old_files = 0
    new_bytes = old_bytes = 0
    hashtag_counter: Counter = Counter()
    gallery_dist: Counter = Counter()
    new_work_rows: list[dict] = []

    for work_key, items in works.items():
        new_items = []
        for item in items:
            digest = cache.get(item.path)
            if digest is None:
                digest = sha256_file(item.path)
                cache[item.path] = digest
            is_new = digest not in library_hashes
            bucket = "已入库" if not is_new else "净新增"
            kind_stat[item.kind][bucket] += 1
            author_stat[item.author_dir][bucket] += 1
            if is_new:
                new_items.append(item)
                new_files += 1
                new_bytes += item.size
                for tag in item.hashtags:
                    hashtag_counter[tag] += 1
            else:
                old_files += 1
                old_bytes += item.size

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
        author_stat.items(), key=lambda x: -(x[1]["净新增"] + x[1]["已入库"])
    ):
        key = normalize_author(author_dir)
        authors.append(
            {
                "author_dir": author_dir,
                "author_key": key,
                "new": counter["净新增"],
                "old": counter["已入库"],
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
        "files_new": new_files,
        "files_in_library": old_files,
        "bytes_new": new_bytes,
        "bytes_in_library": old_bytes,
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


def print_report(report: dict, top_hashtags: int = 15) -> None:
    """打印人类可读的报表。

    Args:
        report: :func:`build_report` 的结果。
        top_hashtags: 展示的热门话题条数。
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

    print("\n-- 文件类型分布 --")
    for kind, counter in sorted(report["kind_stat"].items()):
        new = counter.get("净新增", 0)
        old = counter.get("已入库", 0)
        print(f"   {kind:<7} 净新增 {new:>6}   已在库 {old:>6}   合计 {new+old:>6}")

    print("\n-- 净新增作品的文件构成 --")
    for label, count in sorted(report["gallery_dist"].items(), key=lambda x: -x[1]):
        print(f"   {label:<20} {count}")

    print("\n-- 作者（前 25）--")
    print(f"   {'作者目录':<18}{'净新增':>7}{'已在库':>7}  库内博主匹配")
    for author in report["authors"][:25]:
        matched = "、".join(author["blogger_matched"]) or "❌ 未匹配（待确认）"
        print(
            f"   {author['author_dir'][:17]:<18}{author['new']:>7}{author['old']:>7}  {matched}"
        )
    if report["unmatched_authors"]:
        print(
            f"   ⚠ 有 {len(report['unmatched_authors'])} 个作者目录匹配不到库内抖音博主："
            f"{'、'.join(report['unmatched_authors'][:10])}"
        )

    cost = report["tag_cost"]
    print("\n-- 打标成本预估（按实测 {} 秒/张）--".format(DEFAULT_SEC_PER_TAG))
    print(f"   甲 逐文件打标:            {cost['per_file_count']:>6} 次 ≈ {cost['per_file_hours']:>5} 小时")
    print(f"   丙 按作品打一次(复制标签): {cost['per_work_count']:>6} 次 ≈ {cost['per_work_hours']:>5} 小时")
    print(
        f"   丙+ 例外(≥{BIG_GALLERY_THRESHOLD} 张作品逐图): {cost['hybrid_count']:>6} 次 ≈ {cost['hybrid_hours']:>5} 小时"
        f"（其中 {cost['big_gallery_works']} 个作品、{cost['big_gallery_images']} 张图）"
    )

    print(f"\n-- 话题（共 {report['hashtags_total']} 个，Top {top_hashtags}）--")
    for tag, count in report["top_hashtags"][:top_hashtags]:
        print(f"   #{tag}  {count}")
    print("\n提示：本命令不写库；真导入（入库 + 绑定博主 + 话题存档）在后续步骤实现。")


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
        description="扫描 f2 抖音下载目录并输出与素材库的重合面报表（只读）"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_F2_ROOT,
        help=f"f2 的 post 目录（默认 {DEFAULT_F2_ROOT}）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只扫描出报表（当前为默认行为，显式写出便于后续加 --apply）",
    )
    parser.add_argument("--json", type=Path, default=None, help="把报表另存为 JSON")
    parser.add_argument("--top-hashtags", type=int, default=15, help="展示的热门话题条数")
    parser.add_argument(
        "--sec-per-tag",
        type=float,
        default=DEFAULT_SEC_PER_TAG,
        help=f"打标耗时估算，秒/张（默认 {DEFAULT_SEC_PER_TAG}）",
    )
    args = parser.parse_args(argv)

    files = scan_directory(args.root)
    if not files:
        print(f"未在 {args.root} 找到可识别的 f2 产物文件")
        return 1

    report = build_report(
        root=args.root,
        files=files,
        library_hashes=load_library_hashes(),
        bloggers=load_douyin_bloggers(),
        sec_per_tag=args.sec_per_tag,
    )
    print_report(report, top_hashtags=args.top_hashtags)

    if args.json:
        args.json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n报表已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
