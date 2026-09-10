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

三条约定
--------
1. **导入不做标签分析**：本模块绝不调用 analyze_image，也不建向量。素材入库后
   处于「未打标」状态，由现有的批量分析任务（一键）按需补——一次性 1.2 万张
   要 8~41 小时 GPU，不该卡住「获取素材」这一步。
2. **必须去重**（见 :func:`build_import_plan` 的四层判据），且重复运行幂等。
3. **不写话题存档表**（``scraper_hashtags``）：正文里的 ``#话题`` 已经随
   ``caption`` 落库，而 caption 参与文本向量（TEXT_EMBEDDING_FORMULA_VERSION=2），
   语义搜索能命中，**零信息损失**；而话题库在 UI 侧只对小红书显示、唯一用途是
   给「定时采集计划」提供候选关键词，与抖音素材入库无交集。故本路径不写入，
   避免产生一批无处可用的数据。
"""

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

# 与 backend/scripts 下其它脚本一致：把 backend 加入 sys.path，便于模块方式执行
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings  # noqa: E402
from app.services.file_service import (  # noqa: E402
    _generate_image_thumbnail_sync,
    validate_media,
)

from .scraper_common import utcnow  # noqa: E402
from .scraper_download import extract_video_thumbnail_sync  # noqa: E402

# ═══════════════════════════════════════════════════════════════
#  常量
# ═══════════════════════════════════════════════════════════════

"""f2 默认工作目录（可用 --f2-dir 覆盖）。

f2 在**自己的工作目录**下创建 `Download/` 与 `douyin_users.db`（相对路径），
所以调用它时必须把 cwd 设到该目录，作者清单也从这里的 DB 读。
"""
DEFAULT_F2_DIR = Path(r"C:\Users\Administrator\Desktop\f2")

"""下载产物相对 f2 工作目录的位置（f2 在 cwd 下建 Download/douyin/post/）。"""
F2_DOWNLOAD_SUBDIR = Path("Download/douyin/post")

"""f2 的作者库文件名（含 user_info_web 表：sec_user_id / nickname / aweme_count）。"""
F2_AUTHOR_DB = "douyin_users.db"

"""f2 默认下载根目录（可用 --root 覆盖；与 --f2-dir 联动）。"""
DEFAULT_F2_ROOT = DEFAULT_F2_DIR / F2_DOWNLOAD_SUBDIR

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
    账号后加 `√`；库里的博主名则可能是 `美羊羊桑_`，而清单/昵称里写的是
    `美羊羊桑～`。规则：去掉尾部的 `√`、数字、下划线、点、空白与**波浪号**
    （半角 `~` / 全角 `～` / 波线 `〜`，抖音昵称常用它做装饰），其余保留。

    实测踩点：只剥 `√`/数字时会漏掉 `美羊羊桑～`，导致回填博主时把同一人
    当成新博主建出重复记录。

    Args:
        name: 作者目录名（或库内博主名 / 昵称）。

    Returns:
        归一化后的名字（可能为空串）。
    """
    return re.sub(r"[√\s_\.0-9~～〜]+$", "", (name or "").strip())


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
    print(
        "\n提示：本命令不写库（只读报表）。确认无误后执行导入："
        "\n    python -m scripts.import_f2_downloads --apply"
        "（要连增量下载一起做：--fetch --apply）"
    )


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
INSERT_F2_SQL = (
    "INSERT INTO inspirations (id, source_type, source_url, source_author, "
    "source_platform_id, file_path, thumbnail_path, media_type, dominant_colors, "
    "is_favorite, quality_status, rating, is_ai_generated, content_hash, caption, "
    "scraper_task_id, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, 'pending', 0, 0, ?, ?, NULL, ?, ?)"
)


def work_hash(work_key: str) -> str:
    """作品键 → 12 位短哈希（合成平台 ID 用，规避中文与长度超限）。"""
    return hashlib.sha1(work_key.encode("utf-8")).hexdigest()[:12]


