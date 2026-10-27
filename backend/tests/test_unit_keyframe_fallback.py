"""关键帧提取：短于抽帧间隔的视频必须能兜底取到帧。

真实缺陷（2026-10）：抽帧用固定间隔 ``fps=1/interval``（默认 3 秒一帧），而**短于
间隔**的视频一帧都抽不到——实测 0.7~2.7 秒的抖音片段，ffmpeg 直接报
``Output file is empty, nothing was encoded``。抽不到帧 → 素材被记作「分析源不可用」
跳过且不写日志 → 永远挂在「未分析」里（当时 144 个未分析里有 134 个就是这种）。

现在：间隔抽帧一帧都没产出时，退化「取前 3 帧」（去掉 fps 过滤）。
"""

import pytest

from app.services import video_service as vs


def _video(rel_path: str = "test/kf_clip.mp4") -> str:
    """在测试存储根下造一个假视频文件，返回相对路径。"""
    from app.config import settings

    full = settings.storage_root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(b"not-a-real-video")
    return rel_path


@pytest.fixture(autouse=True)
def _isolate_keyframes(tmp_path, monkeypatch):
    """关键帧目录指到临时目录，别把帧写进测试存储根的共享位置。"""
    monkeypatch.setattr(vs, "keyframes_dir", lambda _insp_id: tmp_path / "kf")


async def test_falls_back_to_first_frames_when_interval_yields_nothing(monkeypatch):
    """间隔抽帧产出 0 帧时，用「取前 3 帧」再试一次并返回结果。"""
    rel_path = _video()
    calls: list[list[str]] = []

    async def _fake_run(video_path, out_dir, extra_args, video_rel_path):
        calls.append(list(extra_args))
        if len(calls) == 2:  # 只有兜底那次真的写出一帧
            (out_dir / "frame_001.jpg").write_bytes(b"jpeg")

    monkeypatch.setattr(vs, "_run_ffmpeg", _fake_run)

    frames = await vs.extract_keyframes_for_video("insp-1", rel_path)

    assert len(frames) == 1
    assert len(calls) == 2
    # 第一次是固定间隔（-vf fps=...），第二次是兜底（只限制帧数，不再按间隔抽）
    assert calls[0][0] == "-vf" and "fps=" in calls[0][1]
    assert calls[1] == ["-frames:v", "3"]


async def test_returns_empty_and_cleans_dir_when_both_attempts_fail(monkeypatch, tmp_path):
    """两次都抽不到：返回空列表，且不留下空的关键帧目录（下次还能重试）。"""

    async def _noop(video_path, out_dir, extra_args, video_rel_path):
        return None

    monkeypatch.setattr(vs, "_run_ffmpeg", _noop)

    frames = await vs.extract_keyframes_for_video("insp-2", _video("test/kf_clip2.mp4"))

    assert frames == []
    assert not (tmp_path / "kf").exists()


async def test_missing_video_returns_empty_without_running_ffmpeg(monkeypatch):
    """视频文件缺失：直接返回空，不启动 ffmpeg（避免每次分析白等一个进程）。"""
    started = False

    async def _should_not_run(*_args, **_kwargs):  # pragma: no cover - 不该被调用
        nonlocal started
        started = True

    monkeypatch.setattr(vs, "_run_ffmpeg", _should_not_run)

    assert await vs.extract_keyframes_for_video("insp-3", "test/not-exists.mp4") == []
    assert started is False
