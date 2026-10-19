"""按来源统计质量审核通过率（审查 P2）。

用来回答「哪个采集源/平台质量差」——通过率只在**已判定**素材上计算，
pending 不进分母，避免「待审多」的来源看起来质量差。
"""


def test_stats_by_source_shape(client, upload):
    upload()
    body = client.get("/api/ai/quality-stats-by-source").json()
    assert "items" in body and "total" in body
    assert body["total"] >= 1
    row = body["items"][0]
    for key in ("source_type", "total", "approved", "rejected", "pending", "pass_rate"):
        assert key in row, row
    assert row["total"] == row["approved"] + row["rejected"] + row["pending"]
    assert 0 <= row["pass_rate"] <= 100


def test_stats_by_source_pass_rate_ignores_pending(client, upload):
    """全部待审时通过率为 0（而不是把 pending 当分母算出奇怪的数）。"""
    upload()
    body = client.get("/api/ai/quality-stats-by-source").json()
    for row in body["items"]:
        decided = row["approved"] + row["rejected"]
        if decided == 0:
            assert row["pass_rate"] == 0, row
