"""质量审核结果 CSV 导出（审查 P3）。

手动复核/外包标注时需要把审核结果带走。带 BOM 便于 Excel 识别中文，
所有展示字段过 `sanitize_csv_cell`（原因来自模型输出，属不可信内容）。
"""


def test_export_csv_headers_and_bom(client):
    r = client.get("/api/ai/quality-export.csv")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    # BOM：Excel 打开中文不乱码
    assert r.content.startswith(b"\xef\xbb\xbf")
    text = r.content.decode("utf-8-sig")
    header = text.splitlines()[0]
    for col in ("审核ID", "素材ID", "判定", "原因码", "模型", "prompt指纹", "时间"):
        assert col in header, header
