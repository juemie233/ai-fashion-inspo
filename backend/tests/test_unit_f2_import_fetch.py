"""f2 下载侧单元测试：命令拼装、作者解析、拉取结果汇总。"""

import os
import sqlite3
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from f2_patch import patch_f2
from scripts import import_f2_downloads as f2
from scripts.scraper_common import utcnow
from f2_import_unit_helpers import (  # noqa: F401
    SEC,
    AWEME,
    _write,
    _fake_tree,
    _make_lib,
    _decisions,
    _jpeg,
    _fake_mp4,
    _import_lib,
    _f2_dir_with_authors,
    _f2_dir_with_video_db,
    _imported_fixture,
    _new_name,
)


def test_run_fetch_skips_unregistered_authors(tmp_path, monkeypatch):
    """回归：一键/CLI 下载默认跳过未登记账号，不再把官方号的作品拉进下载目录。"""
    f2_dir = tmp_path / "f2proj"
    f2_dir.mkdir()
    conn = sqlite3.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, aweme_count INTEGER)"
    )
    conn.executemany(
        "INSERT INTO user_info_web VALUES (?, ?, 1)",
        [("MS4x_lixiang", "里香1√"), ("MS4x_game", "网易第五人格")],
    )
    conn.commit()
    conn.close()

    db = tmp_path / "lib.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE bloggers (id INTEGER PRIMARY KEY, name TEXT, platform TEXT, "
        "platform_user_id TEXT)"
    )
    conn.execute("INSERT INTO bloggers VALUES (1, '里香', 'douyin', 'MS4x_lixiang')")
    conn.commit()
    conn.close()
    patch_f2(monkeypatch, "library_db_path", lambda: db)

    commands: list[str] = []
    result = f2.run_fetch(
        f2_dir=f2_dir,
        download_root=tmp_path / "Download",
        runner=lambda cmd, cwd: (commands.append(cmd[-1]) or (0, "")),
    )

    assert result["total"] == 1  # 只下 里香1√
    assert result["skipped_authors"] == ["网易第五人格"]
    assert all("网易第五人格" not in cmd for cmd in commands)

    # 显式放开：两个账号都下
    commands.clear()
    forced = f2.run_fetch(
        f2_dir=f2_dir,
        download_root=tmp_path / "Download",
        runner=lambda cmd, cwd: (commands.append(cmd[-1]) or (0, "")),
        include_unknown=True,
    )
    assert forced["total"] == 2 and forced["skipped_authors"] == []



def test_build_f2_one_command_flags():
    """单作品反查命令：`-M one` + 作品链接 + 命名模板 + 下载根。"""
    url = "https://www.douyin.com/note/7624898240707091963"
    cmd = f2.build_f2_one_command(url, download_root=Path("D:/tmp/f2resolve"))

    assert cmd[1:4] == ["-m", "f2", "dy"]
    assert cmd[cmd.index("-u") + 1] == url
    assert cmd[cmd.index("-M") + 1] == "one"
    assert cmd[cmd.index("-n") + 1] == f2.POST_NAMING_TEMPLATE
    assert cmd[cmd.index("-p") + 1] == str(Path("D:/tmp/f2resolve"))



def test_build_f2_one_command_omits_optional_flags():
    cmd = f2.build_f2_one_command("https://www.douyin.com/note/1")
    assert "-p" not in cmd and "--auto-cookie" not in cmd



