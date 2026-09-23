"""收藏夹枚举与按夹下载（scripts/f2_collects.py）单元测试。

不联网：把 f2 的 crawler / handler / utils 全部打桩，真实参与运算的只有本模块的
编排逻辑与 f2 自己的响应过滤器（过滤器不联网，喂进构造好的响应即可）。
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

from scripts import f2_collects
from scripts import import_f2_downloads as f2


def _folder_response(folders, has_more=False, cursor=0):
    """构造 collects/list/ 的响应（字段名与抖音接口一致，交给真实过滤器解析）。"""
    return {
        "status_code": 0,
        "cursor": cursor,
        "has_more": has_more,
        "total_number": len(folders),
        "collects_list": [
            {
                "collects_id": str(fid),
                "collects_name": name,
                "total_number": total,
                "last_collect_time": 1700000000 + i,
            }
            for i, (fid, name, total) in enumerate(folders)
        ],
    }


def _work_response(aweme_ids, has_more=False, cursor=0):
    """构造 collects/video/list/ 的响应（作品字段只填过滤器会读的那几个）。"""
    return {
        "status_code": 0,
        "cursor": cursor,
        "has_more": has_more,
        "aweme_list": [
            {
                "aweme_id": str(aid),
                "desc": f"作品{aid}",
                "create_time": 1700000000,
                "author": {"nickname": "某作者"},
                "aweme_type": 68,
                "images": [{"url_list": ["https://x/1.webp"]}],
            }
            for aid in aweme_ids
        ],
    }


class _FakeCrawler:
    """假的 DouyinCrawler：按调用顺序吐预先排好的响应。"""

    def __init__(self, kwargs, responses):
        self.responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def fetch_user_collects(self, _params):
        return self.responses.pop(0)

    async def fetch_user_collects_video(self, _params):
        return self.responses.pop(0)


@pytest.fixture
def fake_runtime(monkeypatch):
    """打桩运行时装配：不读真实 f2 配置、不碰真实目录。"""
    monkeypatch.setattr(
        f2_collects, "load_f2_runtime", lambda f2_dir=None: ({"cookie": "x"}, "假配置")
    )


def _patch_crawler(monkeypatch, responses: list):
    """把 f2 的 DouyinCrawler 换成假的（生产代码在函数内 import，取的是模块属性）。

    ⚠ 依赖守卫写在**用例内部**（经由本助手）：f2 只装在本机，CI 上没有。模块级
    ``pytest.importorskip`` 会把本文件里与 f2 无关的用例（命名模板、空勾选分支、
    import 顺序守卫）一起跳过，等于白丢覆盖——仓库既有约定见
    ``test_unit_f2_import.py`` 末尾「守卫必须写在每个用例内部」那段。
    """
    pytest.importorskip("f2", reason="需要 f2 本体来打桩收藏夹接口")
    import f2.apps.douyin.crawler as crawler_mod

    monkeypatch.setattr(
        crawler_mod, "DouyinCrawler", lambda kwargs: _FakeCrawler(kwargs, responses)
    )
    # 去掉分页间隔，测试别真睡
    monkeypatch.setattr(f2_collects, "COLLECTS_PAGE_SLEEP", 0)


# ── 收藏夹清单 ──


def test_list_collect_folders_parses_names_counts_and_totals(fake_runtime, monkeypatch):
    """扫描结果要给出夹名 / 夹 ID / 件数，并汇总「夹数」与「作品合计」。"""
    _patch_crawler(
        monkeypatch,
        [
            _folder_response(
                [("111", "秘书OL", 96), ("222", "股票", 1), ("333", "过膝袜短袜JK", 334)]
            )
        ],
    )

    data = f2.list_collect_folders(Path("x"))

    assert data["total_folders"] == 3
    assert data["total_works"] == 431
    assert data["cookie_source"] == "假配置"
    assert [f["name"] for f in data["folders"]] == ["秘书OL", "股票", "过膝袜短袜JK"]
    # 只给「夹名 / 夹 ID / 件数」：最近收藏时间没有消费方，且接口缺失时是
    # f2 过滤器产出的垃圾串（"Invalid timestamp"），故不返回该字段
    assert data["folders"][0] == {"id": "111", "name": "秘书OL", "total": 96}


def test_list_collect_folders_pages_until_has_more_is_false(fake_runtime, monkeypatch):
    """收藏夹多于一页时要继续翻（has_more=True 才会取第二页）。"""
    _patch_crawler(
        monkeypatch,
        [
            _folder_response([("111", "A", 1)], has_more=True, cursor=99),
            _folder_response([("222", "B", 2)], has_more=False),
        ],
    )

    data = f2.list_collect_folders(Path("x"))

    assert [f["id"] for f in data["folders"]] == ["111", "222"]
    assert data["total_works"] == 3


def test_list_collect_folders_without_cookie_raises(monkeypatch):
    """配置里没有 Cookie 时给可读错误（收藏列表只有本人可见）。"""
    monkeypatch.setattr(
        f2_collects,
        "load_f2_runtime",
        lambda f2_dir=None: (_ for _ in ()).throw(RuntimeError("f2 配置里没有 Cookie")),
    )
    with pytest.raises(RuntimeError, match="没有 Cookie"):
        f2.list_collect_folders(Path("x"))


# ── 按选中收藏夹下载 ──


class _FakeHandler:
    """假的 DouyinHandler：记录「读了哪个夹的哪些页」，把作品交给假下载器。"""

    def __init__(self, kwargs, folder_pages: dict, profile_nickname="还行吧"):
        self.kwargs = kwargs
        self.folder_pages = folder_pages
        self.downloader = _FakeDownloader()
        self._profile_nickname = profile_nickname

    async def fetch_user_profile(self, _sec_user_id):
        return type("P", (), {"nickname": self._profile_nickname})()

    def fetch_user_collects_videos(self, collects_id, _cursor=0, _counts=20, max_counts=None):
        pages = self.folder_pages.get(str(collects_id), [])
        return self._gen(pages, max_counts)

    async def _gen(self, pages, max_counts):
        taken = 0
        for page in pages:
            ids = list(page)
            if max_counts:
                ids = ids[: max(0, max_counts - taken)]
            if not ids:
                return
            taken += len(ids)
            yield type("Page", (), {"aweme_id": ids, "_to_list": lambda self, ids=ids: [{"aweme_id": i} for i in ids]})()


class _FakeDownloader:
    def __init__(self):
        self.pages: list[list] = []
        self.saved_last: list = []

    async def create_download_tasks(self, _kwargs, aweme_list, _user_path):
        self.pages.append(aweme_list)

    async def save_last_aweme_id(self, sec_user_id, aweme_id):  # pragma: no cover - 应被摘掉
        self.saved_last.append((sec_user_id, aweme_id))


def _patch_download_stack(monkeypatch, folder_pages, folder_list_response, handler_box):
    """把 f2 的 crawler / handler / utils 三处都换成假的（依赖守卫见 :func:`_patch_crawler`）。"""
    pytest.importorskip("f2", reason="需要 f2 本体来打桩 handler / downloader")
    import f2.apps.douyin.handler as handler_mod
    import f2.apps.douyin.utils as utils_mod

    _patch_crawler(monkeypatch, [folder_list_response])

    def _handler(kwargs):
        handler = _FakeHandler(kwargs, folder_pages)
        handler_box.append(handler)
        return handler

    monkeypatch.setattr(handler_mod, "DouyinHandler", _handler)
    monkeypatch.setattr(
        utils_mod, "SecUserIdFetcher", type("F", (), {"get_sec_user_id": staticmethod(_async("MS4w"))})
    )
    monkeypatch.setattr(
        utils_mod, "create_user_folder", lambda kwargs, nickname: Path("/fake") / str(nickname)
    )


def _async(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner


def test_download_only_touches_selected_folders_and_hands_works_to_f2(
    fake_runtime, monkeypatch
):
    """只枚举选中的夹；每页作品都交给 f2 自己的下载器，落点是「我的昵称」目录。"""
    folder_list = _folder_response(
        [("111", "秘书OL", 96), ("222", "股票", 1), ("333", "长黑高", 268)]
    )
    folder_pages = {"111": [["a1", "a2"]], "222": [["b1"]]}
    box: list = []
    _patch_download_stack(monkeypatch, folder_pages, folder_list, box)

    progress: list[dict] = []
    stats = f2.download_collect_folders(
        Path("x"), "https://www.douyin.com/user/MS4w", ["111"], on_progress=progress.append
    )

    handler = box[0]
    # 只读了选中的夹；没选中的「股票」一件都没碰
    assert [p["aweme_id"] for page in handler.downloader.pages for p in page] == ["a1", "a2"]
    assert stats["works"] == 2
    assert stats["total_works"] == 96  # 进度分母来自夹的 total_number
    assert stats["folders"] == [
        {"id": "111", "name": "秘书OL", "total": 96, "works": 2, "skipped_existing": 0}
    ]
    assert stats["root"].endswith("还行吧")
    assert stats["missing_folders"] == []
    # 进度回调：先给分母（0/96），每个夹完成后给一次
    assert progress[0]["works"] == 0 and progress[0]["total_works"] == 96
    assert progress[-1]["works"] == 2


def test_progress_callbacks_are_snapshots_not_live_structures(fake_runtime, monkeypatch):
    """进度回调必须给**快照**：消费者（任务执行器的 watcher）会跨线程读它并落库。

    别名到内部结构的话，早先那次回调拿到的 folders 会随后续 append 一起变长——
    这里用「每次回调看到几个夹」把它锁死：0（刚拿到分母）→ 1 → 2。
    """
    folder_list = _folder_response([("111", "A", 1), ("222", "B", 1)])
    box: list = []
    _patch_download_stack(monkeypatch, {"111": [["a1"]], "222": [["b1"]]}, folder_list, box)

    payloads: list[dict] = []
    f2.download_collect_folders(Path("x"), "u", ["111", "222"], on_progress=payloads.append)

    assert [len(p["folders"]) for p in payloads] == [0, 1, 2]
    # current 也是副本：最后一页之后仍能看到「当前夹」是最后一次设置的那个
    assert payloads[-1]["current"]["name"] == "B"


def test_download_reports_missing_folders(fake_runtime, monkeypatch):
    """扫描之后夹被删掉/改名的 ID：不报错，列进 missing_folders 让上层说明。"""
    folder_list = _folder_response([("111", "秘书OL", 96)])
    box: list = []
    _patch_download_stack(monkeypatch, {"111": [["a1"]]}, folder_list, box)

    stats = f2.download_collect_folders(Path("x"), "u", ["111", "999"])

    assert stats["missing_folders"] == ["999"]
    assert stats["works"] == 1


def test_download_writes_collect_folder_map(fake_runtime, monkeypatch, tmp_path):
    """按夹下载要写下「作品 ID → 收藏夹」归属清单（二级收藏夹的唯一依据）。

    文件平铺在昵称目录下、路径不带夹名，只有这份清单能回答「这件作品属于哪个夹」。
    被跳过（库里已有）的作品同样要记：它一样是这个夹里的作品，入库阶段要靠它
    把已在库的那件补进对应的二级收藏夹。
    """
    pytest.importorskip("f2", reason="需要 f2 本体来打桩 handler / downloader")
    import f2.apps.douyin.utils as utils_mod

    folder_list = _folder_response([("111", "秘书OL", 2), ("222", "股票", 1)])
    box: list = []
    _patch_download_stack(
        monkeypatch, {"111": [["a1", "a2"]], "222": [["b1"]]}, folder_list, box
    )
    # 落点换成真实临时目录：本用例要读回写下的清单
    monkeypatch.setattr(
        utils_mod, "create_user_folder", lambda kwargs, nickname: tmp_path / str(nickname)
    )

    stats = f2.download_collect_folders(
        Path("x"), "u", ["111", "222"], existing_aweme_ids={"a2"}
    )

    map_path = Path(stats["folder_map_file"])
    assert map_path.name == "_collect_folders.json"
    assert map_path.parent.name == "还行吧"
    payload = json.loads(map_path.read_text(encoding="utf-8"))
    by_name = {e["name"]: e["aweme_ids"] for e in payload["folders"].values()}
    assert by_name == {"秘书OL": ["a1", "a2"], "股票": ["b1"]}
    # 清单是 json、文件名不符合 f2 命名模板 → 不会被扫描/入库当成素材
    from scripts.f2_common import parse_media_filename

    assert parse_media_filename(map_path) is None


def test_collect_folder_map_merges_previous_runs(tmp_path):
    """多次运行取并集：max_counts / 中断 / 只勾部分夹都不该把已有归属抹掉。"""
    path = tmp_path / "_collect_folders.json"
    f2_collects._merge_folder_map(path, {"111": {"name": "秘书OL", "aweme_ids": ["a1"]}})
    f2_collects._merge_folder_map(
        path, {"111": {"name": "秘书OL", "aweme_ids": ["a1", "a2"]}, "222": {"name": "股票", "aweme_ids": ["b1"]}}
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["folders"]["111"]["aweme_ids"] == ["a1", "a2"]
    assert payload["folders"]["222"] == {"name": "股票", "aweme_ids": ["b1"]}


def test_collect_folder_map_rebuilds_on_corrupt_file(tmp_path):
    """清单损坏时按空清单重建（下载不能因为一份辅助清单失败）。"""
    path = tmp_path / "_collect_folders.json"
    path.write_text("{ 这不是 json", encoding="utf-8")

    payload = f2_collects._merge_folder_map(
        path, {"111": {"name": "秘书OL", "aweme_ids": ["a1"]}}
    )

    assert payload is not None
    assert json.loads(path.read_text(encoding="utf-8"))["folders"]["111"]["aweme_ids"] == ["a1"]


def test_collect_folder_map_write_failure_is_not_fatal(tmp_path):
    """写不进去时返回 None 而不是抛错——归属清单是辅助信息，不能让下载整批失败。"""
    blocker = tmp_path / "同名文件"
    blocker.write_text("占位", encoding="utf-8")  # 拿文件当目录 → mkdir 必然失败

    assert (
        f2_collects._merge_folder_map(
            blocker / "_collect_folders.json", {"111": {"name": "A", "aweme_ids": ["a1"]}}
        )
        is None
    )


def test_download_respects_per_folder_max_counts(fake_runtime, monkeypatch):
    """max_counts 是**每个夹**的上限：每个夹各取最近 N 件。"""
    folder_list = _folder_response([("111", "A", 10), ("222", "B", 10)])
    folder_pages = {"111": [["a1", "a2", "a3"]], "222": [["b1", "b2", "b3"]]}
    box: list = []
    _patch_download_stack(monkeypatch, folder_pages, folder_list, box)

    stats = f2.download_collect_folders(Path("x"), "u", ["111", "222"], max_counts=2)

    handler = box[0]
    assert [p["aweme_id"] for page in handler.downloader.pages for p in page] == [
        "a1",
        "a2",
        "b1",
        "b2",
    ]
    assert stats["works"] == 4


def test_download_stops_between_folders_when_should_stop(fake_runtime, monkeypatch):
    """暂停/取消：停止标记一置位就收手（已下载部分保留），并标记 stopped。"""
    folder_list = _folder_response([("111", "A", 1), ("222", "B", 1)])
    box: list = []
    _patch_download_stack(monkeypatch, {"111": [["a1"]], "222": [["b1"]]}, folder_list, box)

    state = {"stop": False}

    def _on_progress(stats: dict) -> None:
        # 第一次回调是「刚拿到分母」（还没有完成的夹），第二次起是某个夹下完
        if stats["folders"]:
            state["stop"] = True  # 第一个夹下完就停（模拟外部点了暂停）

    stats = f2.download_collect_folders(
        Path("x"), "u", ["111", "222"], should_stop=lambda: state["stop"], on_progress=_on_progress
    )

    assert stats["stopped"] is True
    assert [f["id"] for f in stats["folders"]] == ["111"]
    assert stats["works"] == 1
    # 第二个夹一件都没碰
    handler = box[0]
    assert [p["aweme_id"] for page in handler.downloader.pages for p in page] == ["a1"]


def test_download_skips_works_already_in_library(fake_runtime, monkeypatch):
    """F-1：库里已有的作品整件跳过（不下也不交给下载器），ID 带出来供合集补齐。"""
    folder_list = _folder_response([("111", "秘书OL", 3)])
    box: list = []
    _patch_download_stack(monkeypatch, {"111": [["a1", "a2", "a3"]]}, folder_list, box)

    stats = f2.download_collect_folders(
        Path("x"), "u", ["111"], existing_aweme_ids={"a1", "a3"}
    )

    handler = box[0]
    # 只把 a2 交给了下载器
    assert [p["aweme_id"] for page in handler.downloader.pages for p in page] == ["a2"]
    assert stats["skipped_existing"] == 2
    assert stats["skipped_existing_ids"] == ["a1", "a3"]
    # works 仍是「看到的作品数」：进度分母口径不变
    assert stats["works"] == 3
    # 按夹也记了跳过数
    assert stats["folders"][0]["skipped_existing"] == 2


def test_download_prelinks_files_from_other_mode_root(fake_runtime, monkeypatch, tmp_path):
    """F-2：同类目录（like/）里已有的文件硬链接到目标目录，f2 见到即跳过（不重复下载）。"""
    like_root = tmp_path / "like" / "我的账号"
    like_root.mkdir(parents=True)
    name = "不养羊_2026-09-14 10-31-14_下一站再见吧#jk_7670881947199742833_image_1.jpg"
    source = like_root / name
    source.write_bytes(b"bytes-of-this-work")
    target_dir = tmp_path / "collection" / "我的账号"
    target_dir.mkdir(parents=True)

    folder_list = _folder_response([("111", "A", 1)])
    box: list = []
    _patch_download_stack(monkeypatch, {"111": [["7670881947199742833"]]}, folder_list, box)
    # 让 user_path 指向我们准备好的目标目录
    import f2.apps.douyin.utils as utils_mod

    monkeypatch.setattr(utils_mod, "create_user_folder", lambda kwargs, nickname: target_dir)

    stats = f2.download_collect_folders(
        Path("x"), "u", ["111"], link_from_root=tmp_path / "like"
    )

    linked = target_dir / name
    assert linked.exists()
    assert os.stat(linked).st_ino == os.stat(source).st_ino  # 同一份数据，不是复制
    assert stats["prelinked"] == 1
    assert stats["link_index_size"] == 1


def test_download_flags_truncated_skip_id_list(fake_runtime, monkeypatch):
    """ID 列表被上限截断时要显式置标记（否则合集少补了却无人知道）。"""
    monkeypatch.setattr(f2_collects, "SKIPPED_ID_LIMIT", 1)
    folder_list = _folder_response([("111", "A", 3)])
    box: list = []
    _patch_download_stack(monkeypatch, {"111": [["a1", "a2", "a3"]]}, folder_list, box)

    stats = f2.download_collect_folders(
        Path("x"), "u", ["111"], existing_aweme_ids={"a1", "a2", "a3"}
    )

    assert stats["skipped_existing"] == 3
    assert stats["skipped_existing_ids"] == ["a1"]  # 只留前 1 条
    assert stats["skipped_ids_truncated"] is True


def test_download_without_selection_is_a_noop(fake_runtime, monkeypatch):
    """没勾选任何夹：直接返回空结果（不联网、不构造任何 f2 客户端）。"""
    called = []
    monkeypatch.setattr(
        f2_collects.asyncio, "run", lambda *a, **k: called.append(a) or {}
    )

    stats = f2.download_collect_folders(Path("x"), "u", [])

    assert stats["works"] == 0 and stats["folders"] == []
    assert not called


def test_download_does_not_write_f2_user_db(fake_runtime, monkeypatch):
    """不写 f2 的用户库：它的库名是相对路径，在后端进程里会落到 backend/ 抢锁。"""
    folder_list = _folder_response([("111", "A", 1)])
    box: list = []
    _patch_download_stack(monkeypatch, {"111": [["a1"]]}, folder_list, box)

    asyncio.run(
        f2_collects._download_folders_async(Path("x"), "u", ["111"], 0, lambda: False, None)
    )

    handler = box[0]
    assert handler.downloader.saved_last == []  # save_last_aweme_id 已被摘掉


def test_collect_naming_matches_shared_template():
    """命名模板与采集侧共用同一份常量（换个模板就会让判重/原作者全部失效）。"""
    assert f2_collects._collect_naming() == f2.COLLECT_NAMING_TEMPLATE


def test_f2_classes_are_imported_after_clone_is_on_path():
    """结构守卫：f2 的类必须**在 load_f2_runtime 之后**才 import。

    为什么值得一条用例：`load_f2_runtime` 才会把克隆版 f2 插到 sys.path 最前并清掉
    已导入的旧模块。顺序颠倒时拿到的是 site-packages 里那份旧版（签名失效），收藏夹
    接口直接 403——而且**只在进程内第一次执行时**显现（之前有别的调用先把克隆版放好
    就看不出来），是最难复现的一类错。这里用 AST 把顺序钉死。
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(f2_collects._download_folders_async)))
    load_line = next(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "load_f2_runtime"
    )
    f2_import_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("f2.")
    ]
    assert f2_import_lines, "这条守卫要盯的 f2 import 不见了，请同步更新用例"
    assert all(load_line < line for line in f2_import_lines), (
        "f2 的类必须在 load_f2_runtime 之后 import，否则可能用到旧版 f2（403）"
    )
