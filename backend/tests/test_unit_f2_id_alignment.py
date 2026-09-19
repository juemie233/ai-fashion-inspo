"""f2 真实作品 ID 对齐报告单元测试：Cookie 归一 / 对齐分档 / 作者归集。

覆盖 scripts/report_f2_id_alignment.py 的**纯函数部分**（不发网络请求）。
唯一需要 f2 本体的是「接口侧期望文件名 == 磁盘侧文件名」这条不变量——它必须与
f2 的 replaceT/split_filename 逐字符一致，故直接调用 f2 校验（未装 f2 时跳过）。
"""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from scripts import report_f2_id_alignment as rep

requires_f2 = pytest.mark.skipif(
    importlib.util.find_spec("f2") is None,
    reason="未安装 f2（对齐报告依赖它的文件名变换）",
)


def _write(path: Path, content: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# ═══════════════════════════════════════════════════════════════
#  Cookie
# ═══════════════════════════════════════════════════════════════


def test_normalize_cookie_accepts_three_shapes():
    assert rep.normalize_cookie("a=1; b=2") == "a=1; b=2"
    assert rep.normalize_cookie('[{"name":"a","value":"1"}]') == "a=1"
    assert rep.normalize_cookie('{"a":"1","b":"2"}') == "a=1; b=2"
    assert rep.normalize_cookie("") == ""
    assert rep.normalize_cookie("   ") == ""


def test_normalize_cookie_keeps_broken_json_as_text():
    """JSON 解析失败时按原始文本返回，不要静默丢成空串。"""
    assert rep.normalize_cookie("[不是 JSON") == "[不是 JSON"


def test_looks_logged_out_detects_guest_cookie():
    """实测：只有 UIFID_TEMP 的游客 Cookie 会被接口 403——必须提前识别。"""
    assert rep.looks_logged_out("UIFID_TEMP=abc") is True
    assert rep.looks_logged_out("ttwid=1; UIFID_TEMP=abc") is True
    assert rep.looks_logged_out("ttwid=1; sessionid=xyz") is False
    assert rep.looks_logged_out("sid_tt=xyz") is False
    assert rep.looks_logged_out("") is True


def test_read_cookie_from_conf(tmp_path):
    conf = tmp_path / "app.yaml"
    conf.write_text('mode: post\ncookie: "a=1; b=2"\nnaming: "{create}_{desc}"\n', encoding="utf-8")
    assert rep.read_cookie_from_conf(conf) == "a=1; b=2"
    assert rep.read_cookie_from_conf(tmp_path / "没有.yaml") == ""


def test_find_conf_path_prefers_nested_layout(tmp_path):
    """实测两种布局都存在过：<f2>/f2/conf/app.yaml 与 <f2>/conf/app.yaml。"""
    assert rep.find_conf_path(tmp_path) is None

    nested = _write(tmp_path / "f2" / "conf" / "app.yaml", b"cookie: x")
    assert rep.find_conf_path(tmp_path) == nested

    flat = _write(tmp_path / "conf" / "app.yaml", b"cookie: y")
    assert rep.find_conf_path(tmp_path) == nested  # 嵌套布局优先

    nested.unlink()
    assert rep.find_conf_path(tmp_path) == flat


def test_resolve_cookie_prefers_flag_then_file_then_conf(tmp_path):
    """优先级：--cookie > --cookie-file > f2 配置。"""
    _write(tmp_path / "f2" / "conf" / "app.yaml", b'cookie: "from_conf=1"\n')
    cfile = tmp_path / "douyin_cookies.json"
    cfile.write_text('[{"name":"sessionid","value":"from_file"}]', encoding="utf-8")

    class Args:
        pass

    args = Args()
    args.cookie = ""
    args.cookie_file = ""
    args.conf = ""
    assert rep.resolve_cookie(args, tmp_path) == "from_conf=1"

    args.cookie_file = str(cfile)
    assert rep.resolve_cookie(args, tmp_path) == "sessionid=from_file"

    args.cookie = "sessionid=from_flag"
    assert rep.resolve_cookie(args, tmp_path) == "sessionid=from_flag"


def test_resolve_cookie_reads_browser_extension_export(tmp_path):
    """回归（用户实际路径）：浏览器插件导出的 JSON 数组要能被 --cookie-file 吃下。

    与 storage/cookies/xiaohongshu_cookies.json 同一种形态；且识别出这是**登录态**
    （有 sessionid），不会误报游客态提示。
    """
    path = tmp_path / "douyin_cookies.json"
    path.write_text(
        json.dumps(
            [
                {"name": "sessionid", "value": "abc123", "domain": ".douyin.com"},
                {"name": "UIFID_TEMP", "value": "tmp", "domain": ".douyin.com"},
            ]
        ),
        encoding="utf-8",
    )

    class Args:
        pass

    args = Args()
    args.cookie = ""
    args.cookie_file = str(path)
    args.conf = ""

    cookie = rep.resolve_cookie(args, tmp_path)
    assert cookie == "sessionid=abc123; UIFID_TEMP=tmp"
    assert rep.looks_logged_out(cookie) is False


# ═══════════════════════════════════════════════════════════════
#  文件名主干（对齐键）
# ═══════════════════════════════════════════════════════════════


def _parsed(name: str, author_dir: str = "A"):
    from scripts import import_f2_downloads as f2

    return f2.parse_media_filename(Path(author_dir) / name, author_dir)


def test_disk_work_stem_post_and_like():
    post = _parsed("2025-01-01 10-00-00_标题_image_1.webp", "A")
    assert rep.disk_work_stem(post, like_mode=False) == "2025-01-01 10-00-00_标题"
    assert rep.disk_work_stem(post, like_mode=True) == "A_2025-01-01 10-00-00_标题"

    like = _parsed("不养羊_2025-01-01 10-00-00_标题_image_1.webp", "我的账号")
    assert rep.disk_work_stem(like, like_mode=True) == "不养羊_2025-01-01 10-00-00_标题"


@requires_f2
def test_api_stem_matches_disk_stem_for_same_work():
    """不变量：接口侧期望主干必须与磁盘侧主干**逐字符一致**。

    两边都过 f2 自己的 replaceT/split_filename；差一个字符就会全判未命中。
    """
    created = "2025-01-01 10-00-00"
    raw_desc = "下一站再见吧#地铁jk #jk"
    disk = _parsed(f"{created}_下一站再见吧#地铁jk_#jk_image_1.webp", "不养羊")

    assert rep.disk_work_stem(disk, like_mode=False) == rep.api_work_stem(
        created, raw_desc, like_mode=False
    )


@requires_f2
def test_api_stem_matches_disk_stem_like_mode():
    created = "2025-01-01 10-00-00"
    raw_desc = "#jk 穿搭"
    disk = _parsed(f"不养羊_{created}_#jk_穿搭_image_2.webp", "我的账号")

    assert rep.disk_work_stem(disk, like_mode=True) == rep.api_work_stem(
        created, raw_desc, like_mode=True, nickname="不养羊"
    )


@requires_f2
def test_api_stem_truncates_long_desc_like_f2():
    """超长描述：f2 会中段截断，接口侧期望值必须同样截断（否则永远未命中）。"""
    created = "2025-01-01 10-00-00"
    long_desc = "穿搭" * 200

    stem = rep.api_work_stem(created, long_desc, like_mode=False)

    assert "......" in stem  # f2 的截断标记
    assert len(stem) < len(long_desc)


# ═══════════════════════════════════════════════════════════════
#  对齐分档
# ═══════════════════════════════════════════════════════════════


def _work(aweme_id: str, stem: str) -> rep.ApiWork:
    return rep.ApiWork(aweme_id=aweme_id, created="", desc="", stem=stem)


def test_classify_unique_ambiguous_unmatched():
    api = [_work("111", "k1"), _work("222", "k2"), _work("333", "k2")]
    result = rep.classify_alignment(["k1", "k2", "k3"], api)

    assert result["unique"] == [("k1", "111")]
    assert result["ambiguous"] == [("k2", ["222", "333"])]
    assert result["unmatched"] == ["k3"]
    assert result["counts"] == {
        "disk_works": 3,
        "api_works": 3,
        "unique": 1,
        "ambiguous": 1,
        "unmatched": 1,
        "api_only": 0,
        "coverage": round(1 / 3, 4),
    }


def test_classify_api_only_counts_works_not_on_disk():
    """接口有、磁盘没有：说明该作品没下载或被删，只作信息性统计。"""
    api = [_work("111", "k1"), _work("999", "k9")]
    result = rep.classify_alignment(["k1"], api)

    assert result["api_only"] == ["999"]
    assert result["counts"]["coverage"] == 1.0


def test_classify_empty_disk_is_safe():
    """磁盘为空时不能除零。"""
    result = rep.classify_alignment([], [_work("111", "k1")])
    assert result["counts"]["coverage"] == 0.0
    assert result["counts"]["disk_works"] == 0
    assert result["api_only"] == ["111"]


def test_classify_does_not_fuzzy_match():
    """空描述的作品在接口侧会被替换成 `_`，不能与「描述不同」的磁盘项混为一谈。

    回归点：任何模糊/前缀匹配都可能把 A 作品的链接写到 B 素材上。
    """
    api = [_work("111", "2025-01-01 10-00-00_")]
    result = rep.classify_alignment(["2025-01-01 10-00-00_其他"], api)

    assert result["unmatched"] == ["2025-01-01 10-00-00_其他"]
    assert result["unique"] == []


def test_merge_reports_aggregates_and_recomputes_coverage():
    a = rep.classify_alignment(["k1"], [_work("111", "k1")])
    b = rep.classify_alignment(["k1", "k2"], [_work("222", "k1")])
    merged = rep.merge_reports({"甲": a, "乙": b})

    totals = merged["totals"]
    assert totals["authors"] == 2
    assert totals["disk_works"] == 3
    assert totals["unique"] == 2
    assert totals["unmatched"] == 1
    assert totals["coverage"] == round(2 / 3, 4)


def test_render_report_mentions_all_three_buckets():
    report = rep.merge_reports(
        {"甲": rep.classify_alignment(["k1", "k2"], [_work("111", "k1")])}
    )
    text = rep.render_report(report)

    assert "覆盖率" in text
    assert "只读" in text
    assert "未命中" in text
    assert "111" in text


# ═══════════════════════════════════════════════════════════════
#  磁盘侧归集
# ═══════════════════════════════════════════════════════════════


def test_collect_disk_works_groups_by_normalized_author(tmp_path):
    """同一作者被拆到 `不养羊` / `不养羊√` 两个目录时要合并（与导入侧口径一致）。"""
    _write(tmp_path / "不养羊" / "2025-01-01 10-00-00_甲_image_1.webp")
    _write(tmp_path / "不养羊" / "2025-01-01 10-00-00_甲_image_2.webp")
    _write(tmp_path / "不养羊√" / "2025-01-02 10-00-00_乙_image_1.webp")
    _write(tmp_path / "Jade7" / "2025-01-03 10-00-00_丙_video.mp4")

    grouped = rep.collect_disk_works(tmp_path, "post")

    # 归一化会去掉尾部的标记：`不养羊√` → `不养羊`、`Jade7` → `Jade`
    assert set(grouped) == {"不养羊", "Jade"}
    assert grouped["不养羊"] == [
        "2025-01-01 10-00-00_甲",
        "2025-01-02 10-00-00_乙",
    ]
    assert grouped["Jade"] == ["2025-01-03 10-00-00_丙"]


def test_collect_disk_works_like_mode_keeps_author_prefix(tmp_path):
    """点赞模式：原作者在文件名里而不是目录名，主干要带作者前缀。"""
    _write(tmp_path / "我的账号" / "不养羊_2025-01-01 10-00-00_甲_image_1.webp")

    grouped = rep.collect_disk_works(tmp_path, "like")

    assert grouped == {"不养羊": ["不养羊_2025-01-01 10-00-00_甲"]}


def test_collect_disk_works_empty_root(tmp_path):
    assert rep.collect_disk_works(tmp_path / "不存在", "post") == {}


# ═══════════════════════════════════════════════════════════════
#  磁盘侧风险画像（不联网可得）
# ═══════════════════════════════════════════════════════════════


def test_analyze_disk_keys_counts_duplicate_stems():
    """同作者内主干重复 → 对齐键相撞，接口侧若 ≥2 条即必然多义。"""
    disk = {"甲": ["k1", "k1", "k2"], "乙": ["k3"]}
    analysis = rep.analyze_disk_keys(disk)

    assert analysis["works"] == 4
    assert analysis["authors"] == 2
    assert analysis["duplicate_stems"] == 2  # k1 出现两次
    assert analysis["duplicate_examples"][0]["author"] == "甲"


def test_analyze_disk_keys_flags_placeholder_time():
    """实测存在：f2 拿到的 create_time 是整点 0 分 0 秒（网易第五人格 2 件）。"""
    disk = {
        "甲": [
            "2026-08-27 00-00-00__是非_对错",
            "2026-08-28 13-37-36_正常",
        ]
    }
    analysis = rep.analyze_disk_keys(disk)

    assert analysis["placeholder_time"] == 1
    assert analysis["missing_time"] == 0
    assert "00-00-00" in analysis["placeholder_examples"][0]


def test_analyze_disk_keys_flags_missing_time():
    disk = {"甲": ["没有时间戳的主干"]}
    analysis = rep.analyze_disk_keys(disk)

    assert analysis["missing_time"] == 1
    assert analysis["placeholder_time"] == 0


def test_analyze_disk_keys_counts_empty_and_truncated_body():
    disk = {
        "甲": [
            "2026-08-27 10-00-00_",  # 描述为空
            "2026-08-27 11-00-00_前半......后半",  # 被 f2 中段截断
        ]
    }
    analysis = rep.analyze_disk_keys(disk)

    assert analysis["empty_body"] == 1
    assert analysis["truncated_body"] == 1


def test_analyze_disk_keys_like_mode_prefix_still_finds_time():
    """点赞模式主干带作者前缀，时间戳扫描不能因此失效。"""
    disk = {"不养羊": ["不养羊_2026-08-27 00-00-00_标题"]}
    analysis = rep.analyze_disk_keys(disk)

    assert analysis["placeholder_time"] == 1
    assert analysis["missing_time"] == 0


def test_analyze_disk_keys_clean_data_is_all_zero():
    disk = {"甲": ["2026-08-27 13-37-36_正常描述", "2026-08-28 10-00-00_另一条"]}
    analysis = rep.analyze_disk_keys(disk)

    assert analysis["duplicate_stems"] == 0
    assert analysis["placeholder_time"] == 0
    assert analysis["missing_time"] == 0


def test_render_disk_analysis_mentions_risk_buckets():
    text = rep.render_disk_analysis(rep.analyze_disk_keys({"甲": ["k1"]}))

    assert "主干重复" in text
    assert "00-00-00" in text
    assert "不联网" in text


def test_render_report_includes_disk_analysis_when_present():
    report = rep.merge_reports({"甲": rep.classify_alignment(["k1"], [])})
    report["disk_analysis"] = rep.analyze_disk_keys({"甲": ["k1"]})

    assert "磁盘侧对齐风险画像" in rep.render_report(report)


# ═══════════════════════════════════════════════════════════════
#  作者库
# ═══════════════════════════════════════════════════════════════


def _author_db(f2_dir: Path, rows) -> None:
    f2_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f2_dir / rep.F2_AUTHOR_DB)
    conn.execute("CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT)")
    for sec, nick in rows:
        conn.execute("INSERT INTO user_info_web VALUES (?, ?)", (sec, nick))
    conn.commit()
    conn.close()