def test_resolve_author_by_aweme_succeeds_on_new_user_row_despite_exit_code(tmp_path):
    """成功信号是「f2 用户库多了一行」，**不是退出码**。

    实测 f2 跑 `-M one` 必然以退出码 1 结束：它写完作者资料后去写 ``video_info``，
    而该 checkout 给作品数据加了 ``caption`` 字段却没同步表结构（`table video_info
    has no column named caption`）。作者资料落在崩溃之前——这里把退出码固定成 1，
    锁死「不看退出码」这个口径，防止有人「顺手」把它改成 rc == 0 才算成功。
    """
    f2_dir = _f2_dir_with_authors(tmp_path)

    def fake_runner(cmd, cwd):
        conn = sqlite3.connect(Path(cwd) / f2.F2_AUTHOR_DB)
        conn.execute(
            "INSERT INTO user_info_web VALUES (?, ?, ?)",
            ("MS4wLjABAAAAnew", "1123木头人", 27),
        )
        conn.commit()
        conn.close()
        return 1, ""

    res = f2.resolve_f2_author_by_aweme(
        f2_dir, "7624898240707091963", runner=fake_runner
    )

    assert res["ok"] is True
    assert res["sec_user_id"] == "MS4wLjABAAAAnew"
    assert res["nickname"] == "1123木头人"
    assert res["detected"] == "user_info_new"
    assert res["rc"] == 1  # 退出码 1 但结果有效
    assert res["cmd"][res["cmd"].index("-M") + 1] == "one"



def test_resolve_author_by_aweme_uses_default_note_url(tmp_path):
    """只给作品 ID 时按 `note/<id>` 拼链接（实测图集作品这条链接可用）。"""
    f2_dir = _f2_dir_with_authors(tmp_path)
    seen: list[list[str]] = []

    def fake_runner(cmd, cwd):
        seen.append(cmd)
        return 0, ""

    f2.resolve_f2_author_by_aweme(f2_dir, "7624898240707091963", runner=fake_runner)
    assert seen[0][seen[0].index("-u") + 1] == (
        "https://www.douyin.com/note/7624898240707091963"
    )



def test_resolve_author_by_aweme_falls_back_to_video_info(tmp_path):
    """没有新增行（作者已在 f2 库里）时退回作品级映射。"""
    f2_dir = _f2_dir_with_authors(tmp_path)
    conn = sqlite3.connect(f2_dir / f2.F2_VIDEO_DB)
    conn.execute(
        "CREATE TABLE video_info (aweme_id TEXT, sec_user_id TEXT, nickname TEXT)"
    )
    conn.execute(
        "INSERT INTO video_info VALUES (?, ?, ?)",
        ("7624898240707091963", "MS4wLjABAAAAold", "1123木头人"),
    )
    conn.commit()
    conn.close()

    res = f2.resolve_f2_author_by_aweme(
        f2_dir, "7624898240707091963", runner=lambda cmd, cwd: (0, "")
    )
    assert res["ok"] is True
    assert res["sec_user_id"] == "MS4wLjABAAAAold"
    assert res["detected"] == "video_info"



def test_resolve_author_by_aweme_reports_reason_when_nothing_new(tmp_path):
    """既没新增行、也没有作品级映射 → 明确失败原因（不猜）。"""
    f2_dir = _f2_dir_with_authors(tmp_path)
    res = f2.resolve_f2_author_by_aweme(
        f2_dir, "7624898240707091963", runner=lambda cmd, cwd: (1, "")
    )
    assert res["ok"] is False
    assert res["sec_user_id"] == ""
    assert "没有新增作者记录" in res["reason"]



def test_resolve_author_by_aweme_rejects_ambiguous_multiple_new_rows(tmp_path):
    """一次多出两个新账号时不猜是谁（宁可不填，也不写错 ID）。"""
    f2_dir = _f2_dir_with_authors(tmp_path)

    def fake_runner(cmd, cwd):
        conn = sqlite3.connect(Path(cwd) / f2.F2_AUTHOR_DB)
        conn.executemany(
            "INSERT INTO user_info_web VALUES (?, ?, ?)",
            [("MS4wLjABAAAAx", "甲", 1), ("MS4wLjABAAAAy", "乙", 1)],
        )
        conn.commit()
        conn.close()
        return 0, ""

    res = f2.resolve_f2_author_by_aweme(
        f2_dir, "7624898240707091963", runner=fake_runner
    )
    assert res["ok"] is False
    assert "无法确定" in res["reason"]



