"""视频素材参与质量审核（审查老 #1）。

此前质量审核只取 `media_type == "image"`，视频既不通过也不拒绝、永远 pending，
在统计里也不出现（分母都算错）。现在视频按「上传时 ffmpeg 抽的首帧缩略图」送审，
统计口径同步纳入视频。
"""

import asyncio

from sqlalchemy import text

from app.database import async_session
from app.services.task_runners.quality_check import _audit_path


def test_audit_path_uses_thumbnail_for_video():
    """视频用首帧缩略图送审；没有缩略图才退回原文件。"""
    assert _audit_path("video", "videos/a.mp4", "thumbs/a.jpg") == "thumbs/a.jpg"
    assert _audit_path("video", "videos/a.mp4", None) == "videos/a.mp4"
    # 图片始终用原文件（缩略图可能被压缩，审核要看清细节）
    assert _audit_path("image", "images/a.jpg", "thumbs/a.jpg") == "images/a.jpg"


def test_quality_stats_counts_videos(client, upload):
    """统计口径纳入视频：把一条素材改成视频后，它仍应计入 total。"""
    insp = upload().json()

    async def _make_video():
        async with async_session() as db:
            await db.execute(
                text(
                    "UPDATE inspirations SET media_type = 'video', thumbnail_path = file_path "
                    "WHERE id = :i"
                ),
                {"i": insp["id"]},
            )
            await db.commit()

    asyncio.run(_make_video())

    body = client.get("/api/ai/quality-stats").json()
    assert body["total"] >= 1, body
    assert body["pending"] + body["approved"] + body["rejected"] == body["total"]
