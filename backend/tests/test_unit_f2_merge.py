"""跨模式重复产物合并（scripts/f2_common.merge_personal_duplicates）单元测试。

背景：f2 没有下载台账，「当前模式目录里有没有同名文件」就是它判断「下过没有」的
唯一依据（目录 = `<path>/douyin/<mode>/<昵称>/`）。同一作品既被点赞又被收藏时，
like 与 collection 各下一次、各存一份；入库侧按平台 ID 只收一条，多出来的那份
纯占磁盘。这里锁的是合并函数的行为边界：只合并**跨模式 + 内容完全相同**的副本，
把它换成硬链接（两个目录都留着文件，f2 两侧的「存在即跳过」继续有效）。

只碰 tmp_path；不联网、不跑 f2、不碰真实下载目录。
"""

import os
from pathlib import Path

import pytest

from f2_patch import patch_f2
from scripts import import_f2_downloads as f2

"""测试用命名（与 f2 的点赞/收藏模板同形：`{原作者}_{时间}_{正文}_{作品ID}_{类型}_{序号}`）。"""
IMAGE_1 = "不养羊_2026-09-14 10-31-14_下一站再见吧#jk_7670881947199742833_image_1.jpg"
IMAGE_2 = "不养羊_2026-09-14 10-31-14_下一站再见吧#jk_7670881947199742833_image_2.jpg"
VIDEO = "不养羊_2026-09-15 09-00-00_另一条_video.mp4"


def _mode_root(tmp_path: Path, mode: str) -> Path:
    """构造一个模式的产物目录：`{根}/{我的昵称}/`（f2 把「我的列表」全下在这里）。"""
    author_dir = tmp_path / mode / "我的账号"
    author_dir.mkdir(parents=True, exist_ok=True)
    return author_dir


def _write(directory: Path, name: str, content: bytes) -> Path:
    path = directory / name
    path.write_bytes(content)
    return path


def _same_data(left: Path, right: Path) -> bool:
    a, b = left.stat(), right.stat()
    return a.st_dev == b.st_dev and a.st_ino == b.st_ino


def test_merge_links_identical_cross_mode_files(tmp_path):
    """跨模式同名同内容：合并成硬链接，两个路径都在、内容不变、只占一份。"""
    like_dir = _mode_root(tmp_path, "like")
    coll_dir = _mode_root(tmp_path, "collection")
    payload = b"jpeg-bytes" * 100
    like_file = _write(like_dir, IMAGE_1, payload)
    coll_file = _write(coll_dir, IMAGE_1, payload)
    assert not _same_data(like_file, coll_file)

    stats = f2.merge_personal_duplicates([tmp_path / "like", tmp_path / "collection"])

    assert stats["scanned"] == 2
    assert stats["candidates"] == 1
    assert stats["linked"] == 1
    assert stats["saved_bytes"] == len(payload)
    assert (stats["conflict"], stats["failed"]) == (0, 0)
    assert like_file.exists() and coll_file.exists()  # 两侧路径都必须留着
    assert like_file.read_bytes() == payload  # 内容不变
    assert _same_data(like_file, coll_file)  # 同一份数据
    assert like_file.stat().st_nlink >= 2
    # 临时链接名不能残留（否则会被当成下载产物统计进去）
    assert not list(tmp_path.rglob(f"*{f2.MERGE_TMP_SUFFIX}"))


def test_merge_keeps_both_when_size_differs(tmp_path):
    """同名但大小不同：保留两份（实测 post 与 like 对同一作品会下到不同清晰度）。"""
    like_dir = _mode_root(tmp_path, "like")
    coll_dir = _mode_root(tmp_path, "collection")
    like_file = _write(like_dir, IMAGE_1, b"x" * 300)
    coll_file = _write(coll_dir, IMAGE_1, b"x" * 100)

    stats = f2.merge_personal_duplicates([tmp_path / "like", tmp_path / "collection"])

    assert (stats["linked"], stats["conflict"], stats["saved_bytes"]) == (0, 1, 0)
    assert not _same_data(like_file, coll_file)
    assert like_file.stat().st_size == 300 and coll_file.stat().st_size == 100
    assert stats["samples"] and "大小不同" in stats["samples"][0]


def test_merge_keeps_both_when_content_differs(tmp_path):
    """大小相同但内容不同：同样保留两份（必须比内容，不能只看大小）。"""
    like_dir = _mode_root(tmp_path, "like")
    coll_dir = _mode_root(tmp_path, "collection")
    like_file = _write(like_dir, IMAGE_1, b"a" * 128)
    coll_file = _write(coll_dir, IMAGE_1, b"b" * 128)

    stats = f2.merge_personal_duplicates([tmp_path / "like", tmp_path / "collection"])

    assert (stats["linked"], stats["conflict"]) == (0, 1)
    assert not _same_data(like_file, coll_file)
    assert stats["samples"] and "内容不同" in stats["samples"][0]