def test_resolve_author_by_aweme_cleans_temp_download_root(tmp_path):
    """反查用的临时产物目录用完即删（f2 一旦不崩就会真下这个作品）。"""
    f2_dir = _f2_dir_with_authors(tmp_path)
    roots: list[Path] = []

    def fake_runner(cmd, cwd):
        roots.append(Path(cmd[cmd.index("-p") + 1]))
        (roots[-1] / "douyin" / "post" / "某作者").mkdir(parents=True, exist_ok=True)
        return 1, ""

    f2.resolve_f2_author_by_aweme(f2_dir, "7624898240707091963", runner=fake_runner)
    assert roots and not roots[0].exists()



def test_resolve_author_by_aweme_reports_runner_failure(tmp_path):
    """执行器抛异常 → 明确的失败原因，不向上抛（调用方据此提示用户）。"""
    f2_dir = _f2_dir_with_authors(tmp_path)

    def boom(cmd, cwd):
        raise OSError("f2 起不来")

    res = f2.resolve_f2_author_by_aweme(f2_dir, "1", runner=boom)
    assert res["ok"] is False
    assert "OSError" in res["reason"]



def test_resolve_author_by_aweme_reports_timeout(tmp_path):
    """反查超时 → 专门的原因文案（不是含糊的「调用 f2 失败」）。

    为什么要限时：反查是**同步**跑在请求线程里的，f2 一旦卡在风控/登录页，不限时就会让
    子进程与请求线程一直挂着（前端超时了服务端还在跑）。
    """
    f2_dir = _f2_dir_with_authors(tmp_path)

    def slow(cmd, cwd):
        raise subprocess.TimeoutExpired(cmd="f2", timeout=1)

    res = f2.resolve_f2_author_by_aweme(f2_dir, "1", runner=slow)
    assert res["ok"] is False
    assert "超时" in res["reason"]
    assert str(int(f2.RESOLVE_RUN_TIMEOUT_S)) in res["reason"]



def test_build_f2_command_default_flags():
    """默认命令：主页作品 + 全部日期 + 指定下载根 + **带作品 ID 的命名模板**。"""
    author = {"sec_user_id": "MS4wLjABAAAAaaa", "nickname": "里香"}
    cmd = f2.build_f2_command(author, download_root=Path("D:/f2/Download"))

    assert cmd[1:4] == ["-m", "f2", "dy"]
    assert "-u" in cmd and cmd[cmd.index("-u") + 1] == "https://www.douyin.com/user/MS4wLjABAAAAaaa"
    assert cmd[cmd.index("-M") + 1] == "post"
    assert cmd[cmd.index("-i") + 1] == "all"
    assert cmd[cmd.index("-p") + 1] == str(Path("D:/f2/Download"))
    assert "--auto-cookie" not in cmd
    # 缺省必须显式传 -n 且含 {aweme_id}：不传时 f2 用配置里的 {create}_{desc}，
    # 新素材会继续丢失真实作品 ID（无法回填原帖链接）
    assert cmd[cmd.index("-n") + 1] == f2.POST_NAMING_TEMPLATE
    assert "{aweme_id}" in f2.POST_NAMING_TEMPLATE



def test_build_f2_command_optional_flags():
    author = {"sec_user_id": "sec1", "nickname": "A"}
    cmd = f2.build_f2_command(author, naming="{create}_{desc}", auto_cookie="chrome")
    assert cmd[cmd.index("-n") + 1] == "{create}_{desc}"
    assert cmd[cmd.index("--auto-cookie") + 1] == "chrome"



def test_build_f2_command_passes_date_window():
    """日期窗口必须真的进命令行：f2 在 `-i all` 时不会提前结束翻页。"""
    author = {"sec_user_id": "sec1", "nickname": "A"}
    cmd = f2.build_f2_command(author, interval="2026-08-27|2026-09-10")
    assert cmd[cmd.index("-i") + 1] == "2026-08-27|2026-09-10"
    # 空/缺省回落 all，避免拼出非法参数
    assert f2.build_f2_command(author, interval="")[cmd.index("-i") + 1] == "all"



