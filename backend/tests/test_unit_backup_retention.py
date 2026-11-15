"""备份保留策略：成功备份只留最新 N 份，失败备份独立保留排障证据。"""

from pathlib import Path

import pytest

from scripts.backup_retention import DEFAULT_KEEP_COUNT, main, prune_backups


def _make_backup(root: Path, stamp: str, marker: str = "SUCCESS") -> Path:
    path = root / stamp
    path.mkdir(parents=True)
    if marker:
        (path / marker).write_text("ok", encoding="utf-8")
    return path


def test_default_keep_count_is_three():
    assert DEFAULT_KEEP_COUNT == 3


def test_keeps_latest_three_successful_and_deletes_older(tmp_path):
    for day in range(1, 7):
        _make_backup(tmp_path, f"2026-08-{day:02d}_030000")

    result = prune_backups(tmp_path)

    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == [
        "2026-08-04_030000",
        "2026-08-05_030000",
        "2026-08-06_030000",
    ]
    assert result["deleted"] == [
        "2026-08-03_030000",
        "2026-08-02_030000",
        "2026-08-01_030000",
    ]


def test_custom_keep_count(tmp_path):
    for day in range(1, 6):
        _make_backup(tmp_path, f"2026-08-{day:02d}_030000")

    prune_backups(tmp_path, keep_count=1)

    assert [p.name for p in tmp_path.iterdir()] == ["2026-08-05_030000"]


def test_failed_backups_are_kept_separately(tmp_path):
    for day in range(1, 7):
        _make_backup(tmp_path, f"2026-08-{day:02d}_030000", marker="")
        (tmp_path / f"2026-08-{day:02d}_030000" / "FAILED").write_text(
            "failed", encoding="utf-8"
        )
    _make_backup(tmp_path, "2026-08-10_030000")

    result = prune_backups(tmp_path, keep_count=1, failed_keep=2)

    names = sorted(p.name for p in tmp_path.iterdir())
    assert "2026-08-10_030000" in names
    assert "2026-08-06_030000" in names
    assert "2026-08-05_030000" in names
    assert "2026-08-04_030000" not in names
    assert len(result["success_kept"]) == 1


def test_incomplete_directories_are_removed(tmp_path):
    _make_backup(tmp_path, "2026-08-01_030000", marker="")
    (tmp_path / "manual_note").mkdir()

    result = prune_backups(tmp_path)

    assert not (tmp_path / "2026-08-01_030000").exists()
    assert (tmp_path / "manual_note").exists()
    assert "2026-08-01_030000 (中断)" in result["deleted"]


def test_dry_run_does_not_delete(tmp_path):
    _make_backup(tmp_path, "2026-08-01_030000")
    _make_backup(tmp_path, "2026-08-02_030000")

    result = prune_backups(tmp_path, keep_count=1, dry_run=True)

    assert (tmp_path / "2026-08-01_030000").exists()
    assert result["deleted"] == ["2026-08-01_030000"]


@pytest.mark.parametrize("keep_count", [0, -1])
def test_invalid_keep_count_rejected(keep_count):
    with pytest.raises(ValueError):
        prune_backups(Path("."), keep_count=keep_count)


def test_cli_entrypoint_prunes_and_prints(tmp_path, capsys):
    _make_backup(tmp_path, "2026-08-01_030000")
    _make_backup(tmp_path, "2026-08-02_030000")

    assert main([str(tmp_path), "1"]) == 0

    assert [p.name for p in tmp_path.iterdir()] == ["2026-08-02_030000"]
    out = capsys.readouterr().out
    assert "删除 1 份" in out
    assert "保留 1 份" in out