def platform_id_for(item: ParsedFile) -> str:
    """构造素材的 source_platform_id：``f2:{作品短哈希}#{序号}``。

    ⚠ 为什么必须带**类型前缀**的序号：``ix_inspirations_source_platform_id``
    是**全局唯一**索引（仅约束未删除素材，见 models/inspiration.py），同一
    作品的多张图/多段视频不能共用一个平台 ID。序号还必须带类型——同一作品
    常同时有静态图与 live 分段（``..._image_1.webp`` 与 ``..._live_1.mp4``），
    只用数字会让两者都变成 ``#1`` 而撞唯一索引（试跑实测：live 分段整批失败）。

    Args:
        item: 已解析文件。

    Returns:
        稳定的平台 ID（同一文件重复计算结果一致 → 天然幂等）；
        仍可按前缀 ``f2:{作品短哈希}#`` 聚合出「同一作品」的全部素材。
    """
    suffix = (
        f"{item.kind}{item.index}" if item.kind in ("image", "live") else item.kind
    )
    return f"f2:{work_hash(item.work_key)}#{suffix}"


@dataclass
class ImportDecision:
    """单个文件的导入决策。"""

    item: ParsedFile
    action: str  # import / skip
    reason: str  # 跳过原因（action=import 时为空串）
    platform_id: str
    content_hash: str
    blogger_id: int | None = None  # 匹配到的库内博主（未匹配为 None）


def _skip_reason(
    item: ParsedFile,
    digest: str,
    library_hashes: set[str],
    seen_hashes: set[str],
    existing_platform_ids: set[str],
    skip_live: bool,
) -> str:
    """返回跳过原因（空串＝可以导入）；判据顺序即优先级。"""
    if skip_live and item.kind == "live":
        return "按 --skip-live 跳过 live 分段"
    if digest in library_hashes:
        return "已在库（内容相同）"
    if digest in seen_hashes:
        return "批次内重复（同内容已处理）"
    if platform_id_for(item) in existing_platform_ids:
        return "已在库（平台 ID 命中）"
    return ""


def build_import_plan(
    files: list[ParsedFile],
    library_hashes: set[str],
    existing_platform_ids: set[str],
    bloggers: dict[str, list[dict]] | None = None,
    authors: set[str] | None = None,
    limit: int | None = None,
    skip_live: bool = False,
    hash_cache: dict[Path, str] | None = None,
) -> tuple[list[ImportDecision], dict[str, int], int]:
    """决定每个文件「导入 / 跳过」——去重判据集中于此（纯函数，便于单测）。

    去重分层（顺序即优先级，命中即跳过）：

    1. **内容判重**：文件 SHA-256 命中库内 ``inspirations.content_hash``
       （库内素材哈希覆盖率 100%，这是主判据，跨来源也有效）
    2. **批次内判重**：同一批次里相同内容只入一次（重复下载 / 多目录同一文件）
    3. **平台 ID 判重**：合成平台 ID 命中库内 ``source_platform_id``
       （幂等兜底：即使哈希口径变化，重复运行也不会重复入库）
    4. **参数过滤**：``--authors`` 只导指定作者、``--limit`` 限制作品数、
       ``--skip-live`` 跳过 live 分段

    作品维度：只要该作品还有待导入文件，就消耗一个 ``--limit`` 配额；
    同一作品的全部文件共用同一博主关联。

    Args:
        files: 已解析文件列表。
        library_hashes: 库内未删除素材的 content_hash 集合。
        existing_platform_ids: 库内未删除素材的 source_platform_id 集合。
        bloggers: 归一化博主名 → 博主列表（用于绑定；仅唯一候选才自动绑）。
        authors: 只导入这些作者（归一化名或目录名），None 表示全部。
        limit: 最多导入多少个作品。
        skip_live: 是否跳过 live 实况分段视频。
        hash_cache: 文件哈希缓存（跨调用复用，避免重复读盘）。

    Returns:
        (决策列表, 跳过原因计数, 因超出 limit 未处理的作品数)
    """
    cache = hash_cache if hash_cache is not None else {}
    bloggers = bloggers or {}
    wanted = {normalize_author(a) for a in authors} if authors else None

    decisions: list[ImportDecision] = []
    skipped: Counter = Counter()
    seen_hashes: set[str] = set()
    accepted_works = 0
    deferred_works = 0

    for _work_key, items in group_works(files).items():
        digests: dict[Path, str] = {}
        for item in items:
            digest = cache.get(item.path)
            if digest is None:
                digest = sha256_file(item.path)
                cache[item.path] = digest
            digests[item.path] = digest

        first = items[0]
        if wanted is not None and first.author_key not in wanted and first.author_dir not in wanted:
            skipped["作者不在 --authors 范围"] += len(items)
            continue

        reasons = [
            _skip_reason(
                item, digests[item.path], library_hashes, seen_hashes,
                existing_platform_ids, skip_live,
            )
            for item in items
        ]
        if not any(not r for r in reasons):
            for item, reason in zip(items, reasons):
                skipped[reason] += 1
                decisions.append(ImportDecision(
                    item=item, action="skip", reason=reason,
                    platform_id=platform_id_for(item), content_hash=digests[item.path],
                ))
            continue

        if limit is not None and accepted_works >= limit:
            for item in items:
                skipped["超出 --limit 未处理"] += 1
            deferred_works += 1
            continue
        accepted_works += 1

        matched = bloggers.get(first.author_key) or []
        blogger_id = matched[0]["id"] if len(matched) == 1 else None

        for item, reason in zip(items, reasons):
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


