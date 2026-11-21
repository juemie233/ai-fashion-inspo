"""Rust sidecar 跨语言契约测试（`feat/rust-media-worker` 分支）。

验证 Python 调用方与 **真实** Rust 二进制（`media-worker` / `storage-audit`）
之间的 CLI/JSON 契约：子进程调用、stdin manifest、stdout 报告、临时目录、
ZIP 内容与文件系统产物。mock 单测覆盖不到「两边对 JSON 字段/退出码理解不一致」
这类问题，本文件专门锁这一层。

默认门禁不跑（标记 ``slow``）；本机已构建 sidecar 时手动验证：

    export MEDIA_WORKER_BIN='D:/tools/cargo-target/fashion-inspo/debug/media-worker.exe'
    cd backend && py -3.12 -m pytest tests/test_rust_sidecar_contract.py -m slow -q

未设置 ``MEDIA_WORKER_BIN`` 或二进制不存在时整文件 skip：CI/无 Rust 环境不应失败。
构建方式见 ``backend/rust/media-worker/README.md``（工具链与 target 目录都在 D 盘）。
"""

import hashlib
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from app.config import settings
from app.services import collection_export_service, storage_audit_service

pytestmark = pytest.mark.slow

_CONTRACT_DIR = "images/_rust_contract"


def _require_bin() -> Path:
    configured = os.environ.get("MEDIA_WORKER_BIN", "").strip()
    if not configured:
        pytest.skip("未设置 MEDIA_WORKER_BIN，跳过 Rust sidecar 跨语言契约测试")
    binary = Path(configured)
    if not binary.is_file():
        pytest.skip(f"MEDIA_WORKER_BIN 指向的文件不存在：{binary}")
    return binary


@pytest.fixture(scope="module")
def worker_bin() -> Path:
    return _require_bin()


@pytest.fixture(scope="module")
def audit_bin(worker_bin: Path) -> Path:
    for name in ("storage_audit.exe", "storage_audit", "storage-audit.exe", "storage-audit"):
        candidate = worker_bin.with_name(name)
        if candidate.is_file():
            return candidate
    pytest.skip("media-worker 同目录未找到 storage_audit 二进制")


@pytest.fixture()
def contract_files() -> list[Path]:
    """在（测试）storage_root 下造契约测试用素材文件，结束后清理。"""
    root = settings.storage_root / _CONTRACT_DIR
    root.mkdir(parents=True, exist_ok=True)
    files = [
        root / "photo_a.jpg",
        root / "photo_b.png",
        root / "clip_c.bin",
    ]
    files[0].write_bytes(b"rust-sidecar-contract-a")
    Image.new("RGB", (64, 32), (10, 20, 30)).save(files[1], "PNG")
    files[2].write_bytes(bytes(range(64)))
    try:
        yield files
    finally:
        for path in files:
            path.unlink(missing_ok=True)
        try:
            root.rmdir()
        except OSError:
            pass