def test_run_fetch_passes_per_author_window(tmp_path):
    """每个作者按自己的目录 mtime 取窗口：新博主全量，久未更新的自动放大。"""
    f2_dir = _f2_dir_with_authors(
        tmp_path,
        authors=[
            ("MS4wLjABAAAAaaa", "里香1√", 171),
            ("MS4wLjABAAAAbbb", "娜娜瑜√", 232),
        ],
    )
    post_root = f2_dir / f2.F2_DOWNLOAD_SUBDIR
    (post_root / "里香1√").mkdir(parents=True)
    # 里香：上次下载是 60 天前 → 窗口应放大到那天之前；娜娜瑜：目录不存在 → 最小窗口
    stamp = (utcnow() - timedelta(days=60)).timestamp()
    os.utime(post_root / "里香1√", (stamp, stamp))

    seen: list[list[str]] = []
    runner = lambda cmd, cwd: (seen.append(cmd) or 0, "")  # noqa: E731

    result = f2.run_fetch(f2_dir, runner=runner, since_days=14)

    intervals = [cmd[cmd.index("-i") + 1] for cmd in seen]
    assert all("|" in value for value in intervals)  # 都是窗口，不再是 all
    widened = intervals[0]
    assert widened.startswith(f"{(utcnow() - timedelta(days=61)).date():%Y-%m-%d}")
    assert intervals[1] == f2.compute_fetch_interval(14, None)
    assert result["windows"] == {widened: 1, intervals[1]: 1}
    assert result["results"][0]["interval"] == widened

    # since_days=0 → 全历史（首次全量）
    seen.clear()
    f2.run_fetch(f2_dir, runner=runner, since_days=0)
    assert all(cmd[cmd.index("-i") + 1] == "all" for cmd in seen)



def test_run_fetch_invokes_f2_per_author_and_continues_on_failure(tmp_path):
    """逐作者串行调用；某个作者失败只记录并继续（cwd 必须是 f2 工作目录）。"""
    f2_dir = _f2_dir_with_authors(tmp_path)
    calls: list[tuple[list[str], Path]] = []

    def fake_runner(cmd, cwd):
        calls.append((cmd, cwd))
        return (1 if "娜娜瑜" in " ".join(cmd) or len(calls) == 2 else 0), ""

    result = f2.run_fetch(f2_dir, runner=fake_runner)

    assert result["total"] == 2 and result["ok"] == 1 and result["failed"] == 1
    assert len(calls) == 2  # 失败后继续跑下一个作者
    assert all(cwd == f2_dir for _cmd, cwd in calls)  # f2 的 cwd 必须是其工作目录
    assert [r["nickname"] for r in result["results"]] == ["里香1√", "娜娜瑜√"]
    assert result["results"][1]["rc"] == 1



def test_run_fetch_author_filter_and_limit(tmp_path):
    """--authors 复用归一化匹配（与导入阶段语义一致）；--fetch-limit 截断。"""
    f2_dir = _f2_dir_with_authors(tmp_path)
    seen: list[str] = []
    runner = lambda cmd, cwd: (seen.append(" ".join(cmd)) or 0, "")  # noqa: E731

    result = f2.run_fetch(f2_dir, authors={"里香"}, runner=runner)
    assert result["total"] == 1 and len(seen) == 1

    result = f2.run_fetch(f2_dir, limit=1, runner=runner)
    assert result["total"] == 1



def test_run_fetch_runner_exception_does_not_abort(tmp_path):
    """执行器抛异常（如 f2 崩溃）也只算该作者失败。"""
    f2_dir = _f2_dir_with_authors(tmp_path)

    def boom(cmd, cwd):
        raise OSError("f2 崩溃")

    result = f2.run_fetch(f2_dir, runner=boom)
    assert result["failed"] == 2 and result["ok"] == 0