def test_load_author_sec_ids_normalizes_nickname(tmp_path):
    _author_db(tmp_path, [("sec1", "里香1√"), ("sec2", "娜娜瑜")])
    assert rep.load_author_sec_ids(tmp_path) == {"里香": "sec1", "娜娜瑜": "sec2"}


def test_load_author_sec_ids_tolerates_missing(tmp_path):
    assert rep.load_author_sec_ids(tmp_path / "没有") == {}


# ═══════════════════════════════════════════════════════════════
#  CLI：--disk-only 不发请求
# ═══════════════════════════════════════════════════════════════


def test_main_disk_only_makes_no_requests(tmp_path, capsys):
    """--disk-only 只扫磁盘：没有 Cookie 也必须成功返回，并给出风险画像。"""
    root = tmp_path / "Download" / "douyin" / "post"
    _write(root / "A" / "2025-01-01 10-00-00_甲_image_1.webp")

    code = rep.main(["--f2-dir", str(tmp_path), "--disk-only"])

    assert code == 0
    out = capsys.readouterr().out
    assert "作者 1 个" in out
    assert "磁盘侧对齐风险画像" in out


def test_main_without_cookie_fails_with_actionable_hint(tmp_path, capsys):
    root = tmp_path / "Download" / "douyin" / "post"
    _write(root / "A" / "2025-01-01 10-00-00_甲_image_1.webp")

    code = rep.main(["--f2-dir", str(tmp_path)])

    assert code == 1
    out = capsys.readouterr().out
    assert "403" in out and "Cookie" in out


