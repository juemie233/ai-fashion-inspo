"""合集导出 Rust ZIP 分支单测（feat/rust-media-worker 分支新增代码）。

覆盖：
- ``_zip_archive_name``：源文件后缀优先、无源文件回退 row 文件名后缀、
  都无 → ``.bin``；作者 sanitize / 截断 40 / 空作者回退文件名、素材 ID；
  序号 4 位补零；
- ``_zip_bytes_via_media_worker``：未配置 / 配置指向不存在文件 → None；
  成功时 stdin manifest（manifest.csv / README.md 内嵌 content + 媒体 entries）、
  ZIP 写盘与 (成功数, 跳过数, 文件大小) 返回值；返回码非 0 / stdout JSON
  非法 / 输出文件未生成 / 超时 → RuntimeError；
- ``create_export`` 的 Rust 分支：行数达到阈值（含 ``==`` 边界）走 Rust 且
  不再走 Python ``_zip_bytes``、响应计数与真实磁盘大小；行数低于阈值即使
  配置了二进制也不调用 Rust；sidecar 返回 None 时回退 Python。

全部 monkeypatch ``asyncio.create_subprocess_exec``，不依赖真实 Rust 二进制。
Rust 成功/失败回退的 HTTP 端到端变体已由 ``tests/test_collection_export.py``
的 ``test_export_zip_falls_back_when_rust_sidecar_fails`` 覆盖，这里不重复。
"""

import asyncio
import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from app.config import settings
from app.services import collection_export_service


def _row(
    index: int,
    *,
    author: str = "小明",
    filename: str = "photo.JPG",
    insp_id: str = "insp-1",
    rel_path: str = "",
) -> dict:
    """构造导出用的行数据（键与 ``_build_rows`` 产物一致）。"""
    return {
        "序号": index,
        "素材ID": insp_id,
        "文件名": filename,
        "媒体类型": "image",
        "来源": "manual_upload",
        "作者": author,
        "收藏时间": "",
        "评分": "",
        "标签": "",
        "原链接": "",
        "相对路径": rel_path,
    }


# ── _zip_archive_name：Python / Rust 共用的命名规则 ──


def test_zip_archive_name_prefers_source_suffix(tmp_path):
    source = tmp_path / "pic.webp"
    source.write_bytes(b"img")
    assert (
        collection_export_service._zip_archive_name(_row(1, filename="photo.JPG"), source)
        == "0001_小明.webp"
    )


def test_zip_archive_name_falls_back_to_row_filename_suffix():
    assert (
        collection_export_service._zip_archive_name(_row(2, filename="clip.MP4"), None)
        == "0002_小明.mp4"
    )


def test_zip_archive_name_without_any_extension_uses_bin():
    assert (
        collection_export_service._zip_archive_name(_row(3, filename="无后缀文件"), None)
        == "0003_小明.bin"
    )


def test_zip_archive_name_sanitizes_author():
    """路径分隔符/非法字符替换为下划线，首尾的 ``._`` 去掉。"""
    name = collection_export_service._zip_archive_name(_row(4, author="a/b:c*d?"), None)
    assert name == "0004_a_b_c_d.jpg"


def test_zip_archive_name_truncates_author_to_40_chars():
    name = collection_export_service._zip_archive_name(_row(5, author="作" * 50), None)
    stem = name[len("0005_") : -len(".jpg")]
    assert len(stem) == 40


def test_zip_archive_name_uses_file_stem_when_author_empty():
    assert (
        collection_export_service._zip_archive_name(
            _row(6, author="", filename="IMG_001.png"), None
        )
        == "0006_IMG_001.png"
    )


def test_zip_archive_name_uses_inspiration_id_when_author_and_stem_empty():
    row = _row(7, author="", filename="", insp_id="abc-123")
    assert collection_export_service._zip_archive_name(row, None) == "0007_abc-123.bin"


def test_zip_archive_name_pads_index_to_four_digits():
    assert collection_export_service._zip_archive_name(_row(42), None).startswith("0042_")


# ── _zip_bytes_via_media_worker ──


