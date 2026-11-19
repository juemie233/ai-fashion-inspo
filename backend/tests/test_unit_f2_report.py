"""f2 扫描报表补充测试：画廊分布分档、读取失败降级、报表打印输出。

对应 ``app/services/f2/report.py`` 中既有 test_unit_f2_import_apply.py 未覆盖的
分支：gallery_dist 的 6 档分组、单文件读失败跳过并计数、打标成本三档口径，
以及 print_report 的完整/最简两种输出姿态。
"""

from pathlib import Path

from app.services.f2 import report as f2_report
from app.services.f2.common import BIG_GALLERY_THRESHOLD, DEFAULT_SEC_PER_TAG, ParsedFile
from app.services.f2.plan import DedupIndex


def _parsed(
    path: Path,
    *,
    kind: str = "image",
    index: int = 1,
    body: str = "#jk_#穿搭",
    author: str = "作者A",
    work: str = "w1",
    size: int = 10,
) -> ParsedFile:
    """构造一个 ParsedFile（path 无需真实存在，哈希走 cache 字典）。"""
    return ParsedFile(
        path=path,
        author_dir=author,
        author_key=author,
        work_key=work,
        created="2025-01-01 10-00-00",
        body=body,
        kind=kind,
        index=index,
        media_type="image" if kind == "image" else "video",
        size=size,
    )


def _cache(files: list[ParsedFile]) -> dict[Path, str]:
    """给每个文件一个内容不同的假 digest（走进程内缓存，不读盘）。"""
    return {f.path: f"digest-{i}" for i, f in enumerate(files)}


def _empty_dedup() -> DedupIndex:
    return DedupIndex(set(), set(), set(), set())


def test_gallery_dist_covers_all_buckets(tmp_path):
    """净新增作品的图数分布：0/1/2-4/5-9/10-19/20+ 六档都能命中。"""
    files: list[ParsedFile] = []
    specs = [(0, "video"), (1, "image"), (3, "image"), (7, "image"), (12, "image"), (22, "image")]
    for work_idx, (img_count, kind) in enumerate(specs):
        work = f"w{work_idx}"
        if kind == "video":
            files.append(
                _parsed(tmp_path / f"{work}_video.mp4", kind="video", index=0, work=work)
            )
            continue
        for i in range(1, img_count + 1):
            files.append(
                _parsed(tmp_path / f"{work}_image_{i}.webp", index=i, work=work)
            )

    report = f2_report.build_report(
        root=tmp_path,
        files=files,
        dedup=_empty_dedup(),
        bloggers={},
        hash_cache=_cache(files),
    )

    assert report["works_total"] == 6
    assert report["works_with_new"] == 6
    assert report["gallery_dist"] == {
        "纯视频/实况（无图）": 1,
        "1 张": 1,
        "2-4 张": 1,
        "5-9 张": 1,
        "10-19 张": 1,
        "20+ 张": 1,
    }
    # 文件类型分布按 kind 分别累计
    assert report["kind_stat"]["video"] == {"净新增": 1}
    assert report["kind_stat"]["image"] == {"净新增": 45}


def test_read_failed_file_is_skipped_and_counted(tmp_path):
    """扫描后消失/读不出的文件：跳过并单独计数，不让整张报表崩掉。

    用一个不存在的路径并且不给哈希缓存 → sha256_file 抛 FileNotFoundError（OSError）。
    其余文件仍正常统计；失败文件不计入净新增。
    """
    good = _parsed(tmp_path / "a.webp", work="w1", body="#jk")
    missing = _parsed(tmp_path / "已消失.webp", index=2, work="w1", body="#jk")

    report = f2_report.build_report(
        root=tmp_path,
        files=[good, missing],
        dedup=_empty_dedup(),
        bloggers={},
        hash_cache=_cache([good]),  # 只为 good 提供缓存，missing 会真实读盘失败
    )

    assert report["files_read_failed"] == 1
    assert report["files_new"] == 1
    assert report["files_total"] == 2
    # 作品层面仍按「有两个文件」计 gallery_imgs（另一个被跳过）
    assert report["works_with_new"] == 1


def test_cost_big_gallery_hybrid_formula(tmp_path):
    """打标成本三档：大图集作品在 hybrid 口径里按逐图计。"""
    files: list[ParsedFile] = []
    big_work = "big"
    for i in range(1, BIG_GALLERY_THRESHOLD + 1):
        files.append(_parsed(tmp_path / f"{big_work}_{i}.webp", index=i, work=big_work))
    for work in ("small1", "small2"):
        files.append(_parsed(tmp_path / f"{work}_1.webp", work=work))

    report = f2_report.build_report(
        root=tmp_path,
        files=files,
        dedup=_empty_dedup(),
        bloggers={"作者A": [{"id": 1, "name": "作者A"}]},
        hash_cache=_cache(files),
        sec_per_tag=2.0,
    )

    cost = report["tag_cost"]
    assert cost["big_gallery_works"] == 1
    assert cost["big_gallery_images"] == BIG_GALLERY_THRESHOLD
    assert cost["per_file_count"] == len(files)
    assert cost["per_work_count"] == 3
    assert cost["hybrid_count"] == (3 - 1) + BIG_GALLERY_THRESHOLD
    assert cost["hybrid_hours"] == round(
        ((3 - 1) * 2.0 + BIG_GALLERY_THRESHOLD * 2.0) / 3600, 1
    )
    assert report["matched_author_keys"] == ["作者A"]