def test_run_fetch_without_author_db_reports_error(tmp_path):
    result = f2.run_fetch(tmp_path / "不存在", runner=lambda cmd, cwd: (0, ""))
    assert result["total"] == 0 and result["error"]



def test_build_f2_like_command_shape():
    """点赞命令：-M like + 带 {nickname}/{aweme_id} 的命名模板 + **不传 `-i`**。

    原作者只存在于文件名里，作品 ID 同理（点赞列表跨作者，缺一不可）。

    回归点（2026-09 读 f2 源码更正）：f2 的点赞模式 `handle_user_like` /
    `fetch_user_like_videos` **都不读 `interval`**，`-i` 纯属无效参数——此前传
    `-i all` 并注释「f2 按发布时间过滤，窗口会漏掉最近点赞的老视频」，那描述的是
    一个不存在的过滤器，会误导后人以为能用 `-i` 收窄点赞增量。
    """
    cmd = f2.build_f2_like_command(
        "MS4wLjABAAAAme", download_root=Path("D:/f2/Download")
    )

    assert cmd[:4] == [sys.executable, "-m", "f2", "dy"]
    assert cmd[cmd.index("-u") + 1] == "https://www.douyin.com/user/MS4wLjABAAAAme"
    assert cmd[cmd.index("-M") + 1] == "like"
    assert "-i" not in cmd  # 点赞模式不读 interval，传了是无效参数
    assert cmd[cmd.index("-n") + 1] == f2.LIKE_NAMING_TEMPLATE
    assert f2.LIKE_NAMING_TEMPLATE == "{nickname}_{create}_{desc}_{aweme_id}"
    assert cmd[cmd.index("-p") + 1] == str(Path("D:/f2/Download"))
    assert "-o" not in cmd  # 缺省全量翻到底（行为与改造前一致）



def test_build_f2_like_command_incremental_adds_max_counts():
    """增量模式：`max_counts>0` 时传 `-o`（唯一能收窄 f2 点赞翻页量的参数）。

    为什么需要：f2 的点赞分页没有「遇到已下载就停」，每页还固定 sleep 一次 timeout；
    点赞列表最新在前，只翻最近 N 条即可覆盖新增。
    """
    cmd = f2.build_f2_like_command("MS4wLjABAAAAme", max_counts=150)

    assert cmd[cmd.index("-o") + 1] == "150"
    assert "-i" not in cmd

    # 0 / 负数都视为「全量」，不拼出非法参数
    assert "-o" not in f2.build_f2_like_command("sec", max_counts=0)
    assert "-o" not in f2.build_f2_like_command("sec", max_counts=-5)



def test_build_f2_collect_command_shape():
    """收藏命令：-M collection + 带 {nickname}/{aweme_id} 的命名模板 + **不传 `-i`**。

    与点赞模式同形（都是「我的列表」），但 mode 与产物目录必须分开——否则收藏会被下到
    like 目录、与「我的喜欢」混在一起，扫描时张冠李戴。
    """
    cmd = f2.build_f2_collect_command(
        "MS4wLjABAAAAme", download_root=Path("D:/f2/Download")
    )

    assert cmd[:4] == [sys.executable, "-m", "f2", "dy"]
    assert cmd[cmd.index("-u") + 1] == "https://www.douyin.com/user/MS4wLjABAAAAme"
    assert cmd[cmd.index("-M") + 1] == "collection"
    assert "-i" not in cmd  # 收藏模式同样不读 interval
    assert cmd[cmd.index("-n") + 1] == f2.COLLECT_NAMING_TEMPLATE
    # 原作者与真实作品 ID 必须留在文件名里（判重与绑博主的依据）
    assert "{nickname}" in f2.COLLECT_NAMING_TEMPLATE
    assert "{aweme_id}" in f2.COLLECT_NAMING_TEMPLATE
    assert cmd[cmd.index("-p") + 1] == str(Path("D:/f2/Download"))
    assert "-o" not in cmd  # 缺省全量翻到底

    # 产物根目录与点赞分开（扫描/入库按模式取对应目录）
    assert f2.DEFAULT_F2_COLLECT_ROOT != f2.DEFAULT_F2_LIKE_ROOT
    assert "collection" in str(f2.DEFAULT_F2_COLLECT_ROOT)



