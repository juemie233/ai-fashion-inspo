"""视频关键帧与 WebSocket 连接的测试。

覆盖两块「不报错但会静默降级」的基础设施：

1. `video_service`：ffmpeg 抽帧参数、幂等、均匀采样、清理——抽帧不对会让
   AI 只看到视频第一帧（穿搭视频通常最后才换装），分析结论直接错。
2. `ws.ConnectionManager`：广播时单条连接失败不能拖垮其余连接，否则一个卡住的
   前端会静默掐断所有人的实时推送。
"""

import asyncio
from pathlib import Path

import pytest

from app.routers.ws import ConnectionManager
from app.services import video_service


# ── video_service：纯函数部分 ──


def test_filter_args_use_scene_detection_when_threshold_set(monkeypatch):
    """阈值 > 0 时按场景突变抽帧：表达式里的逗号必须转义（会被 ffmpeg 当分隔符）。"""
    monkeypatch.setattr(video_service.settings, "keyframe_scene_threshold", 0.3, raising=False)

    args = video_service._build_filter_args()

    assert args == ["-vf", "select=gt(scene\\,0.3)", "-vsync", "vfr"]


def test_filter_args_fall_back_to_fixed_interval(monkeypatch):
    """阈值 0 → 固定间隔抽帧；间隔给 0 时兜底 0.1（否则 fps=1/0 会除零）。"""
    monkeypatch.setattr(video_service.settings, "keyframe_scene_threshold", 0, raising=False)
    monkeypatch.setattr(video_service.settings, "keyframe_interval_seconds", 5, raising=False)
    assert video_service._build_filter_args() == ["-vf", "fps=1/5"]

    monkeypatch.setattr(video_service.settings, "keyframe_interval_seconds", 0, raising=False)
    assert video_service._build_filter_args() == ["-vf", "fps=1/0.1"]


def _frames(n: int) -> list[Path]:
    return [Path(f"/tmp/f{i:03d}.jpg") for i in range(n)]


def test_sample_frames_evenly_covers_whole_video():
    """均匀采样要覆盖全片：首尾帧必须保留（结尾常是换装后的完整造型）。"""
    frames = _frames(10)
    picked = video_service.sample_frames(frames, 4)

    assert picked[0] == frames[0]
    assert picked[-1] == frames[-1]
    assert len(picked) == 4


@pytest.mark.parametrize("max_n,expected", [(0, 10), (-1, 10), (10, 10), (20, 10), (1, 1)])
def test_sample_frames_boundaries(max_n, expected):
    """max_n <=0 不限、超量原样返回、max_n=1 只取第一帧。"""
    frames = _frames(10)
    picked = video_service.sample_frames(frames, max_n)
    assert len(picked) == expected
    if expected == 1:
        assert picked == [frames[0]]


def test_sample_frames_dedupes_rounding_collisions():
    """帧数少而 max_n 大时 round 会产生重复位置，结果不能有重复帧。"""
    frames = _frames(5)
    picked = video_service.sample_frames(frames, 4)
    assert len(picked) == len(set(picked))


def test_keyframes_dir_and_listing():
    """目录约定 storage/keyframes/{id}；只认 frame_*.jpg（其它文件不算帧）。"""
    directory = video_service.keyframes_dir("insp-1")

    assert directory == video_service.settings.keyframes_dir / "insp-1"
    assert not directory.exists()  # 纯路径计算，负号：不建目录（由 ffmpeg 阶段建）
    assert video_service._list_frames(directory) == []  # 目录不存在返回空而不是抛错

    directory.mkdir(parents=True, exist_ok=True)

    (directory / "frame_002.jpg").write_bytes(b"x")
    (directory / "frame_001.jpg").write_bytes(b"x")
    (directory / "note.txt").write_text("x", encoding="utf-8")

    # 按文件名排序 = 时间序
    assert [p.name for p in video_service._list_frames(directory)] == [
        "frame_001.jpg",
        "frame_002.jpg",
    ]


async def test_get_keyframes_returns_empty_for_unknown():
    assert await video_service.get_keyframes("不存在") == []


async def test_cleanup_keyframes_removes_directory():
    directory = video_service.keyframes_dir("insp-2")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "frame_001.jpg").write_bytes(b"x")

    await video_service.cleanup_keyframes("insp-2")

    assert not directory.exists()
    # 再删一次不报错（幂等）
    await video_service.cleanup_keyframes("insp-2")


