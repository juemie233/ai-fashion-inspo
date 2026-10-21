"""备份抽样哈希校验的测试。

核心要证明的是它补上了「文件数与字节数都对、但内容已坏」这个盲区 ——
这也是现网备份校验里唯一不花几十 GB 读取还能抓到磁盘损坏的手段。
"""

import json

from scripts.verify_backup_sample import (
    DEFAULT_SAMPLE,
    collect_files,
    main,
    pick_sample,
    verify,
)


def _write(root, rel: str, data: bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_detects_same_size_different_content(tmp_path):
    """同大小、不同内容必须被抓出来（只比文件数/字节数的校验会漏）。"""
    src, dest = tmp_path / "src", tmp_path / "dest"
    _write(src, "a/1.jpg", b"AAAA")
    _write(dest, "a/1.jpg", b"BBBB")  # 同样 4 字节

    result = verify(src, dest, sample=10)

    assert result["corrupt"] == ["a/1.jpg"]
    assert result["ok"] is False


def test_size_changed_is_warning_not_failure(tmp_path):
    """备份后源端又被写入（大小变了）不算损坏，否则日常备份会天天空报。"""
    src, dest = tmp_path / "src", tmp_path / "dest"
    _write(src, "a/1.jpg", b"new-content")   # 源端更长
    _write(dest, "a/1.jpg", b"old")          # 备份是旧快照

    result = verify(src, dest, sample=10)

    assert result["changed"] == ["a/1.jpg"]
    assert result["corrupt"] == []
    assert result["ok"] is True


def test_detects_missing_file(tmp_path):
    """备份端缺文件 → missing，并判不通过。"""
    src, dest = tmp_path / "src", tmp_path / "dest"
    _write(src, "a/1.jpg", b"x")
    _write(src, "a/2.jpg", b"y")
    _write(dest, "a/1.jpg", b"x")

    result = verify(src, dest, sample=10)

    assert "a/2.jpg" in result["missing"]
    assert result["ok"] is False


def test_all_good_passes(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    for i in range(5):
        _write(src, f"a/{i}.jpg", f"data-{i}".encode())
        _write(dest, f"a/{i}.jpg", f"data-{i}".encode())

    result = verify(src, dest, sample=10)

    assert result["checked"] == 5
    assert result["ok"] is True


def test_sample_is_deterministic_and_sorted():
    """同 seed 抽同一组（便于事后复查），且返回升序。"""
    first = pick_sample(1000, 20, seed=7)
    second = pick_sample(1000, 20, seed=7)
    assert first == second
    assert first == sorted(first)
    assert len(first) == 20
    assert pick_sample(1000, 20, seed=8) != first  # 换 seed 应换样本


def test_sample_edge_cases():
    assert pick_sample(0, 10, 0) == []
    assert pick_sample(5, 0, 0) == []  # 0 = 关闭
    assert pick_sample(5, 99, 0) == [0, 1, 2, 3, 4]  # 抽样数超过总数 → 全量


def test_collect_files_skips_directories_and_sorts(tmp_path):
    root = tmp_path / "s"
    _write(root, "b/2.jpg", b"x")
    _write(root, "a/1.jpg", b"x")
    (root / "empty").mkdir()

    rels = [p.as_posix() for p in collect_files(root)]

    assert rels == ["a/1.jpg", "b/2.jpg"]


def test_cli_returns_2_on_corruption_and_writes_json(tmp_path, capsys):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _write(src, "a/1.jpg", b"good")
    _write(dest, "a/1.jpg", b"bad!")
    out = tmp_path / "report.json"

    code = main(["--src", str(src), "--dest", str(dest), "--sample", "5", "--json", str(out)])

    assert code == 2
    assert "内容不一致" in capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8"))["corrupt"] == ["a/1.jpg"]


def test_cli_skips_when_source_missing(tmp_path, capsys):
    """源端不存在（如同盘备份场景）不应判失败：跳过即可。"""
    code = main(["--src", str(tmp_path / "nope"), "--dest", str(tmp_path / "dest")])
    assert code == 0
    assert "跳过" in capsys.readouterr().out


def test_default_sample_is_bounded():
    """默认样本量要有界：不能让日常备份变成全量哈希。"""
    assert 0 < DEFAULT_SAMPLE <= 500