def test_run_fetch_collects_requires_user_and_uses_collect_mode(tmp_path):
    """没配主页链接给中文错误且不调 f2；配了则跑收藏命令并标明「我的收藏」。"""
    calls: list[list[str]] = []
    missing = f2.run_fetch_collects(
        tmp_path, "", runner=lambda cmd, cwd: (calls.append(cmd) or (0, ""))
    )
    assert missing["total"] == 0 and "我的主页链接" in missing["error"]
    assert "我的收藏" in missing["error"]
    assert calls == [], "缺配置时不该调 f2"

    seen: list[list[str]] = []
    ok = f2.run_fetch_collects(
        tmp_path,
        "MS4wLjABAAAAme",
        max_counts=30,
        runner=lambda cmd, cwd: (seen.append(cmd) or (0, "")),
    )
    assert ok["total"] == 1 and ok["ok"] == 1 and ok["failed"] == 0
    assert ok["results"][0]["nickname"] == "我的收藏"
    assert ok["results"][0]["max_counts"] == 30
    assert seen and seen[0][seen[0].index("-M") + 1] == "collection"
    assert seen[0][seen[0].index("-o") + 1] == "30"



def test_run_fetch_likes_reports_and_requires_user(tmp_path):
    """没配主页链接直接给出可操作错误；配了则跑一条命令并汇报退出码。"""
    missing = f2.run_fetch_likes(tmp_path, "")
    assert missing["total"] == 0 and "未配置" in missing["error"]

    calls: list[list[str]] = []
    ok = f2.run_fetch_likes(
        tmp_path, "MS4wLjABAAAAme", runner=lambda cmd, cwd: (calls.append(cmd) or (0, ""))
    )
    assert ok["total"] == 1 and ok["ok"] == 1 and ok["failed"] == 0
    assert calls and calls[0][calls[0].index("-M") + 1] == "like"

    bad = f2.run_fetch_likes(
        tmp_path, "MS4wLjABAAAAme", runner=lambda cmd, cwd: (1, "cookie 失效")
    )
    assert bad["failed"] == 1 and bad["ok"] == 0



def test_run_fetch_likes_passes_max_counts_and_reports_it(tmp_path):
    """增量条数要真的进命令行，并回显在结果里（任务结果据此显示「最近 N 条」）。"""
    seen: list[list[str]] = []
    result = f2.run_fetch_likes(
        tmp_path,
        "MS4wLjABAAAAme",
        max_counts=120,
        runner=lambda cmd, cwd: (seen.append(cmd) or (0, "")),
    )

    assert result["max_counts"] == 120
    assert result["results"][0]["max_counts"] == 120
    assert seen and seen[0][seen[0].index("-o") + 1] == "120"



def test_run_fetch_likes_defaults_to_full_pagination(tmp_path):
    """缺省 max_counts=0 → 不传 `-o`（全量，行为与改造前一致）。"""
    seen: list[list[str]] = []
    result = f2.run_fetch_likes(
        tmp_path,
        "MS4wLjABAAAAme",
        runner=lambda cmd, cwd: (seen.append(cmd) or (0, "")),
    )

    assert result["max_counts"] == 0
    assert seen and "-o" not in seen[0]