def test_merge_requires_same_work_and_segment(tmp_path):
    """只有「同一作品的同一分段」才配对：作品不同、分段不同都不动。"""
    like_dir = _mode_root(tmp_path, "like")
    coll_dir = _mode_root(tmp_path, "collection")
    payload = b"same-bytes"
    # 同一作品的不同分段
    like_1 = _write(like_dir, IMAGE_1, payload)
    coll_2 = _write(coll_dir, IMAGE_2, payload)
    # 不同作品、内容相同
    like_v = _write(like_dir, VIDEO, payload)
    coll_v = _write(coll_dir, VIDEO, payload)

    stats = f2.merge_personal_duplicates([tmp_path / "like", tmp_path / "collection"])

    assert stats["candidates"] == 1  # 只有 VIDEO 配成对
    assert stats["linked"] == 1
    assert not _same_data(like_1, coll_2)
    assert _same_data(like_v, coll_v)


def test_merge_ignores_duplicates_inside_one_root(tmp_path):
    """同一个模式目录里的重复不归本函数管（只在跨模式之间合并）。"""
    like_dir = _mode_root(tmp_path, "like")
    _mode_root(tmp_path, "collection")
    first = _write(like_dir, IMAGE_1, b"only-like")
    second = _write(like_dir, VIDEO, b"only-like")  # 同内容、不同作品：不会被配对
    like_dup = _write(like_dir, IMAGE_2, b"dup-bytes")
    # 同一根目录里的「同分段两份」：扩展名不同（重新下载换了后缀时会出现）
    like_dup2 = _write(like_dir, IMAGE_2.replace(".jpg", ".jpeg"), b"dup-bytes")

    stats = f2.merge_personal_duplicates([tmp_path / "like", tmp_path / "collection"])

    assert stats["scanned"] == 4
    assert (stats["candidates"], stats["linked"]) == (0, 0)
    assert not _same_data(first, second)
    # 同根目录里同分段同内容的两个文件（扩展名不同）也不动
    assert not _same_data(like_dup, like_dup2)


def test_merge_is_idempotent(tmp_path):
    """重复运行幂等：已合并过的不再计入，第二次不再声称省了空间。"""
    like_dir = _mode_root(tmp_path, "like")
    coll_dir = _mode_root(tmp_path, "collection")
    payload = b"payload" * 10
    like_file = _write(like_dir, IMAGE_1, payload)
    coll_file = _write(coll_dir, IMAGE_1, payload)
    roots = [tmp_path / "like", tmp_path / "collection"]

    first = f2.merge_personal_duplicates(roots)
    second = f2.merge_personal_duplicates(roots)

    assert (first["linked"], first["saved_bytes"]) == (1, len(payload))
    assert (second["linked"], second["saved_bytes"]) == (0, 0)
    assert second["candidates"] == 1  # 仍能认出是同一分段，只是已经是同一份数据
    assert _same_data(like_file, coll_file)


def test_merge_dedupes_repeated_roots_and_missing_roots(tmp_path):
    """同一个目录传两次不重复配对；目录不存在（还没下载过）不报错。"""
    like_dir = _mode_root(tmp_path, "like")
    _write(like_dir, IMAGE_1, b"only-like")

    stats = f2.merge_personal_duplicates(
        [tmp_path / "like", tmp_path / "like", tmp_path / "collection"]
    )

    assert stats["roots"] == [str(tmp_path / "like"), str(tmp_path / "collection")]
    assert stats["scanned"] == 1
    assert (stats["candidates"], stats["linked"]) == (0, 0)


def test_merge_default_roots_are_like_and_collection(monkeypatch, tmp_path):
    """缺省只处理 like 与 collection 两个「我的列表」根（post 绝不参与：字节常不同）。"""
    like_root, coll_root = tmp_path / "like", tmp_path / "collection"
    like_dir = _mode_root(tmp_path, "like")
    coll_dir = _mode_root(tmp_path, "collection")
    post_dir = _mode_root(tmp_path, "post")
    payload = b"cross-mode-bytes"
    _write(like_dir, IMAGE_1, payload)
    _write(coll_dir, IMAGE_1, payload)
    post_file = _write(post_dir, IMAGE_1, payload)

    patch_f2(monkeypatch, "DEFAULT_F2_LIKE_ROOT", like_root)
    patch_f2(monkeypatch, "DEFAULT_F2_COLLECT_ROOT", coll_root)
    stats = f2.merge_personal_duplicates()

    assert stats["roots"] == [str(like_root), str(coll_root)]
    assert stats["linked"] == 1
    # post 侧不在处理范围内：它既没被合并、也没被算进候选
    assert not _same_data(post_file, like_dir / IMAGE_1)
    assert os.stat(post_file).st_nlink == 1


def test_guard_rejects_real_roots():
    """越界保护有效：用真实默认根目录调用合并必须被拦下（测试绝不许动真实下载目录）。

    为什么锁这条：个人列表模式下载后会自动调用合并，而「我的喜欢」类用例常常只 patch
    一侧根目录——漏 patch 的那一侧就是用户真实的 Download/douyin，测试会去扫它、并在
    名字/大小/内容都一样时**硬链接替换真实文件**（已实测发生过）。保护由 conftest 的
    ``guard_f2_merge_off_real_roots`` 提供，这里同时验证它确实挂在合并函数上。
    """
    with pytest.raises(AssertionError, match="越界到真实目录"):
        f2.merge_personal_duplicates()