def _run(
    bin_path: Path, args: list[str], *, stdin_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(bin_path), *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


# ── media-worker：hash / thumbnail ──


def test_hash_cli_matches_hashlib(worker_bin: Path, contract_files: list[Path]) -> None:
    payload = contract_files[2]  # 含 0..63 全字节，覆盖二进制内容
    result = _run(worker_bin, ["hash", str(payload)])

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["sha256"] == hashlib.sha256(payload.read_bytes()).hexdigest()
    assert body["bytes"] == payload.stat().st_size
    assert body["path"].replace("\\", "/").endswith("clip_c.bin")


def test_hash_cli_error_on_missing_file(worker_bin: Path, tmp_path: Path) -> None:
    result = _run(worker_bin, ["hash", str(tmp_path / "no-such.jpg")])

    assert result.returncode != 0
    assert "media-worker 失败" in result.stderr
    assert result.stdout.strip() == ""


def test_thumbnail_cli_bounds_and_never_upscales(worker_bin: Path, tmp_path: Path) -> None:
    big = tmp_path / "big.png"
    Image.new("RGB", (300, 900), (200, 100, 50)).save(big, "PNG")
    out_big = tmp_path / "big.jpg"

    result = _run(
        worker_bin,
        ["thumbnail", str(big), str(out_big), "--max-width", "400", "--max-height", "600"],
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert (body["width"], body["height"]) == (200, 600)  # 1:3 等比缩到高度上限
    assert (body["source_width"], body["source_height"]) == (300, 900)
    with Image.open(out_big) as image:
        assert image.format == "JPEG"
        assert image.size == (200, 600)

    small = tmp_path / "small.png"
    Image.new("RGB", (100, 50), (1, 2, 3)).save(small, "PNG")
    out_small = tmp_path / "small.jpg"
    result = _run(worker_bin, ["thumbnail", str(small), str(out_small)])
    assert result.returncode == 0, result.stderr
    small_body = json.loads(result.stdout)
    assert (small_body["width"], small_body["height"]) == (100, 50)  # 不放大


def test_thumbnail_cli_rejects_non_image(worker_bin: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"this is not an image")
    result = _run(worker_bin, ["thumbnail", str(bad), str(tmp_path / "o.jpg")])

    assert result.returncode != 0
    assert "media-worker 失败" in result.stderr


# ── media-worker：archive / batch ──


def test_archive_cli_builds_streaming_zip(
    worker_bin: Path, contract_files: list[Path], tmp_path: Path
) -> None:
    out = tmp_path / "nested" / "out.zip"  # 父目录不存在，CLI 应自动创建
    manifest = {
        "entries": [
            {"path": str(contract_files[0]), "archive": "0001_photo_a.jpg"},
            {"archive": "manifest.csv", "content": "a,b\n1,2\n"},
            {"path": str(tmp_path / "missing.jpg"), "archive": "0002_missing.jpg"},
            {"path": str(contract_files[1]), "archive": "sub/0003_photo_b.png"},
        ]
    }
    result = _run(
        worker_bin,
        ["archive", str(out)],
        stdin_text=json.dumps(manifest, ensure_ascii=False),
    )

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["entries"] == 3  # 真实文件 2 + 内嵌内容 1
    assert body["skipped"] == 1  # 缺失文件
    assert body["bytes"] == out.stat().st_size
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
        assert "manifest.csv" in names
        assert "0001_photo_a.jpg" in names
        # 路径分隔符被清洗成下划线，避免 ZIP 内出现目录穿越式条目
        assert "sub_0003_photo_b.png" in names
        assert archive.read("0001_photo_a.jpg") == b"rust-sidecar-contract-a"
        assert archive.read("manifest.csv") == b"a,b\n1,2\n"


def test_archive_cli_rejects_empty_entries(worker_bin: Path, tmp_path: Path) -> None:
    result = _run(
        worker_bin,
        ["archive", str(tmp_path / "empty.zip")],
        stdin_text=json.dumps({"entries": []}),
    )

    assert result.returncode != 0
    assert "没有 entries" in result.stderr


def test_batch_cli_rounds_trip_multiple_ops(
    worker_bin: Path, contract_files: list[Path], tmp_path: Path
) -> None:
    thumb_out = tmp_path / "batch_thumb.jpg"
    requests = [
        {"id": "h1", "op": "hash", "input": str(contract_files[0])},
        {
            "id": "t1",
            "op": "thumbnail",
            "input": str(contract_files[1]),
            "output": str(thumb_out),
            "max_width": 32,
            "max_height": 32,
        },
        {"id": "u1", "op": "unknown-op"},
    ]
    stdin_text = "\n".join(json.dumps(item) for item in requests) + "\n\n"
    result = _run(worker_bin, ["batch"], stdin_text=stdin_text)

    assert result.returncode == 0, result.stderr
    lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 3  # 空行被忽略，失败请求也占一行
    by_id = {line.get("id"): line for line in lines}
    assert by_id["h1"]["ok"] is True
    assert by_id["h1"]["sha256"] == hashlib.sha256(contract_files[0].read_bytes()).hexdigest()
    assert by_id["t1"]["ok"] is True
    assert thumb_out.is_file()
    assert (by_id["t1"]["width"], by_id["t1"]["height"]) == (32, 16)
    assert by_id["u1"]["ok"] is False
    assert "未知 op" in by_id["u1"]["error"]


# ── Python 服务 ↔ 真实 sidecar ──


async def test_storage_audit_service_with_real_binary(
    worker_bin: Path, audit_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实二进制的报告要能被 `storage_audit_service` 解析成 Python 字段。"""
    root = settings.storage_root
    images = root / "images"
    images.mkdir(parents=True, exist_ok=True)
    keep = images / "_contract_keep.bin"
    orphan = images / "_contract_orphan.bin"
    keep.write_bytes(b"keep")
    orphan.write_bytes(b"orphan")
    monkeypatch.setattr(settings, "media_worker_bin", str(worker_bin))
    try:
        report = await storage_audit_service.run_storage_audit(
            ["images/_contract_keep.bin", "images/_contract_missing.bin"]
        )
    finally:
        keep.unlink(missing_ok=True)
        orphan.unlink(missing_ok=True)

    assert report is not None
    assert "images/_contract_missing.bin" in report["missing"]
    assert any(item["path"] == "images/_contract_orphan.bin" for item in report["orphans"])
    assert report["expected_files"] == 2
    assert report["scanned_media_dirs"] == ["images", "thumbnails", "videos"]


async def test_export_zip_via_real_binary_contract(
    worker_bin: Path, contract_files: list[Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实 Rust ZIP 产物必须能被 Python zipfile 读回，且计数与文件大小一致。"""
    monkeypatch.setattr(settings, "media_worker_bin", str(worker_bin))
    monkeypatch.setattr(settings, "media_worker_archive_threshold", 1)
    rows = [
        {
            "序号": 1,
            "素材ID": "contract-1",
            "文件名": "photo_a.jpg",
            "作者": "契约作者",
            "相对路径": f"{_CONTRACT_DIR}/photo_a.jpg",
        },
        {
            "序号": 2,
            "素材ID": "contract-2",
            "文件名": "missing.jpg",
            "作者": "",
            "相对路径": f"{_CONTRACT_DIR}/not-exist.jpg",  # 缺失素材应计入 skipped
        },
    ]
    output = tmp_path / "export.zip"

    result = await collection_export_service._zip_bytes_via_media_worker(
        rows, b"id,author\n", b"# title\n", output
    )

    assert result is not None
    item_count, skipped, size_bytes = result
    assert (item_count, skipped) == (1, 1)
    assert size_bytes == output.stat().st_size
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert "manifest.csv" in names and "README.md" in names
        assert any(name.startswith("0001_") for name in names)
        assert archive.read("manifest.csv") == b"id,author\n"