async def test_cleanup_keyframes_batch_handles_many():
    for i in range(3):
        directory = video_service.keyframes_dir(f"b{i}")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "frame_001.jpg").write_bytes(b"x")

    await video_service.cleanup_keyframes_batch(["b0", "b1", "b2", "不存在"])

    assert not any(video_service.keyframes_dir(f"b{i}").exists() for i in range(3))


# ── ws.ConnectionManager：广播容错 ──


class _FakeWS:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent: list[dict | str] = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_json(self, message: dict):
        if self.fail:
            raise RuntimeError("连接已断")
        self.sent.append(message)

    async def send_text(self, text: str):
        self.sent.append(text)


async def test_connect_and_disconnect():
    manager = ConnectionManager()
    ws = _FakeWS()

    await manager.connect(ws)
    assert ws.accepted is True
    assert manager.active_connections == [ws]

    manager.disconnect(ws)
    assert manager.active_connections == []
    manager.disconnect(ws)  # 重复移除不抛错（并发断开）

    assert manager.disconnect.__doc__ is not None


async def test_broadcast_drops_broken_connection_but_reaches_others():
    """一条连接发送失败不能拖垮其余连接，也不能留在列表里反复失败。"""
    manager = ConnectionManager()
    good, bad = _FakeWS(), _FakeWS(fail=True)
    await manager.connect(good)
    await manager.connect(bad)

    await manager.broadcast({"type": "progress", "value": 1})

    assert good.sent == [{"type": "progress", "value": 1}]
    assert manager.active_connections == [good]  # 坏连接已被移除


async def test_send_to_broken_connection_disconnects():
    manager = ConnectionManager()
    bad = _FakeWS(fail=True)
    await manager.connect(bad)

    await manager.send_to(bad, {"type": "x"})

    assert manager.active_connections == []


async def test_send_to_ok_connection():
    manager = ConnectionManager()
    ws = _FakeWS()
    await manager.connect(ws)

    await manager.send_to(ws, {"type": "y"})

    assert ws.sent == [{"type": "y"}]


async def test_websocket_endpoint_replies_pong():
    """端点收到 ping 回 pong；断开时把自己从连接表里摘掉。"""
    from fastapi import WebSocketDisconnect

    from app.routers import ws as ws_module

    class _Scripted:
        def __init__(self):
            self.accepted = False
            self.texts: list[str] = []
            self._received = 0

        async def accept(self):
            self.accepted = True

        async def receive_text(self) -> str:
            self._received += 1
            if self._received == 1:
                return "ping"
            raise WebSocketDisconnect()

        async def send_text(self, text: str):
            self.texts.append(text)

        async def send_json(self, message):  # pragma: no cover - 不会走到
            pass

    socket = _Scripted()
    await ws_module.websocket_endpoint(socket)  # type: ignore[arg-type]

    assert socket.accepted is True
    assert socket.texts == ["pong"]
    assert socket not in ws_module.manager.active_connections


async def test_websocket_endpoint_disconnects_on_unexpected_error():
    from app.routers import ws as ws_module

    class _Broken:
        def __init__(self):
            self.accepted = False

        async def accept(self):
            self.accepted = True

        async def receive_text(self):
            raise asyncio.TimeoutError("意外错误")

        async def send_text(self, text: str):  # pragma: no cover
            pass

    socket = _Broken()
    await ws_module.websocket_endpoint(socket)  # type: ignore[arg-type]

    assert socket not in ws_module.manager.active_connections


# ── video_service：抽帧主流程（ffmpeg 用假进程替身）──


class _FakeFFmpegProc:
    """ffmpeg 子进程替身：wait 时「产出」帧文件（模拟真实写盘）。"""

    def __init__(self, out_dir: Path, frames: int = 2, *, hang: bool = False, kill_ok: bool = True):
        self._out_dir = out_dir
        self._frames = frames
        self._hang = hang
        self._kill_ok = kill_ok
        self.killed = False

    async def wait(self):
        if self._hang:
            await asyncio.sleep(10)
        for i in range(1, self._frames + 1):
            (self._out_dir / f"frame_{i:03d}.jpg").write_bytes(b"jpg")
        return 0

    def kill(self):
        self.killed = True
        if not self._kill_ok:
            raise ProcessLookupError()


