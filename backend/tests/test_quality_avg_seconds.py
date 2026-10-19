"""质量审核统计返回实测平均耗时（前端 ETA 用真实值，不再写死 12.5 秒/张）。"""


def test_quality_stats_exposes_avg_seconds(client):
    """没有成功审核样本时 avg_seconds=0（前端据此退回兜底常量）。"""
    body = client.get("/api/ai/quality-stats").json()
    assert "avg_seconds" in body
    assert body["avg_seconds"] == 0


def test_quality_stats_counts_images_only(client, upload):
    """统计只含图片，且三个状态之和等于总数（上传是否自动通过由 .env 决定，故不断言 pending）。"""
    upload()
    body = client.get("/api/ai/quality-stats").json()
    assert body["total"] >= 1
    assert body["pending"] + body["approved"] + body["rejected"] == body["total"]
