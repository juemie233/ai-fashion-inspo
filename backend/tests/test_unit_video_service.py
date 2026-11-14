"""视频关键帧服务（`services/video_service.py`）的抽帧/兜底/采样/清理测试。

原覆盖 50.8%，缺的正好是「视频链路为什么会静默没有分析源」的关键分支：
- 抽帧过滤器：场景检测（阈值>0）与固定间隔两套参数，逗号必须转义；
- ffmpeg：启动异常、超时（kill + 回收 + 告警）都不能阻塞上传/向量/人脸主流程；
- 幂等：已有帧直接返回；**短视频按间隔抽不到帧时退化为「取前 3 帧」**，
  否则 < 间隔的抖音片段永远「不可分析」；仍无帧则删空目录以便下次重试；
- 采样：均匀采样覆盖全片且首尾优先、去重；
- 分析源：图片给原图、视频给采样帧相对路径、其它类型空；
- 清理：物理删除时删关键帧目录（失败只告警）；预热是 fire-and-forget 且异常吞掉。
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.services import video_service as vs


@pytest.fixture(autouse=True)
def _tmp_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(vs.settings, "storage_root", tmp_path)
    monkeypatch.setattr(vs.settings, "keyframes_dir", tmp_path / "keyframes")
    monkeypatch.setattr(vs.settings, "keyframe_scene_threshold", 0)
    monkeypatch.setattr(vs.settings, "keyframe_interval_seconds", 30)
    monkeypatch.setattr(vs.settings, "keyframe_max_frames", 60)
    monkeypatch.setattr(vs.settings, "video_analysis_max_frames", 3)
    vs._prewarm_tasks.clear()
    return tmp_path


def _video(tmp_path, name="videos/a.mp4") -> str:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-video")
    return name


def _frames_dir(tmp_path, inspiration_id="insp-1"):
    return tmp_path / "keyframes" / inspiration_id


async def _fake_ffmpeg_writer(frames: int):
    """返回一个 _run_ffmpeg 替身：在输出目录写 frames 个帧文件。"""

    async def _run(video_path, out_dir, extra_args, video_rel_path):
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in range(1, frames + 1):
            (out_dir / f"frame_{i:03d}.jpg").write_bytes(b"jpeg")

    return _run


class TestPathsAndFilters:
    def test_keyframes_dir_and_list(self, tmp_path):
        assert vs.keyframes_dir("abc") == tmp_path / "keyframes" / "abc"
        assert vs._list_frames(tmp_path / "missing") == []

        directory = _frames_dir(tmp_path)
        directory.mkdir(parents=True)
        (directory / "frame_002.jpg").write_bytes(b"2")
        (directory / "frame_001.jpg").write_bytes(b"1")
        assert [p.name for p in vs._list_frames(directory)] == ["frame_001.jpg", "frame_002.jpg"]

    def test_filter_args_scene_and_interval(self, monkeypatch):
        monkeypatch.setattr(vs.settings, "keyframe_scene_threshold", 0.3)
        args = vs._build_filter_args()
        assert args[0] == "-vf" and args[1].startswith("select=gt(scene")
        assert "0.3" in args[1]
        assert "\\," in args[1]  # 逗号转义，避免被当过滤器分隔符
        assert args[2:] == ["-vsync", "vfr"]

        monkeypatch.setattr(vs.settings, "keyframe_scene_threshold", 0)
        monkeypatch.setattr(vs.settings, "keyframe_interval_seconds", 5)
        assert vs._build_filter_args() == ["-vf", "fps=1/5"]

        monkeypatch.setattr(vs.settings, "keyframe_interval_seconds", 0)
        assert vs._build_filter_args() == ["-vf", "fps=1/0.1"]  # 下限 0.1 秒


class _FakeProc:
    def __init__(self, *, wait_impl=None):
        self.killed = False
        self._wait_impl = wait_impl or (lambda: asyncio.sleep(0))

    async def wait(self):
        return await self._wait_impl()

    def kill(self):
        self.killed = True


class TestRunFfmpeg:
    async def test_success_runs_command(self, monkeypatch, tmp_path):
        recorded: dict = {}
        proc = _FakeProc()

        async def _spawn(*cmd, **kwargs):
            recorded.update(cmd=cmd, kwargs=kwargs)
            return proc

        monkeypatch.setattr(vs.asyncio, "create_subprocess_exec", _spawn)

        await vs._run_ffmpeg(tmp_path / "v.mp4", tmp_path / "out", ["-vf", "x"], "videos/v.mp4")

        assert recorded["cmd"][0] == "ffmpeg"
        assert "videos/v.mp4" not in recorded["cmd"]  # 用的是绝对路径
        assert str(tmp_path / "v.mp4") in recorded["cmd"]

    async def test_timeout_kills_and_reaps(self, monkeypatch, tmp_path):
        post_timeout = {"n": 0}

        async def _slow():
            if post_timeout["n"] == 0:
                await asyncio.sleep(10)
            post_timeout["n"] += 1

        proc = _FakeProc(wait_impl=_slow)
        monkeypatch.setattr(vs.asyncio, "create_subprocess_exec", _spawn_proc(proc))
        monkeypatch.setattr(vs, "_FFMPEG_TIMEOUT", 0.01)

        await vs._run_ffmpeg(tmp_path / "v.mp4", tmp_path, [], "videos/v.mp4")  # 不抛

        assert proc.killed is True
        assert post_timeout["n"] >= 1  # 超时后回收了一次

    async def test_spawn_exception_is_logged(self, monkeypatch, tmp_path):
        async def _boom(*_a, **_k):
            raise OSError("ffmpeg 不存在")

        monkeypatch.setattr(vs.asyncio, "create_subprocess_exec", _boom)

        await vs._run_ffmpeg(tmp_path / "v.mp4", tmp_path, [], "videos/v.mp4")  # 不抛


def _spawn_proc(proc):
    async def _spawn(*_cmd, **_kwargs):
        return proc

    return _spawn


class TestExtractKeyframes:
    async def test_empty_path_or_missing_video(self, tmp_path):
        assert await vs.extract_keyframes_for_video("i1", "") == []
        assert await vs.extract_keyframes_for_video("i1", "videos/missing.mp4") == []

    async def test_idempotent_when_frames_exist(self, tmp_path, monkeypatch):
        rel = _video(tmp_path)
        directory = _frames_dir(tmp_path)
        directory.mkdir(parents=True)
        (directory / "frame_001.jpg").write_bytes(b"x")
        calls: list = []

        async def _run(*_a):
            calls.append(1)

        monkeypatch.setattr(vs, "_run_ffmpeg", _run)

        frames = await vs.extract_keyframes_for_video("insp-1", rel)

        assert len(frames) == 1
        assert calls == []  # 已有帧不重复抽

    async def test_normal_extraction(self, tmp_path, monkeypatch):
        rel = _video(tmp_path)
        monkeypatch.setattr(vs, "_run_ffmpeg", await _fake_ffmpeg_writer(2))

        frames = await vs.extract_keyframes_for_video("insp-1", rel)

        assert [p.name for p in frames] == ["frame_001.jpg", "frame_002.jpg"]

    async def test_short_video_falls_back_to_first_frames(self, tmp_path, monkeypatch):
        rel = _video(tmp_path)
        calls: list[list[str]] = []

        async def _run(_video_path, out_dir, extra_args, _rel):
            calls.append(extra_args)
            if len(calls) == 2:  # 第二次（兜底）才产出帧
                out_dir.mkdir(parents=True, exist_ok=True)
                for i in range(1, 4):
                    (out_dir / f"frame_{i:03d}.jpg").write_bytes(b"x")

        monkeypatch.setattr(vs, "_run_ffmpeg", _run)

        frames = await vs.extract_keyframes_for_video("insp-1", rel)

        assert len(frames) == 3
        assert len(calls) == 2
        assert "fps=1/30" in calls[0][1]  # 主路径：固定间隔
        assert calls[1] == ["-frames:v", "3"]  # 兜底：取前 3 帧

    async def test_no_frames_cleans_empty_dir(self, tmp_path, monkeypatch):
        rel = _video(tmp_path)

        async def _run(*_a):
            return None  # 一帧都不产出

        monkeypatch.setattr(vs, "_run_ffmpeg", _run)

        frames = await vs.extract_keyframes_for_video("insp-1", rel)

        assert frames == []
        assert not _frames_dir(tmp_path).exists()  # 空目录删掉，下次可重试

    async def test_extract_keyframes_only_for_video(self):
        image = SimpleNamespace(id="i1", file_path="images/a.jpg", media_type="image")
        assert await vs.extract_keyframes(image) == []

        video = SimpleNamespace(id="i2", file_path="", media_type="video")
        assert await vs.extract_keyframes(video) == []  # 空路径 → 空（委托内部早退）

    async def test_get_frames_and_first_frame(self, tmp_path, monkeypatch):
        directory = _frames_dir(tmp_path)
        directory.mkdir(parents=True)
        (directory / "frame_001.jpg").write_bytes(b"x")

        assert len(await vs.get_keyframes("insp-1")) == 1

        async def _extract(_inspiration):
            return [directory / "frame_001.jpg"]

        monkeypatch.setattr(vs, "extract_keyframes", _extract)
        assert (await vs.ensure_first_frame(SimpleNamespace())).name == "frame_001.jpg"

        async def _empty(_inspiration):
            return []

        monkeypatch.setattr(vs, "extract_keyframes", _empty)
        assert await vs.ensure_first_frame(SimpleNamespace()) is None


class TestSampleFrames:
    def _frames(self, n):
        return [SimpleNamespace(name=f"f{i}") for i in range(n)]

    def test_limits(self):
        frames = self._frames(5)
        assert vs.sample_frames(frames, 0) == frames  # <=0 不限制
        assert vs.sample_frames(frames, 10) == frames  # 不超过上限原样
        assert vs.sample_frames(frames, 1) == [frames[0]]

    def test_even_sampling_keeps_first_and_last(self):
        frames = self._frames(100)
        sampled = vs.sample_frames(frames, 3)
        assert sampled[0] is frames[0] and sampled[-1] is frames[-1]
        assert len(sampled) == 3

        many = vs.sample_frames(self._frames(4), 3)
        assert len(many) == len(dict.fromkeys(id(f) for f in many))  # 去重后仍唯一


class TestCleanupAndPrewarm:
    def test_cleanup_sync(self, tmp_path, monkeypatch):
        directory = _frames_dir(tmp_path)
        directory.mkdir(parents=True)
        (directory / "a.jpg").write_bytes(b"x")

        vs._cleanup_keyframes_sync("insp-1")
        assert not directory.exists()

        vs._cleanup_keyframes_sync("missing")  # 不存在 → 不抛

        directory.mkdir(parents=True)
        import shutil as _shutil

        monkeypatch.setattr(
            _shutil, "rmtree", lambda *_a, **_k: (_ for _ in ()).throw(OSError("占用"))
        )
        vs._cleanup_keyframes_sync("insp-1")  # 失败只告警

    async def test_cleanup_async_and_batch(self, tmp_path, monkeypatch):
        cleaned: list[str] = []
        monkeypatch.setattr(vs, "_cleanup_keyframes_sync", lambda i: cleaned.append(str(i)))

        await vs.cleanup_keyframes("i1")
        await vs.cleanup_keyframes_batch(["i2", "i3"])

        assert cleaned == ["i1", "i2", "i3"]

    async def test_resolve_analysis_frames(self, tmp_path, monkeypatch):
        image = SimpleNamespace(id="i1", file_path="images/a.jpg", media_type="image")
        assert await vs.resolve_analysis_frames(image) == ["images/a.jpg"]

        other = SimpleNamespace(id="i2", file_path="x.pdf", media_type="file")
        assert await vs.resolve_analysis_frames(other) == []

        video = SimpleNamespace(id="i3", file_path="videos/a.mp4", media_type="video")
        frame_paths = []
        for i in range(6):
            path = tmp_path / "keyframes" / "i3" / f"frame_{i:03d}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
            frame_paths.append(path)

        async def _extract(_inspiration):
            return frame_paths

        monkeypatch.setattr(vs, "extract_keyframes", _extract)

        resolved = await vs.resolve_analysis_frames(video)
        assert len(resolved) == 3  # 按 video_analysis_max_frames 采样
        assert all(not p.startswith("/") and "keyframes/i3" in p for p in resolved)

        async def _none(_inspiration):
            return []

        monkeypatch.setattr(vs, "extract_keyframes", _none)
        assert await vs.resolve_analysis_frames(video) == []

    async def test_prewarm_only_video_and_swallows_errors(self, monkeypatch):
        await asyncio.sleep(0)
        called: list[tuple] = []

        async def _extract(insp_id, rel_path):
            called.append((insp_id, rel_path))
            raise RuntimeError("ffmpeg 失败")  # 预热失败必须吞掉

        monkeypatch.setattr(vs, "extract_keyframes_for_video", _extract)

        vs.prewarm_keyframes(SimpleNamespace(id="i1", file_path="images/a.jpg", media_type="image"))
        await asyncio.sleep(0)
        assert called == []  # 图片不预热

        vs.prewarm_keyframes(SimpleNamespace(id="i2", file_path="videos/a.mp4", media_type="video"))
        await asyncio.sleep(0.05)
        assert called == [("i2", "videos/a.mp4")]
        assert not vs._prewarm_tasks  # 任务结束后从引用集合移除