def _patch_ffmpeg(monkeypatch, proc_factory):
    calls: list[list[str]] = []

    async def _fake_exec(*cmd, **kwargs):
        calls.append(list(cmd))
        return proc_factory()

    monkeypatch.setattr(video_service.asyncio, "create_subprocess_exec", _fake_exec)
    return calls


def _video_file(tmp_path, monkeypatch, rel: str = "videos/x.mp4") -> str:
    path = video_service.settings.storage_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake")
    return rel


async def _extract(monkeypatch, tmp_path, insp_id: str, rel="videos/x.mp4", **proc_kwargs):
    """跑一次抽帧：先建视频文件，再用假 ffmpeg 进程（**每个用例传独立 insp_id**）。"""
    rel = _video_file(tmp_path, monkeypatch, rel)
    out_dir = video_service.keyframes_dir(insp_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    def factory():
        return _FakeFFmpegProc(out_dir, **proc_kwargs)

    calls = _patch_ffmpeg(monkeypatch, factory)
    frames = await video_service.extract_keyframes_for_video(insp_id, rel)
    return frames, calls, out_dir


async def test_extract_builds_command_and_returns_frames(monkeypatch, tmp_path):
    frames, calls, _out = await _extract(monkeypatch, tmp_path, "kf-cmd", frames=3)

    assert [f.name for f in frames] == ["frame_001.jpg", "frame_002.jpg", "frame_003.jpg"]
    cmd = calls[0]
    assert cmd[0] == "ffmpeg"
    assert "-frames:v" in cmd
    assert str(video_service.settings.storage_root / "videos/x.mp4") in cmd
    assert cmd[-1].endswith("frame_%03d.jpg")


async def test_extract_is_idempotent(monkeypatch, tmp_path):
    """已有帧时不再调 ffmpeg（上传/向量/人脸链路会反复调用，重复抽帧很贵）。"""
    frames, calls, out_dir = await _extract(monkeypatch, tmp_path, "kf-idem", frames=2)
    assert calls

    calls.clear()
    again = await video_service.extract_keyframes_for_video("kf-idem", "videos/x.mp4")

    assert len(again) == 2
    assert calls == []  # 没有再次启动 ffmpeg


async def test_extract_skips_missing_video(monkeypatch, tmp_path):
    calls = _patch_ffmpeg(monkeypatch, lambda: _FakeFFmpegProc(tmp_path))
    assert await video_service.extract_keyframes_for_video("kf-miss", "videos/不存在.mp4") == []
    assert calls == []


async def test_extract_skips_empty_path(monkeypatch):
    calls = _patch_ffmpeg(monkeypatch, lambda: _FakeFFmpegProc(Path("/tmp")))
    assert await video_service.extract_keyframes_for_video("kf-empty", "") == []
    assert calls == []


async def test_extract_no_frames_removes_empty_dir(monkeypatch, tmp_path):
    """ffmpeg 跑完却没产出帧：删掉空目录，让下次调用能重试。"""
    frames, _calls, out_dir = await _extract(monkeypatch, tmp_path, "kf-noframes", frames=0)

    assert frames == []
    assert not out_dir.exists()


async def test_extract_timeout_kills_process(monkeypatch, tmp_path):
    """超时要 kill 并回收，避免僵尸进程堆积。"""
    real_wait_for = asyncio.wait_for

    async def _fast_wait_for(awaitable, timeout):
        return await real_wait_for(awaitable, timeout=0.01)

    monkeypatch.setattr(video_service.asyncio, "wait_for", _fast_wait_for)
    frames, _calls, _out = await _extract(monkeypatch, tmp_path, "kf-timeout", hang=True, frames=0)

    assert frames == []


async def test_extract_kill_race_is_tolerated(monkeypatch, tmp_path):
    """kill 时进程已退出（ProcessLookupError）不能把异常抛给调用方。"""
    real_wait_for = asyncio.wait_for

    async def _fast_wait_for(awaitable, timeout):
        return await real_wait_for(awaitable, timeout=0.01)

    monkeypatch.setattr(video_service.asyncio, "wait_for", _fast_wait_for)
    frames, _calls, _out = await _extract(
        monkeypatch, tmp_path, "kf-race", hang=True, frames=0, kill_ok=False
    )

    assert frames == []


async def test_extract_ffmpeg_start_failure_is_swallowed(monkeypatch, tmp_path):
    """ffmpeg 未安装（启动即抛）时返回空列表而不是把异常抛进主流程。"""
    rel = _video_file(tmp_path, monkeypatch)

    async def _boom(*cmd, **kwargs):
        raise FileNotFoundError("ffmpeg 不在 PATH")

    monkeypatch.setattr(video_service.asyncio, "create_subprocess_exec", _boom)

    assert await video_service.extract_keyframes_for_video("kf-err", rel) == []


async def test_extract_keyframes_skips_non_video(monkeypatch):
    """图片素材不抽帧（懒提取入口按 media_type 判）。"""

    class _Insp:
        id = "img-1"
        file_path = "images/x.jpg"
        media_type = "image"

    assert await video_service.extract_keyframes(_Insp()) == []


async def test_ensure_first_frame_returns_first_or_none(monkeypatch, tmp_path):
    frames, _calls, out_dir = await _extract(monkeypatch, tmp_path, "kf-first", frames=2)

    class _Video:
        id = "kf-first"
        file_path = "videos/x.mp4"
        media_type = "video"

    first = await video_service.ensure_first_frame(_Video())
    assert first == frames[0]

    class _NoFrame:
        id = "kf-none"
        file_path = "videos/不存在.mp4"
        media_type = "video"

    assert await video_service.ensure_first_frame(_NoFrame()) is None


# ── video_service：分析帧解析 / 后台预热 ──


async def test_resolve_analysis_frames_for_image_returns_original():
    """图片素材直接返回原图路径（不抽帧，与既有单帧分析链路一致）。"""

    class _Img:
        id = "i1"
        file_path = "images/2026-10/x.jpg"
        media_type = "image"

    assert await video_service.resolve_analysis_frames(_Img()) == ["images/2026-10/x.jpg"]


async def test_resolve_analysis_frames_for_other_media_returns_empty():
    class _Other:
        id = "o1"
        file_path = "x.gif"
        media_type = "gif"

    assert await video_service.resolve_analysis_frames(_Other()) == []


async def test_resolve_analysis_frames_samples_video(monkeypatch, tmp_path):
    """视频按 video_analysis_max_frames 采样，返回相对 storage_root 的路径。"""
    frames, _calls, _out = await _extract(monkeypatch, tmp_path, "kf-resolve", frames=6)

    class _Video:
        id = "kf-resolve"
        file_path = "videos/x.mp4"
        media_type = "video"

    monkeypatch.setattr(video_service.settings, "video_analysis_max_frames", 3, raising=False)
    paths = await video_service.resolve_analysis_frames(_Video())

    assert len(paths) == 3
    assert all(p.startswith("keyframes/kf-resolve/") for p in paths)
    assert all(not Path(p).is_absolute() for p in paths)
    assert paths[0].endswith("frame_001.jpg")


async def test_resolve_analysis_frames_degrades_when_no_frames(monkeypatch, tmp_path):
    """抽帧失败（视频缺失）→ 空列表，由调用方降级，不抛异常。"""

    class _Video:
        id = "kf-none-resolve"
        file_path = "videos/不存在.mp4"
        media_type = "video"

    assert await video_service.resolve_analysis_frames(_Video()) == []


async def test_prewarm_skips_non_video():
    """非视频不调度后台任务（上传响应里不该为图片起 ffmpeg）。"""

    class _Img:
        id = "p1"
        file_path = "images/x.jpg"
        media_type = "image"

    before = len(video_service._prewarm_tasks)
    video_service.prewarm_keyframes(_Img())
    assert len(video_service._prewarm_tasks) == before


async def test_prewarm_schedules_and_swallows_failure(monkeypatch):
    """预热失败只记日志、不影响主流程，且任务结束后从引用集合移除。"""
    called: list[tuple[str, str]] = []

    async def _boom(insp_id: str, rel: str):
        called.append((insp_id, rel))
        raise RuntimeError("ffmpeg 挂了")

    monkeypatch.setattr(video_service, "extract_keyframes_for_video", _boom)

    class _Video:
        id = "p2"
        file_path = "videos/y.mp4"
        media_type = "video"

    video_service.prewarm_keyframes(_Video())
    assert len(video_service._prewarm_tasks) == 1

    await asyncio.gather(*list(video_service._prewarm_tasks), return_exceptions=True)
    await asyncio.sleep(0)  # 让 done_callback 执行

    assert called == [("p2", "videos/y.mp4")]
    assert video_service._prewarm_tasks == set()  # 不强引用已结束的任务