class _FakeWorkerProcess:
    """假 media-worker：communicate 时按回调写产物，记录 stdin 与 kill/wait。"""

    def __init__(self, *, on_communicate=None, stdout=b"", stderr=b"", returncode=0):
        self.on_communicate = on_communicate
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.stdin_payload = None
        self.killed = False
        self.waited = False

    async def communicate(self, input=None):
        self.stdin_payload = input
        if self.on_communicate is not None:
            self.on_communicate()
        return self.stdout, self.stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True
        return self.returncode


def _install_fake_worker_exec(monkeypatch, process):
    record: dict = {}

    async def fake_exec(*argv, **kwargs):
        record["argv"] = argv
        record["kwargs"] = kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return record


@pytest.fixture
def worker_env(tmp_path, monkeypatch):
    """storage_root 指到 tmp_path，并造一个存在的 media-worker 可执行文件。"""
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    binary = tmp_path / "media-worker.exe"
    binary.write_bytes(b"stub")
    monkeypatch.setattr(settings, "storage_root", storage_root)
    monkeypatch.setattr(settings, "media_worker_bin", str(binary))
    monkeypatch.setattr(settings, "media_worker_archive_timeout_seconds", 30)
    return {"root": storage_root, "binary": binary, "tmp": tmp_path}


def _make_media_file(worker_env, rel_path: str = "images/2026-11/a.jpg") -> Path:
    path = worker_env["root"] / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"image-bytes")
    return path


async def test_zip_via_media_worker_returns_none_without_binary(worker_env, monkeypatch):
    monkeypatch.setattr(settings, "media_worker_bin", "")
    called: list[tuple] = []

    async def fake_exec(*argv, **kwargs):
        called.append(argv)
        raise AssertionError("未配置 media_worker_bin 时不应创建子进程")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    output = worker_env["tmp"] / "out.zip"
    result = await collection_export_service._zip_bytes_via_media_worker(
        [_row(1)], b"manifest", b"# README", output
    )
    assert result is None
    assert called == []


async def test_zip_via_media_worker_returns_none_when_binary_missing(
    worker_env, monkeypatch
):
    monkeypatch.setattr(
        settings, "media_worker_bin", str(worker_env["tmp"] / "no-such-worker.exe")
    )
    output = worker_env["tmp"] / "out.zip"
    assert (
        await collection_export_service._zip_bytes_via_media_worker(
            [_row(1)], b"manifest", b"# README", output
        )
        is None
    )
    assert not output.exists()


async def test_zip_via_media_worker_success_writes_stdin_manifest(worker_env, monkeypatch):
    media = _make_media_file(worker_env)
    rows = [
        _row(1, author="小明", filename=media.name, rel_path="images/2026-11/a.jpg"),
        _row(
            2,
            author="",
            filename="",
            insp_id="insp-2",
            rel_path="images/2026-11/missing.jpg",
        ),
    ]
    output = worker_env["tmp"] / "out.zip"
    payload = b"PK\x03\x04fake-rust-zip-bytes"

    def write_output():
        output.write_bytes(payload)

    process = _FakeWorkerProcess(
        on_communicate=write_output, stdout=b'{"skipped": 1}'
    )
    record = _install_fake_worker_exec(monkeypatch, process)

    result = await collection_export_service._zip_bytes_via_media_worker(
        rows, b"manifest-bytes", b"# README", output
    )

    assert result == (1, 1, len(payload))
    argv = record["argv"]
    assert argv[0] == str(worker_env["binary"])
    assert argv[1:] == ("archive", str(output))
    assert record["kwargs"]["stdin"] is asyncio.subprocess.PIPE
    assert record["kwargs"]["stdout"] is asyncio.subprocess.PIPE
    assert record["kwargs"]["stderr"] is asyncio.subprocess.PIPE

    manifest = json.loads(process.stdin_payload.decode("utf-8"))
    entries = manifest["entries"]
    assert entries[0] == {"archive": "manifest.csv", "content": "manifest-bytes"}
    assert entries[1] == {"archive": "README.md", "content": "# README"}
    assert entries[2]["archive"] == "0001_小明.jpg"
    assert entries[2]["path"] == str(media.resolve())
    # 缺失文件仍要出现在清单里（path=None），由 Rust 计入 skipped
    assert entries[3]["path"] is None
    assert entries[3]["archive"] == "0002_insp-2.bin"
    assert len(entries) == 4


