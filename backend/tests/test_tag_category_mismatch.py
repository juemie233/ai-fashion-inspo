"""同名标签类别不符时的回报（2026-10）：用户把「裸足」当穿搭大标签加不上。

根因：标签按名称唯一；「裸足」早已作为小标签存在（body_part）。加标签接口对同名标签
只复用、**不改类别**，而大标签区只渲染 category=outfit → 于是「提示已添加、却看不到」。
修复是**显式回报** category_mismatch（不擅自改类别，那会把标签从小标签体系里移走），
由前端给出明确提示。
"""




def test_add_outfit_tag_reports_category_mismatch(client, upload):
    """已存在 body_part 的「裸足」，用 category=outfit 加 → 回报 mismatch 且类别不变。"""
    insp = upload().json()["id"]

    # 先按小标签（body_part）建「裸足」
    r = client.post(f"/api/inspirations/{insp}/tags", json={"names": ["裸足"], "category": "body_part"})
    assert r.status_code == 200, r.text

    # 再当穿搭大标签加一次 → 不应静默成功
    r2 = client.post(f"/api/inspirations/{insp}/tags", json={"names": ["裸足"], "category": "outfit"})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    mism = body["category_mismatch"]
    assert len(mism) == 1, body
    assert mism[0]["name"] == "裸足"
    assert mism[0]["category"] == "body_part"
    assert mism[0]["requested"] == "outfit"


def test_add_outfit_tag_new_name_has_no_mismatch(client, upload):
    """全新名字不会有不符回报（正常新增穿搭大标签）。"""
    insp = upload().json()["id"]
    r = client.post(
        f"/api/inspirations/{insp}/tags",
        json={"names": ["裸足穿搭"], "category": "outfit"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["category_mismatch"] == []
    assert r.json()["count"] == 1