def test_main_empty_disk_returns_error(tmp_path, capsys):
    assert rep.main(["--f2-dir", str(tmp_path)]) == 1
    assert "未扫到任何作品" in capsys.readouterr().out


# ═══════════════════════════════════════════════════════════════
#  报告落盘
# ═══════════════════════════════════════════════════════════════


def test_main_writes_json_report(tmp_path, monkeypatch, capsys):
    """跑通「枚举 → 分档 → 落盘」主干（接口调用被打桩，不发网络请求）。"""
    root = tmp_path / "Download" / "douyin" / "post"
    _write(root / "A" / "2025-01-01 10-00-00_甲_image_1.webp")
    _author_db(tmp_path, [("sec1", "A")])

    async def fake_enum(sec, cookie, **kwargs):
        return [_work("111", "2025-01-01 10-00-00_甲")]

    monkeypatch.setattr(rep, "enumerate_author_works", fake_enum)
    out = tmp_path / "report.json"

    code = rep.main(
        ["--f2-dir", str(tmp_path), "--cookie", "sessionid=x; ttwid=y", "--output", str(out)]
    )

    assert code == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["totals"]["unique"] == 1
    assert report["totals"]["coverage"] == 1.0
    assert "111" in capsys.readouterr().out


def test_main_skips_author_missing_from_f2_db(tmp_path, monkeypatch, capsys):
    root = tmp_path / "Download" / "douyin" / "post"
    _write(root / "A" / "2025-01-01 10-00-00_甲_image_1.webp")

    code = rep.main(["--f2-dir", str(tmp_path), "--cookie", "sessionid=x"])

    assert code == 1
    assert "没有 sec_user_id" in capsys.readouterr().out
