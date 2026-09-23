"""存储/产物根目录可配置的单元测试（迁移到别的盘用）。

背景：C 盘曾被数据占满，方案是把 `backend/storage` 与 f2 产物搬到别的盘。
两个坑各有用例锁死：
  1. pydantic 的字段默认值在**类定义时**求值——只设 `STORAGE_ROOT` 不会让
     images_dir/trash_dir/… 跟着走（tests/conftest.py 早就逐项覆盖绕过了它）
  2. f2 的产物根原先写死在 `DEFAULT_F2_DIR`，必须能由 `F2_DOWNLOAD_ROOT` 指走

⚠ 本文件要先清掉 conftest 设的存储环境变量：`tests/conftest.py` 为了隔离，
在会话开始就把 `STORAGE_ROOT` 与各 `<NAME>_DIR` 指到临时目录——不清掉的话，
用例断言的不是「默认/新配置行为」，而是 conftest 的隔离值。
"""

from pathlib import Path

import pytest

from scripts import f2_common

"""所有跟着 storage_root 走的子目录（与 Settings 里的字段一一对应）。"""
STORAGE_SUBDIRS = (
    'images',
    'thumbnails',
    'videos',
    'trash',
    'keyframes',
    'person_photos',
    'person_thumbnails',
    'lancedb',
)


@pytest.fixture
def clean_storage_env(monkeypatch):
    """清掉 conftest 设的存储环境变量，让用例从「干净默认」出发。"""
    monkeypatch.delenv('STORAGE_ROOT', raising=False)
    for name in STORAGE_SUBDIRS:
        monkeypatch.delenv(f'{name.upper()}_DIR', raising=False)
    return monkeypatch


def _fresh_settings(**kwargs):
    """构造一个**不读 .env** 的 Settings：用例结果不该被本机 .env 影响。"""
    from app.config import Settings

    return Settings(_env_file=None, **kwargs)


def test_storage_root_env_moves_every_subdir(clean_storage_env, tmp_path):
    """只设 STORAGE_ROOT，8 个子目录必须全部跟着走（否则迁移会「一半在新盘」）。"""
    target = tmp_path / 'new-storage'
    clean_storage_env.setenv('STORAGE_ROOT', str(target))

    settings = _fresh_settings()

    assert settings.storage_root == target
    for name in STORAGE_SUBDIRS:
        assert getattr(settings, f'{name}_dir') == target / name


def test_explicit_subdir_is_not_overridden(clean_storage_env, tmp_path):
    """显式指定的子目录（测试/特殊部署）不能被 validator 覆盖回去。"""
    custom = tmp_path / 'explicit-images'
    settings = _fresh_settings(storage_root=tmp_path / 'root', images_dir=custom)

    assert settings.images_dir == custom
    # 其余没显式指定的仍跟随 storage_root
    assert settings.videos_dir == tmp_path / 'root' / 'videos'


def test_default_storage_root_is_the_repo_one(clean_storage_env):
    """不设任何环境变量时，storage_root 仍是仓库里的 backend/storage（历史行为）。"""
    settings = _fresh_settings()

    assert settings.storage_root == Path(__file__).resolve().parent.parent / 'storage'
    assert settings.images_dir == settings.storage_root / 'images'


def test_resolve_download_root_defaults_to_f2_workdir(monkeypatch):
    """没配 F2_DOWNLOAD_ROOT 时，产物根仍是 `<f2 工作目录>/Download`（历史行为）。"""
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, 'f2_download_root', None, raising=False)

    assert f2_common.resolve_download_root() == f2_common.DEFAULT_F2_DIR / 'Download'


def test_resolve_download_root_honours_settings(monkeypatch, tmp_path):
    """配了 F2_DOWNLOAD_ROOT（.env）时按它走——f2 安装目录不动，只有产物搬走。"""
    from app.config import settings as app_settings

    target = tmp_path / 'f2-downloads'
    monkeypatch.setattr(app_settings, 'f2_download_root', target, raising=False)

    assert f2_common.resolve_download_root() == target
    assert f2_common.resolve_download_root(str(target / 'x')) == target / 'x'


def test_three_mode_roots_hang_off_download_root():
    """post/like/collection 三个产物根都必须挂在同一个下载根下（否则扫描扫不到）。"""
    root = f2_common.DEFAULT_F2_DOWNLOAD_ROOT

    assert f2_common.DEFAULT_F2_ROOT == root / 'douyin' / 'post'
    assert f2_common.DEFAULT_F2_LIKE_ROOT == root / 'douyin' / 'like'
    assert f2_common.DEFAULT_F2_COLLECT_ROOT == root / 'douyin' / 'collection'


def test_db_file_path_follows_database_url_not_storage_parent(clean_storage_env, tmp_path):
    """库文件路径必须由 DATABASE_URL 推导，不能按 storage_root.parent 猜。

    回归（任务 #370）：`STORAGE_ROOT=G:/fashion-inspo-storage` 时
    `storage_root.parent / "fashion_inspo.db"` 算出的是 `G:\\fashion_inspo.db`
    ——一个空库。后果是静默走偏：f2 判重读空库（全部作品都「库里没有」）、
    入库写空库（`no such table: inspirations`，4970 件作品 0 入库）。
    """
    db = tmp_path / 'elsewhere' / 'fashion_inspo.db'
    settings = _fresh_settings(
        storage_root=tmp_path / 'g-drive-storage',
        database_url=f'sqlite+aiosqlite:///{db.as_posix()}',
    )

    assert settings.db_file_path == db
    # 与 storage 的上一级**无关**（这正是原实现的谬误）
    assert settings.db_file_path != settings.storage_root.parent / 'fashion_inspo.db'


def test_library_db_path_uses_the_same_single_source(monkeypatch, tmp_path):
    """f2 判重/入库用的库路径必须与 app 的库文件口径一致（单一口径）。"""
    from scripts import f2_plan

    db = tmp_path / 'same-source' / 'fashion_inspo.db'
    settings = _fresh_settings(
        storage_root=tmp_path / 'storage',
        database_url=f'sqlite+aiosqlite:///{db.as_posix()}',
    )
    monkeypatch.setattr(f2_plan, 'settings', settings, raising=False)

    assert f2_plan.library_db_path() == db == settings.db_file_path

