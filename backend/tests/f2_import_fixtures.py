"""test_f2_import_* 共用 fixture / 构造助手 / 常量（从 test_f2_import_task.py 抽出，C-6）。

fixture 与 helper 都由三个主题测试文件**显式导入**——注意取源码时必须带上装饰器
（`ast` 的 node.lineno 指向 def 行，装饰器要另外从 decorator_list 取起始行），
否则 `@pytest.fixture` 会丢、测试报 `fixture ... not found`。
"""

import json
import sqlite3
from pathlib import Path
import pytest
from PIL import Image
from app.database import async_session
from app.services import task_runner
from f2_patch import patch_f2
from scripts import import_f2_downloads as f2

SEC = "MS4wLjABAAAACyG6qmWLGt5BbCvwkAfMpEf3nhGwlQqSG1MjwDIGokuUHJnIkwJzxDPu-1RRrfvk"



def _jpeg(path: Path, color: str = "red") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (48, 48), color).save(path, "JPEG")
    return path



def _make_f2_author_db(f2_dir, rows: list[tuple[str, str, int]]) -> None:
    """造一个 f2 用户库（douyin_users.db），rows 为 (sec_user_id, nickname, aweme_count)。"""

    f2_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, aweme_count INTEGER)"
    )
    conn.executemany("INSERT INTO user_info_web VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()



def _make_author_db(f2_dir: Path) -> None:
    """在临时 f2 工作目录建最小 user_info_web 表（load_f2_authors 的唯一数据源）。"""

    conn = sqlite3.connect(f2_dir / f2.F2_AUTHOR_DB)
    conn.execute(
        "CREATE TABLE user_info_web (sec_user_id TEXT, nickname TEXT, aweme_count INTEGER)"
    )
    conn.execute("INSERT INTO user_info_web VALUES ('sec1', '里香1√', 171)")
    conn.commit()
    conn.close()



async def _count_f2_tasks(db) -> int:
    from sqlalchemy import func, select

    from app.models.task import TaskQueue

    return (
        await db.execute(
            select(func.count()).select_from(TaskQueue).where(TaskQueue.type == "f2_import")
        )
    ).scalar() or 0



async def _import_once(fetch: bool = False) -> int:
    """跑一次 f2 导入（临时目录 + 临时库），返回任务 id。"""
    async with async_session() as db:
        task = await task_runner.create_f2_import_task(db, fetch=fetch)
        task_id = task.id
        await task_runner.execute_f2_import(db, task)
    return task_id



def _stub_like_fetch(monkeypatch, calls: dict | None = None):
    """打桩 f2 的点赞抓取：只记录入参并返回成功，不真的跑 f2。"""
    patch_f2(monkeypatch, "f2_available", lambda: True)

    def _fake(f2_dir, like_user, download_root=None, **kwargs):
        if calls is not None:
            calls["like_user"] = like_user
            calls["download_root"] = str(download_root)
            calls["max_counts"] = kwargs.get("max_counts")
        return {
            "total": 1,
            "ok": 1,
            "failed": 0,
            "results": [{"nickname": "我的喜欢", "rc": 0, "cmd": "f2 dy -M like"}],
        }

    patch_f2(monkeypatch, "run_fetch_likes", _fake)



def _stub_collect_fetch(monkeypatch, calls: dict | None = None):
    """打桩 f2 的收藏抓取：只记录入参并返回成功，不真的跑 f2。"""
    patch_f2(monkeypatch, "f2_available", lambda: True)

    def _fake(f2_dir, collect_user, download_root=None, **kwargs):
        if calls is not None:
            calls["fetch"] = "collect"
            calls["like_user"] = collect_user
            calls["download_root"] = str(download_root)
            calls["max_counts"] = kwargs.get("max_counts")
        return {
            "total": 1,
            "ok": 1,
            "failed": 0,
            "results": [{"nickname": "我的收藏", "rc": 0, "cmd": "f2 dy -M collection"}],
        }

    patch_f2(monkeypatch, "run_fetch_collects", _fake)



def _write_collect_folder_map(root, folders: dict) -> None:
    """写收藏夹归属清单（格式契约见 scripts/f2_collects.py 的 ``_merge_folder_map``）。

    Args:
        root: 收藏产物根目录（清单写在 ``{root}/_collect_folders.json``）。
        folders: ``{收藏夹 ID: (收藏夹名, [作品 ID, ...])}``。
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "_collect_folders.json").write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-09-23 16:00:00",
                "folders": {
                    str(fid): {"name": name, "aweme_ids": list(ids)}
                    for fid, (name, ids) in folders.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )



def _stub_collect_folder_download(monkeypatch, calls: dict):
    """打桩「按选中收藏夹下载」：往收藏产物根写一张真图 + 归属清单，模拟 f2 落盘。"""
    patch_f2(monkeypatch, "f2_available", lambda: True)
    # 平铺链路必须**不被走到**（走到就说明 collect_ids 没生效）
    patch_f2(
        monkeypatch,
        "run_fetch_collects",
        lambda *_a, **_k: pytest.fail("选中收藏夹时不该走平铺收藏命令"),
    )

    def _fake(f2_dir, user, collect_ids, max_counts=0, should_stop=None, on_progress=None,
              existing_aweme_ids=None, link_from_root=None):
        calls["collect_ids"] = list(collect_ids)
        calls["max_counts"] = max_counts
        calls["user"] = user
        calls["existing_aweme_ids"] = set(existing_aweme_ids or set())
        calls["link_from_root"] = str(link_from_root or "")
        root = f2.DEFAULT_F2_COLLECT_ROOT / "我的账号"
        _jpeg(root / "不养羊_2026-09-14 10-31-14_下一站再见吧#jk_7670881947199742833_image_1.jpg")
        # 真实下载会把「作品 ID → 收藏夹」写进清单；入库阶段按它限定范围与建二级
        _write_collect_folder_map(
            root, {collect_ids[0]: ("秘书OL", ["7670881947199742833"])}
        )
        stats = {
            "cookie_source": "假配置",
            "root": str(root),
            "folders": [{"id": collect_ids[0], "name": "秘书OL", "total": 1, "works": 1,
                         "skipped_existing": 0}],
            "works": 1,
            "total_works": 1,
            "current": {},
            "stopped": False,
            "missing_folders": [],
            "skipped_existing": 0,
            "skipped_existing_ids": [],
            "skipped_ids_truncated": False,
            "prelinked": 0,
            "link_index_size": 0,
        }
        if on_progress:
            on_progress(stats)
        return stats

    patch_f2(monkeypatch, "download_collect_folders", _fake)



async def _run_like_import(**kwargs) -> int:
    """跑一次「我的喜欢」导入（临时目录 + 临时库），返回任务 id。"""
    async with async_session() as db:
        task = await task_runner.create_f2_import_task(
            db, fetch=True, fetch_mode="like", **kwargs
        )
        task_id = task.id
        await task_runner.execute_f2_import(db, task)
    return task_id



@pytest.fixture(autouse=True)
def isolate_personal_roots(tmp_path, monkeypatch):
    """把「我的喜欢 / 我的收藏」两个产物根目录都指到临时目录（本模块 autouse）。

    为什么必须两侧一起 patch：个人列表模式下载结束后会自动跑一次跨模式重复合并
    （见 ``f2.merge_personal_duplicates``），它按默认值读这两个根目录。本模块的
    「我的喜欢」用例只 patch like 侧（``f2_like_tree``），不一起 patch collect 侧
    就会让合并去扫用户**真实的** ``Download/douyin/collection``（实测：全量套件里
    11 次调用带着真实收藏目录）。这里统一指到临时目录，越界由 conftest 的
    ``guard_f2_merge_off_real_roots`` 断言兜底。
    """
    like_root = tmp_path / "like"
    collect_root = tmp_path / "collection"
    like_root.mkdir()
    collect_root.mkdir()
    patch_f2(monkeypatch, "DEFAULT_F2_LIKE_ROOT", like_root)
    patch_f2(monkeypatch, "DEFAULT_F2_COLLECT_ROOT", collect_root)



@pytest.fixture
def f2_tree(tmp_path, monkeypatch):
    """把 f2 的工作目录/下载目录指向临时目录，并放两个作品的文件。"""
    f2_dir = tmp_path / "f2proj"
    root = f2_dir / "Download" / "douyin" / "post"
    _jpeg(root / "里香1√" / "2025-01-01 10-00-00_#jk_标题_image_1.jpg")
    _jpeg(root / "里香1√" / "2025-01-01 10-00-00_#jk_标题_image_2.jpg", "blue")
    _jpeg(root / "某博主" / "2025-02-02 11-00-00_#通勤_标题_video.mp4".replace(".mp4", ".jpg"), "green")
    patch_f2(monkeypatch, "DEFAULT_F2_DIR", f2_dir)
    patch_f2(monkeypatch, "DEFAULT_F2_ROOT", root)
    return f2_dir, root



@pytest.fixture
def auto_env(f2_tree, monkeypatch):
    """可用的自动获取环境：f2 视为已安装 + 作者库 1 个作者。"""
    f2_dir, _root = f2_tree
    _make_author_db(f2_dir)
    patch_f2(monkeypatch, "f2_available", lambda: True)
    return f2_dir



@pytest.fixture
def auto_settings(monkeypatch):
    """隔离自动获取与「我的喜欢」相关配置项：用例结束后还原（含 API 直接改 settings 的情况）。"""
    from app.config import settings

    original = (
        settings.f2_import_auto_enabled,
        settings.f2_import_interval_hours,
        settings.f2_import_auto_skip_live,
        settings.f2_import_auto_mode,
        settings.f2_fetch_since_days,
        settings.f2_like_user,
    )
    yield settings
    (
        settings.f2_import_auto_enabled,
        settings.f2_import_interval_hours,
        settings.f2_import_auto_skip_live,
        settings.f2_import_auto_mode,
        settings.f2_fetch_since_days,
        settings.f2_like_user,
    ) = original



@pytest.fixture
def f2_like_tree(tmp_path, monkeypatch):
    """把「我的喜欢」产物根指向临时目录：一个作品两张图，文件名带原作者前缀。"""
    like_root = tmp_path / "like"
    author_dir = like_root / "我的账号"  # f2 把喜欢的作品统统下在「我的昵称」目录下
    _jpeg(author_dir / "不养羊_2026-09-14 10-31-14_下一站再见吧#地铁jk_#jk_image_1.jpg")
    _jpeg(author_dir / "不养羊_2026-09-14 10-31-14_下一站再见吧#地铁jk_#jk_image_2.jpg", "blue")
    patch_f2(monkeypatch, "DEFAULT_F2_LIKE_ROOT", like_root)
    return like_root