def test_run_fetch_profiles_downloads_named_blogger_not_in_f2_db(tmp_path):
    """核心用例（任务 351 的正面版本）：f2 用户库里**没有**这个账号，也能下。

    这是「给一个博主 → 下她全部作品」的关键：目标来自用户点名的 profiles，
    而不是 f2 自己的用户库。
    """
    f2_dir = _f2_dir_with_authors(tmp_path, authors=[("MS4wLjABAAAAother", "别人", 5)])
    calls: list[list[str]] = []

    result = f2.run_fetch(
        f2_dir,
        profiles=[f"https://www.douyin.com/user/{SEC}"],
        post_root=tmp_path / "post",  # 空目录 → 无本地下载记录
        runner=lambda cmd, cwd: (calls.append(cmd) or (0, "")),
    )

    assert result["total"] == 1 and result["ok"] == 1
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[cmd.index("-u") + 1] == f"https://www.douyin.com/user/{SEC}"
    # ⚠ 首次采某博主必须翻全量：默认窗口（14 天）只会拿到最近的几篇
    assert cmd[cmd.index("-i") + 1] == "all"
    # 只跑点名的那一个，没把 f2 库里的「别人」带上
    assert "别人" not in " ".join(cmd)



def test_run_fetch_profiles_ignores_registered_blogger_whitelist(tmp_path, monkeypatch):
    """点名博主不受「只下已登记博主」白名单影响——用户已经明确点名了。"""
    f2_dir = _f2_dir_with_authors(tmp_path, authors=[("MS4wLjABAAAAother", "别人", 5)])
    patch_f2(monkeypatch, "load_douyin_bloggers", lambda: {"里香": [{"id": 1}]})
    calls: list[list[str]] = []

    result = f2.run_fetch(
        f2_dir,
        profiles=[SEC],
        post_root=tmp_path / "post",
        runner=lambda cmd, cwd: (calls.append(cmd) or (0, "")),
    )

    assert result["total"] == 1
    assert result["skipped_authors"] == []  # 没走白名单那条路
    assert len(calls) == 1



def test_run_fetch_profiles_reports_invalid_input(tmp_path):
    f2_dir = _f2_dir_with_authors(tmp_path)

    result = f2.run_fetch(
        f2_dir,
        profiles=["72906514384", "v.douyin.com/abc/", SEC],
        post_root=tmp_path / "post",
        runner=lambda cmd, cwd: (0, ""),
    )

    assert result["invalid_profiles"] == ["72906514384", "v.douyin.com/abc/"]
    assert result["total"] == 1  # 只有合法的那个被下



def test_run_fetch_profiles_works_with_empty_f2_author_db(tmp_path):
    """f2 用户库为空时，点名博主仍要能下（不早退成「未找到 f2 作者清单」）。"""
    f2_dir = tmp_path / "empty"
    f2_dir.mkdir()

    result = f2.run_fetch(
        f2_dir,
        profiles=[SEC],
        post_root=tmp_path / "post",
        runner=lambda cmd, cwd: (0, ""),
    )

    assert "error" not in result
    assert result["total"] == 1



def test_run_fetch_profiles_returns_resolved_nicknames(tmp_path):
    """跑完要把点名博主解析成昵称回传，否则入库阶段的白名单无从下手。"""
    f2_dir = _f2_dir_with_authors(tmp_path, authors=[(SEC, "唐思瑶ya", 1582)])

    result = f2.run_fetch(
        f2_dir,
        profiles=[SEC],
        post_root=tmp_path / "post",
        runner=lambda cmd, cwd: (0, ""),
    )

    assert result["resolved_profiles"] == {SEC: "唐思瑶ya"}



def test_run_fetch_passes_naming_with_aweme_id(tmp_path):
    """端到端：run_fetch 构造的 f2 命令必须带含 {aweme_id} 的命名模板。"""
    f2_dir = _f2_dir_with_authors(tmp_path)
    calls: list[list[str]] = []

    result = f2.run_fetch(
        f2_dir, runner=lambda cmd, cwd: (calls.append(cmd) or (0, ""))
    )

    assert result["ok"] == 2
    assert len(calls) == 2
    for cmd in calls:
        assert "{aweme_id}" in cmd[cmd.index("-n") + 1]