async def test_zip_via_media_worker_nonzero_exit_raises_with_stderr(worker_env, monkeypatch):
    process = _FakeWorkerProcess(
        returncode=1, stderr=b"archive failed: disk full"
    )
    _install_fake_worker_exec(monkeypatch, process)
    output = worker_env["tmp"] / "out.zip"

    with pytest.raises(RuntimeError) as excinfo:
        await collection_export_service._zip_bytes_via_media_worker(
            [_row(1)], b"manifest", b"# README", output
        )
    message = str(excinfo.value)
    assert "exit 1" in message
    assert "disk full" in message


async def test_zip_via_media_worker_broken_stdout_raises(worker_env, monkeypatch):
    process = _FakeWorkerProcess(stdout=b"not-a-json")
    _install_fake_worker_exec(monkeypatch, process)
    output = worker_env["tmp"] / "out.zip"

    with pytest.raises(RuntimeError, match="无法解析"):
        await collection_export_service._zip_bytes_via_media_worker(
            [_row(1)], b"manifest", b"# README", output
        )


async def test_zip_via_media_worker_missing_output_file_raises(worker_env, monkeypatch):
    """进程返回成功但没写出 ZIP 文件（例如参数被吞）→ RuntimeError。"""
    process = _FakeWorkerProcess(stdout=b'{"skipped": 0}')
    _install_fake_worker_exec(monkeypatch, process)
    output = worker_env["tmp"] / "out.zip"

    with pytest.raises(RuntimeError, match="未生成文件"):
        await collection_export_service._zip_bytes_via_media_worker(
            [_row(1)], b"manifest", b"# README", output
        )


async def test_zip_via_media_worker_timeout_kills_process(worker_env, monkeypatch):
    process = _FakeWorkerProcess()
    _install_fake_worker_exec(monkeypatch, process)
    recorded: dict = {}

    async def fake_wait_for(coro, timeout):
        recorded["timeout"] = timeout
        coro.close()  # 避免 coroutine never awaited 警告
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
    output = worker_env["tmp"] / "out.zip"

    with pytest.raises(RuntimeError, match="超时"):
        await collection_export_service._zip_bytes_via_media_worker(
            [_row(1)], b"manifest", b"# README", output
        )
    assert recorded["timeout"] == settings.media_worker_archive_timeout_seconds
    assert process.killed is True
    assert process.waited is True


class _HangingWorkerProcess:
    """communicate 永不返回的假进程（验证真实 asyncio.wait_for 超时路径）。"""

    returncode = None

    def __init__(self):
        self.killed = False
        self.waited = False

    async def communicate(self, input=None):
        await asyncio.sleep(3600)

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True
        return self.returncode