def load_library_platform_ids(db_path: Path | None = None) -> set[str]:
    """读取库内未删除素材的 source_platform_id 集合（导入幂等兜底）。

    Args:
        db_path: 数据库路径（缺省用 :func:`library_db_path`）。

    Returns:
        平台 ID 集合（空值与已删除素材不计入）。
    """
    path = db_path or library_db_path()
    if not path.exists():
        return set()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT source_platform_id FROM inspirations "
                "WHERE deleted_at IS NULL AND source_platform_id IS NOT NULL"
            )
        }
    finally:
        conn.close()


def apply_import(
    decisions: list[ImportDecision],
    db_path: Path | None = None,
    storage_root: Path | None = None,
    make_thumbnails: bool = True,
    batch_dir: Path | None = None,
    on_progress=None,
    should_stop=None,
) -> dict:
    """把决策为 import 的文件复制进素材库并写库——**不做标签分析、不建向量**。

    步骤：合法性校验 → 复制文件 → 生成缩略图 → INSERT（含 content_hash /
    caption / 平台 ID / 来源作者）→ 博主关联 → 落批次清单（不写话题存档表，
    理由见模块 docstring 第 3 条）。单条失败只回滚该条（删掉已复制的文件），
    不影响整批。

    Args:
        decisions: :func:`build_import_plan` 的结果（其中 action=skip 的会被忽略）。
        db_path: 素材库路径（缺省 :func:`library_db_path`）。
        storage_root: 存储根目录（缺省 settings.storage_root）。
        make_thumbnails: 是否生成缩略图（图片走 PIL、视频走 ffmpeg 首帧）。
        batch_dir: 批次清单目录（缺省 storage/import_batches）。
        on_progress: 可选回调 ``on_progress(done, total)``，供后台任务报告进度。
        should_stop: 可选回调 ``should_stop() -> bool``，返回 True 时提前收尾
            （供任务暂停/取消；已入库的部分保留，并写入批次清单）。

    Returns:
        统计字典：imported / failed / ids / errors / batch_file / stopped。
    """
    db_path = db_path or library_db_path()
    storage_root = storage_root or settings.storage_root
    batch_dir = batch_dir or (storage_root / IMPORT_BATCH_DIRNAME)
    today = utcnow().strftime("%Y-%m")
    now_str = utcnow().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(str(db_path))

    imported_ids: list[str] = []
    imported_rows: list[dict] = []
    errors: list[dict] = []
    total = sum(1 for d in decisions if d.action == "import")
    processed = 0
    stopped = False

    for decision in decisions:
        if decision.action != "import":
            continue
        if should_stop is not None and should_stop():
            stopped = True
            break
        item = decision.item
        dest: Path | None = None
        thumb: str | None = None
        try:
            # 合法性/体积校验（与上传路径同一套规则），不合格的直接跳过
            validate_media(item.path)

            sub_dir = "videos" if item.media_type == "video" else "images"
            dest_dir = storage_root / sub_dir / today
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{uuid.uuid4().hex[:16]}{item.path.suffix.lower()}"
            shutil.copy2(item.path, dest)

            if make_thumbnails:
                if item.media_type == "video":
                    # 与图片一致：显式传 storage 下的缩略图目录，避免 --storage-root
                    # 试跑时缩略图落进真实存储（DB 里的相对路径在目标根下找不到）
                    thumb = extract_video_thumbnail_sync(
                        dest, today, thumbs_dir=storage_root / "thumbnails"
                    )
                else:
                    # 显式传入 storage 下的缩略图目录：与素材文件同根，便于整体迁移/清理
                    thumb = _generate_image_thumbnail_sync(
                        dest, thumbs_dir=storage_root / "thumbnails"
                    )

            rel_path = f"{sub_dir}/{today}/{dest.name}"
            insp_id = str(uuid.uuid4())
            conn.execute(
                INSERT_F2_SQL,
                (
                    insp_id,
                    "douyin",
                    None,  # source_url：f2 未提供 aweme_id，留空而非造伪链接
                    item.author_key or item.author_dir,
                    decision.platform_id,
                    rel_path,
                    thumb,
                    item.media_type,
                    decision.content_hash,
                    item.caption or None,
                    now_str,
                    now_str,
                ),
            )
            if decision.blogger_id:
                conn.execute(
                    "INSERT OR IGNORE INTO inspiration_bloggers "
                    "(inspiration_id, blogger_id, confidence) VALUES (?, ?, ?)",
                    (insp_id, decision.blogger_id, AUTHOR_MATCH_CONFIDENCE),
                )
            conn.commit()

            imported_ids.append(insp_id)
            imported_rows.append(
                {
                    "inspiration_id": insp_id,
                    "file_path": rel_path,
                    "thumbnail_path": thumb,
                    "source_file": str(item.path),
                    "platform_id": decision.platform_id,
                    "author_dir": item.author_dir,
                    "work_key": item.work_key,
                    "media_type": item.media_type,
                    "caption": item.caption[:200],
                    "hashtags": item.hashtags,
                    "blogger_id": decision.blogger_id,
                }
            )
        except Exception as exc:  # noqa: BLE001 —— 单条失败不影响整批
            try:
                conn.rollback()
            except Exception:
                pass
            # 清理本条已落盘的文件与缩略图（缩略图是相对路径，需拼回存储根，
            # 否则每次失败都会留下一张孤儿缩略图）
            leftovers = [dest]
            if thumb:
                leftovers.append(storage_root / thumb)
            for leftover in leftovers:
                if leftover is None:
                    continue
                try:
                    leftover.unlink(missing_ok=True)
                except Exception:
                    pass
            errors.append({"source_file": str(item.path), "error": str(exc)[:200]})
        finally:
            processed += 1
            if on_progress is not None:
                on_progress(processed, total)

    # 批次清单：记录本批导入的 id 与来源，便于审计与将来一键回滚
    batch_id = f"f2-{utcnow().strftime('%Y%m%d-%H%M%S')}"
    batch_file = batch_dir / f"{batch_id}.json"
    batch_error: str | None = None
    try:
        batch_dir.mkdir(parents=True, exist_ok=True)
        batch_file.write_text(
            json.dumps(
                {
                    "batch_id": batch_id,
                    "created_at": now_str,
                    "db": str(db_path),
                    "storage_root": str(storage_root),
                    "imported": imported_rows,
                    "errors": errors,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        # 单独记录：清单写失败不影响已入库素材，但**这批将无法回滚**，
        # 必须与「素材失败」区分开并醒目提示（曾把两者混在一个 failed 计数里）
        batch_error = str(exc)[:200]
        batch_file = None  # type: ignore[assignment]
    conn.close()

    return {
        "imported": len(imported_ids),
        "failed": len(errors),
        "ids": imported_ids,
        "errors": errors,
        "batch_file": str(batch_file) if batch_file else "",
        "batch_error": batch_error,
        "stopped": stopped,
    }


# ═══════════════════════════════════════════════════════════════
#  调用 f2 增量下载（--fetch）
# ═══════════════════════════════════════════════════════════════


def f2_available() -> bool:
    """f2 是否已安装（以模块方式 `python -m f2` 调用）。"""
    return importlib.util.find_spec("f2") is not None


def load_f2_authors(f2_dir: Path) -> list[dict]:
    """读取 f2 用户库里的作者清单（增量下载的「关注了谁」来源）。

    为什么以 f2 的库为准：素材库的 `bloggers` 表**没有 sec_user_id**
    （实测 24 个抖音博主的 platform_user_id 多为 NULL、profile_url 全空），
    而 f2 的 `user_info_web` 存了 sec_user_id / nickname / aweme_count。

    Args:
        f2_dir: f2 工作目录（其下有 douyin_users.db）。

    Returns:
        [{"sec_user_id", "nickname", "aweme_count"}]；库或表缺失时返回空列表。
    """
    db = f2_dir / F2_AUTHOR_DB
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT sec_user_id, nickname, aweme_count FROM user_info_web"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [
        {
            "sec_user_id": row[0],
            "nickname": row[1] or "",
            "aweme_count": int(row[2] or 0),
        }
        for row in rows
        if row[0]
    ]


def build_f2_command(
    author: dict,
    download_root: Path | None = None,
    naming: str | None = None,
    auto_cookie: str | None = None,
) -> list[str]:
    """构造单个作者的 f2 下载命令（主页作品 / 全部日期，增量由 f2 保证）。

    - `-M post`：主页发布的作品（f2 支持 post/like/collect/mix…）
    - `-i all`：日期区间取全部；f2 用自己记录的 last_aweme_id 跳过已下过的
    - `-p`：下载根目录（缺省就是 f2 工作目录下的 Download/）
    - `-n`：命名模板。**缺省不传**，沿用 f2 配置里的模板——本模块的解析器
      依赖 `{时间}_{正文}_{类型}_{序号}` 形状，擅自改模板会让作品分组/正文解析失效
    - `--auto-cookie`：从浏览器自动取 cookie（需先关闭该浏览器）

    Args:
        author: :func:`load_f2_authors` 的一项。
        download_root: 下载根目录（传给 f2 的 -p）。
        naming: 命名模板（一般不要传）。
        auto_cookie: 浏览器名（chrome / chromium / edge …）。

    Returns:
        可直接交给 subprocess 的参数列表。
    """
    cmd = [
        sys.executable,
        "-m",
        "f2",
        "dy",
        "-u",
        f"https://www.douyin.com/user/{author['sec_user_id']}",
        "-M",
        "post",
        "-i",
        "all",
    ]
    if download_root is not None:
        cmd += ["-p", str(download_root)]
    if naming:
        cmd += ["-n", naming]
    if auto_cookie:
        cmd += ["--auto-cookie", auto_cookie]
    return cmd


def _default_runner(cmd: list[str], cwd: Path) -> tuple[int, str]:
    """默认执行器：继承标准输出，让 f2 的下载进度实时可见。

    Args:
        cmd: 命令参数列表。
        cwd: 工作目录（必须是 f2 工作目录）。

    Returns:
        (退出码, 附加信息)。
    """
    proc = subprocess.run(cmd, cwd=str(cwd), check=False)
    return proc.returncode, ""


def run_fetch(
    f2_dir: Path,
    authors: set[str] | None = None,
    download_root: Path | None = None,
    naming: str | None = None,
    auto_cookie: str | None = None,
    limit: int | None = None,
    runner=None,
) -> dict:
    """串行调 f2 增量下载各作者的新作品（一次一个，避免并发触发风控）。

    设计要点：
    - **cwd 必须是 f2 工作目录**：f2 在 cwd 下读写 douyin_users.db 与 Download/，
      cwd 不对会另起一个空作者库、把下载落到别处
    - 单个作者失败（风控 / 网络 / cookie 失效）只记录并继续下一个，不阻断整批；
      失败细节在 f2/logs/ 下
    - 作者过滤复用 `--authors`（归一化名匹配），与导入阶段的语义一致

    Args:
        f2_dir: f2 工作目录。
        authors: 只下载这些作者（归一化名，None 表示全部）。
        download_root: 传给 f2 的 -p 下载根目录。
        naming: 传给 f2 的 -n 命名模板（一般不要传）。
        auto_cookie: 传给 f2 的 --auto-cookie 浏览器名。
        limit: 最多下载多少个作者（试跑用）。
        runner: 可注入的执行器（签名 (cmd, cwd) -> (returncode, info)），便于单测。

    Returns:
        {"total", "ok", "failed", "results", "error"}；
        results 每项含 nickname / sec_user_id / rc / cmd。
    """
    runner = runner or _default_runner
    all_authors = load_f2_authors(f2_dir)
    if not all_authors:
        return {
            "total": 0,
            "ok": 0,
            "failed": 0,
            "results": [],
            "error": (
                f"未找到 f2 作者清单：{f2_dir / F2_AUTHOR_DB}（先手动跑一次 f2 "
                f"确认能登录并下载，本命令只做「增量」）"
            ),
        }

    wanted = {normalize_author(a) for a in authors} if authors else None
    targets = [
        a for a in all_authors if wanted is None or normalize_author(a["nickname"]) in wanted
    ]
    if limit is not None:
        targets = targets[:limit]

    results: list[dict] = []
    ok = failed = 0
    for author in targets:
        cmd = build_f2_command(author, download_root, naming, auto_cookie)
        print(f"  ▶ {author['nickname']}（抖音作品总数 {author['aweme_count']}）")
        try:
            rc, _info = runner(cmd, f2_dir)
        except Exception as exc:  # noqa: BLE001 —— 单个作者失败不阻断整批
            rc = -1
            print(f"    ✗ 调用 f2 失败：{type(exc).__name__}: {exc}")
        if rc == 0:
            ok += 1
            print("    ✓ 完成（f2 已按 last_aweme_id 跳过已下载作品）")
        else:
            failed += 1
            print(f"    ✗ 退出码 {rc}，跳过该作者（详情见 {f2_dir / 'logs'}）")
        results.append(
            {
                "nickname": author["nickname"],
                "sec_user_id": author["sec_user_id"],
                "rc": rc,
                "cmd": " ".join(cmd),
            }
        )
    return {"total": len(targets), "ok": ok, "failed": failed, "results": results}


# ═══════════════════════════════════════════════════════════════
#  按批次回滚（--rollback）
# ═══════════════════════════════════════════════════════════════


def latest_batch_file(batch_dir: Path) -> Path | None:
    """取批次目录里最新的一份清单（--rollback latest 用）。

    Args:
        batch_dir: 批次清单目录。

    Returns:
        最新的清单路径；目录不存在或无清单时返回 None。
    """
    if not batch_dir.exists():
        return None
    files = sorted(batch_dir.glob("*.json"))
    return files[-1] if files else None


def _table_exists(conn, name: str) -> bool:
    """表是否存在（回滚要在「迁移未跑全的库副本」上也能给出可读结果）。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def batch_storage_root(batch_file: Path) -> Path | None:
    """读取批次清单里记录的导入期存储根（回滚优先用它，而不是当前配置）。

    清单在导入时写入了 `storage_root`；若导入用了 `--storage-root` 临时目录，
    回滚时用当前配置去找文件会全部找不到（孤儿文件），只用当前根定位批次还可能
    选错批次。这里把它读出来供调用方做默认值。

    Args:
        batch_file: 批次清单路径。

    Returns:
        清单记录的存储根；缺失或无法解析时返回 None。
    """
    try:
        data = json.loads(Path(batch_file).read_text(encoding="utf-8"))
    except Exception:
        return None
    root = data.get("storage_root")
    return Path(root) if isinstance(root, str) and root else None


def plan_rollback(
    batch_file: Path,
    db_path: Path,
    force: bool = False,
) -> tuple[list[dict], list[dict]]:
    """按批次清单区分「可删除」与「需保留」的条目（不写任何数据）。

    保留判断（未被导入动作之外的改动污染才删）：素材若已经有标签关联、被收藏、
    有评分、或质量状态不再是 pending，说明用户已经用过它——默认不删，避免把
    人的后续工作一起抹掉；`force=True` 时才连这些一起删。

    查询按批进行（`IN (...)` 分片）：一次回滚可能涉及上万条，逐条 SELECT 会变成
    数万次查询。

    Args:
        batch_file: 导入时落盘的批次清单。
        db_path: 素材库路径。
        force: 是否连「已被改动」的素材一起删。

    Returns:
        (可删除列表, 需保留列表)，每项含 inspiration_id / file_path /
        thumbnail_path / 以及保留原因。
    """
    batch = json.loads(Path(batch_file).read_text(encoding="utf-8"))
    rows = batch.get("imported") or []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    deletable: list[dict] = []
    kept: list[dict] = []
    try:
        # 一次性取回素材状态与标签计数（分片以避开 SQLite 变量数上限）
        ids = [r.get("inspiration_id") for r in rows if r.get("inspiration_id")]
        states: dict[str, tuple] = {}
        tag_counts: dict[str, int] = {}
        has_tag_table = _table_exists(conn, "inspiration_tags")
        for chunk in (ids[i : i + 900] for i in range(0, len(ids), 900)):
            marks = ",".join("?" * len(chunk))
            for insp_id, is_favorite, rating, quality_status in conn.execute(
                "SELECT id, is_favorite, rating, quality_status FROM inspirations "
                f"WHERE deleted_at IS NULL AND id IN ({marks})",
                chunk,
            ):
                states[insp_id] = (is_favorite, rating, quality_status)
            if has_tag_table:
                for insp_id, count in conn.execute(
                    "SELECT inspiration_id, COUNT(*) FROM inspiration_tags "
                    f"WHERE inspiration_id IN ({marks}) GROUP BY inspiration_id",
                    chunk,
                ):
                    tag_counts[insp_id] = count

        for row in rows:
            insp_id = row.get("inspiration_id")
            if not insp_id:
                continue
            if insp_id not in states:
                kept.append({**row, "keep_reason": "素材已不存在（删除过或已回滚）"})
                continue
            tag_links = tag_counts.get(insp_id, 0)
            is_favorite, rating, quality_status = states[insp_id]
            if not force and (
                tag_links or is_favorite or rating or quality_status not in (None, "pending")
            ):
                kept.append(
                    {
                        **row,
                        "keep_reason": (
                            f"已被改动（标签 {tag_links} / 收藏 {is_favorite} / "
                            f"评分 {rating} / 质量 {quality_status}）"
                        ),
                    }
                )
                continue
            deletable.append(row)
    finally:
        conn.close()
    return deletable, kept


def apply_rollback(
    deletable: list[dict],
    db_path: Path,
    storage_root: Path | None = None,
) -> dict:
    """实际删除（物理删除行 + 文件 + 缩略图 + 关键帧目录 + 关联表），逐条提交。

    为什么是物理删除而不是进垃圾桶：本操作语义是「撤销一次错误导入」，
    而垃圾桶素材会作为负样本参与质量学习——把误导入的素材当成「质量差」
    负样本会污染训练数据。

    单条失败只记录并继续（例如文件已被外部删掉）。

    Args:
        deletable: :func:`plan_rollback` 的「可删除」列表。
        db_path: 素材库路径。
        storage_root: 存储根目录（缺省 settings.storage_root）。

    Returns:
        {"deleted", "failed", "removed_files", "errors"}。
    """
    storage_root = storage_root or settings.storage_root
    conn = sqlite3.connect(str(db_path))
    deleted = 0
    removed_files = 0
    errors: list[dict] = []
    has_tag_table = _table_exists(conn, "inspiration_tags")
    has_blogger_table = _table_exists(conn, "inspiration_bloggers")
    for row in deletable:
        insp_id = row.get("inspiration_id")
        try:
            # 顺序与项目既有约定一致（inspiration_trash 的物理删除）：先删库并提交，
            # 再清理磁盘文件——反过来若 DB 删除失败，文件已不可逆丢失而记录还在，
            # 素材会变成指向缺失文件的悬空条目
            if has_tag_table:
                conn.execute(
                    "DELETE FROM inspiration_tags WHERE inspiration_id = ?", (insp_id,)
                )
            if has_blogger_table:
                conn.execute(
                    "DELETE FROM inspiration_bloggers WHERE inspiration_id = ?", (insp_id,)
                )
            conn.execute("DELETE FROM inspirations WHERE id = ?", (insp_id,))
            conn.commit()
            deleted += 1

            for rel in (row.get("file_path"), row.get("thumbnail_path")):
                if not rel:
                    continue
                target = storage_root / rel
                try:
                    if target.exists():
                        target.unlink()
                        removed_files += 1
                except Exception:
                    pass
            # 视频关键帧目录（详情页懒提取可能已生成）
            frames_dir = storage_root / "keyframes" / str(insp_id)
            if frames_dir.exists():
                shutil.rmtree(frames_dir, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001 —— 单条失败不阻断整批
            try:
                conn.rollback()
            except Exception:
                pass
            errors.append({"inspiration_id": insp_id, "error": str(exc)[:200]})
    conn.close()
    return {
        "deleted": deleted,
        "failed": len(errors),
        "removed_files": removed_files,
        "errors": errors,
    }


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
        help="传给 f2 的 -n 命名模板；缺省沿用 f2 配置（勿随意改：解析依赖默认模板形状）",
    )
    parser.add_argument(
        "--auto-cookie",
        default=None,
        help="传给 f2 的 --auto-cookie 浏览器名（chrome/chromium/edge…），需先关闭该浏览器",
    )
    parser.add_argument(
        "--fetch-limit",
        type=int,
        default=None,
        help="--fetch 时最多下载多少个作者（试跑用）",
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
    parser.add_argument("--limit", type=int, default=None, help="最多导入多少个作品")
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
        )
        if fetch.get("error"):
            print(f"  ⚠ {fetch['error']}")
        else:
            print(
                f"  下载汇总：成功 {fetch['ok']} / 失败 {fetch['failed']}"
                f"（共 {fetch['total']} 个作者）"
            )
            if fetch["failed"]:
                print("  ⚠ 有作者下载失败（多为风控/cookie 失效），已跳过，稍后可重跑")
        print()

    files = scan_directory(root)
    if not files:
        print(f"未在 {root} 找到可识别的 f2 产物文件")
        return 1

    hash_cache: dict[Path, str] = {}
    library_hashes = load_library_hashes(db_path)
    bloggers = load_douyin_bloggers(db_path)

    # ── 缺省：只读报表 ──
    if not args.apply:
        report = build_report(
            root=root,
            files=files,
            library_hashes=library_hashes,
            bloggers=bloggers,
            hash_cache=hash_cache,
            sec_per_tag=args.sec_per_tag,
        )
        print_report(report, top_hashtags=args.top_hashtags)
        if args.json:
            args.json.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"\n报表已写入 {args.json}")
        return 0

    # ── 真导入 ──
    if not db_path.exists():
        print(f"素材库不存在: {db_path}")
        return 1

    decisions, skipped, deferred_works = build_import_plan(
        files=files,
        library_hashes=library_hashes,
        existing_platform_ids=load_library_platform_ids(db_path),
        bloggers=bloggers,
        authors=authors_filter,
        limit=args.limit,
        skip_live=args.skip_live,
        hash_cache=hash_cache,
    )
    to_import = [d for d in decisions if d.action == "import"]
    work_count = len({d.item.work_key for d in to_import})

    print(f"\n=== 导入计划（库: {db_path}）===")
    print(f"待导入 {len(to_import)} 个文件 / {work_count} 个作品")
    for reason, count in sorted(skipped.items(), key=lambda x: -x[1]):
        print(f"   跳过 {count:>6}  {reason}")
    if deferred_works:
        print(f"   因 --limit 未处理的作品 {deferred_works} 个")
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


if __name__ == "__main__":
    sys.exit(main())