def _full_report() -> dict:
    """构造一份触发 print_report 所有可选分支的报表。"""
    return {
        "root": "/tmp/f2",
        "files_total": 10,
        "works_total": 4,
        "works_with_new": 2,
        "works_all_in_library": 2,
        "works_with_trash": 1,
        "files_new": 3,
        "files_in_library": 6,
        "files_in_trash": 1,
        "files_read_failed": 1,
        "bytes_new": 3 * 1024**3,
        "bytes_in_library": 6 * 1024**3,
        "bytes_in_trash": 1024**3,
        "kind_stat": {"image": {"净新增": 2, "已入库": 5}, "video": {"净新增": 1, "已在垃圾桶": 1}},
        "authors": [
            {
                "author_dir": "作者A",
                "author_key": "作者A",
                "new": 2,
                "old": 1,
                "trash": 0,
                "blogger_matched": ["博主A"],
                "blogger_candidates": 1,
            },
            {
                "author_dir": "作者B",
                "author_key": "作者B",
                "new": 1,
                "old": 0,
                "trash": 0,
                "blogger_matched": [],
                "blogger_candidates": 0,
            },
        ],
        "unmatched_authors": ["作者B"],
        "matched_author_keys": ["作者A"],
        "gallery_dist": {"2-4 张": 2},
        "top_hashtags": [["jk", 3], ["穿搭", 2]],
        "hashtags_total": 2,
        "tag_cost": {
            "per_file_count": 3,
            "per_file_hours": 0.1,
            "per_work_count": 2,
            "per_work_hours": 0.0,
            "hybrid_count": 4,
            "hybrid_hours": 0.1,
            "big_gallery_works": 1,
            "big_gallery_images": 2,
        },
    }


def test_print_report_full_branches(capsys):
    """有垃圾桶/读取失败/哈希缓存错误/未匹配作者时都打印对应提示。"""
    cache_stats = {
        "hit": 5,
        "cached_rows": 8,
        "computed": 2,
        "hash_seconds": 0.5,
        "error": "db is locked",
    }

    f2_report.print_report(_full_report(), top_hashtags=1, cache_stats=cache_stats)

    out = capsys.readouterr().out
    assert "= f2 下载目录扫描报表" in out
    assert "已在垃圾桶 1 个文件" in out
    assert "1 个文件读取失败" in out
    assert "哈希缓存：复用 5 次" in out
    assert "哈希缓存写入失败" in out
    assert "未匹配（待确认）" in out
    assert "匹配不到库内抖音博主" in out
    assert "打标成本预估" in out
    assert "#jk  3" in out  # top_hashtags=1 只展示第一条
    assert "#穿搭" not in out.split("-- 话题")[1]


def test_print_report_minimal_without_optional_sections(capsys):
    """没有垃圾桶/读取失败/缓存统计时：不打印可选提示，命中率按 0 兜底。"""
    report = {
        "root": "/tmp/empty",
        "files_total": 0,
        "works_total": 0,
        "works_with_new": 0,
        "works_all_in_library": 0,
        "works_with_trash": 0,
        "files_new": 0,
        "files_in_library": 0,
        "files_in_trash": 0,
        "bytes_new": 0,
        "bytes_in_library": 0,
        "bytes_in_trash": 0,
        "kind_stat": {},
        "authors": [],
        "unmatched_authors": [],
        "matched_author_keys": [],
        "gallery_dist": {},
        "top_hashtags": [],
        "hashtags_total": 0,
        "tag_cost": {
            "per_file_count": 0,
            "per_file_hours": 0.0,
            "per_work_count": 0,
            "per_work_hours": 0.0,
            "hybrid_count": 0,
            "hybrid_hours": 0.0,
            "big_gallery_works": 0,
            "big_gallery_images": 0,
        },
    }

    f2_report.print_report(report)

    out = capsys.readouterr().out
    assert "命中率 0.0%" in out
    assert "已在垃圾桶" not in out
    assert "读取失败" not in out
    assert "哈希缓存" not in out
    assert "匹配不到库内抖音博主" not in out
    # 打印里按默认 sec_per_tag 说明成本口径
    assert f"按实测 {DEFAULT_SEC_PER_TAG} 秒/张" in out