async def test_zip_via_media_worker_real_wait_for_timeout(worker_env, monkeypatch):
    """真实 asyncio.wait_for 超时：kill + wait 后抛 RuntimeError。"""
    monkeypatch.setattr(settings, "media_worker_archive_timeout_seconds", 0.01)
    process = _HangingWorkerProcess()

    async def fake_exec(*argv, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    output = worker_env["tmp"] / "out.zip"

    # 外层 5s 保护：实现若读错超时字段（等 300s）则测试失败而不是挂死
    with pytest.raises(RuntimeError, match="超时"):
        await asyncio.wait_for(
            collection_export_service._zip_bytes_via_media_worker(
                [_row(1)], b"manifest", b"# README", output
            ),
            timeout=5,
        )
    assert process.killed is True
    assert process.waited is True


# ── create_export 的 Rust 分支（HTTP 端到端，走既有 client/upload fixture） ──


def _upload(client, upload) -> str:
    resp = upload()
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_collection(client, name: str) -> int:
    resp = client.post("/api/collections", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _add_members(client, collection_id: int, inspiration_ids: list[str]) -> None:
    resp = client.post(
        f"/api/collections/{collection_id}/inspirations",
        json={"inspiration_ids": inspiration_ids},
    )
    assert resp.status_code == 200, resp.text


def _export_ok(client, collection_id: int, fmt: str) -> dict:
    resp = client.post(f"/api/collections/{collection_id}/exports", json={"format": fmt})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _seed_two_item_collection(client, upload) -> int:
    ids = [_upload(client, upload), _upload(client, upload)]
    collection_id = _create_collection(client, "Rust ZIP 分支")
    _add_members(client, collection_id, ids)
    return collection_id


def _patch_zip_bytes_spy(monkeypatch):
    """包一层 Python ``_zip_bytes``，记录是否被调用（回退路径断言用）。"""
    original = collection_export_service._zip_bytes
    calls = {"python": 0}

    def spy(rows, manifest_csv, readme_md):
        calls["python"] += 1
        return original(rows, manifest_csv, readme_md)

    monkeypatch.setattr(collection_export_service, "_zip_bytes", spy)
    return calls


async def test_create_export_uses_rust_zip_at_threshold(client, upload, monkeypatch):
    """行数 == 阈值（>= 边界）时走 Rust，响应计数与 size_bytes 取真实磁盘大小。"""
    collection_id = _seed_two_item_collection(client, upload)
    fake_zip = b"PK\x03\x04rust-streamed-zip"

    async def fake_worker(rows, manifest_csv, readme_md, output):
        assert len(rows) == 2
        assert manifest_csv  # 含 CSV 清单
        assert readme_md
        output.write_bytes(fake_zip)
        return (2, 0, len(fake_zip))

    monkeypatch.setattr(collection_export_service, "_zip_bytes_via_media_worker", fake_worker)
    monkeypatch.setattr(settings, "media_worker_bin", "fake-media-worker.exe")
    monkeypatch.setattr(settings, "media_worker_archive_threshold", 2)  # 恰好等于行数
    python_calls = _patch_zip_bytes_spy(monkeypatch)

    data = _export_ok(client, collection_id, "zip")

    assert python_calls["python"] == 0
    assert data["item_count"] == 2
    assert data["skipped_count"] == 0
    assert data["size_bytes"] == len(fake_zip)
    download = client.get(data["download_url"])
    assert download.status_code == 200
    assert download.content == fake_zip


async def test_create_export_skips_rust_below_threshold(client, upload, monkeypatch):
    """行数 < 阈值：即使配置了二进制也不调用 Rust，走 Python ZIP。"""
    collection_id = _seed_two_item_collection(client, upload)
    rust_calls: list[tuple] = []

    async def fake_worker(*args, **kwargs):
        rust_calls.append(args)
        return None

    monkeypatch.setattr(collection_export_service, "_zip_bytes_via_media_worker", fake_worker)
    monkeypatch.setattr(settings, "media_worker_bin", "fake-media-worker.exe")
    monkeypatch.setattr(settings, "media_worker_archive_threshold", 3)

    data = _export_ok(client, collection_id, "zip")

    assert rust_calls == []
    assert data["item_count"] == 2
    assert data["skipped_count"] == 0
    download = client.get(data["download_url"])
    assert download.status_code == 200
    with zipfile.ZipFile(BytesIO(download.content)) as archive:
        names = archive.namelist()
    assert names[:2] == ["manifest.csv", "README.md"]
    assert len(names) == 4


async def test_create_export_falls_back_when_worker_unavailable(client, upload, monkeypatch):
    """阈值满足但 sidecar 不可用（返回 None）→ 回退 Python，导出仍可用。"""
    collection_id = _seed_two_item_collection(client, upload)

    async def fake_worker(rows, manifest_csv, readme_md, output):
        return None

    monkeypatch.setattr(collection_export_service, "_zip_bytes_via_media_worker", fake_worker)
    monkeypatch.setattr(settings, "media_worker_bin", "")
    monkeypatch.setattr(settings, "media_worker_archive_threshold", 1)
    python_calls = _patch_zip_bytes_spy(monkeypatch)

    data = _export_ok(client, collection_id, "zip")

    assert python_calls["python"] == 1
    assert data["item_count"] == 2
    assert data["skipped_count"] == 0
    download = client.get(data["download_url"])
    assert download.status_code == 200
    with zipfile.ZipFile(BytesIO(download.content)) as archive:
        assert len(archive.namelist()) == 4
