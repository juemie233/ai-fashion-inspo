"""被拒素材抽样做 AI 生成检测（审查老 #3）。

此前只有「通过穿搭」的素材才跑 AI 检测，被拒素材的 `is_ai_generated` 恒为 False，
「AI 生成且非穿搭」这个交叉维度永远统计不到。抽样用**素材 ID 哈希**保证可复现。
"""

from app.services.ai_service.quality import _should_sample_ai_check


def test_sample_rate_bounds():
    """0 全不抽、>=1 全抽。"""
    assert _should_sample_ai_check("abc", 0) is False
    assert _should_sample_ai_check("abc", -1) is False
    assert _should_sample_ai_check("abc", 1) is True
    assert _should_sample_ai_check("abc", 2) is True


def test_sample_is_deterministic():
    """同一素材多次判定必须一致（否则复盘时无法解释为什么这条跑了、那条没跑）。"""
    first = _should_sample_ai_check("inspiration-123", 0.5)
    assert all(_should_sample_ai_check("inspiration-123", 0.5) is first for _ in range(5))


def test_sample_rate_approximately_respected():
    """大样本下抽样比例接近设定值（允许较宽误差，只验证不是恒定真假）。"""
    ids = [f"id-{i}" for i in range(2000)]
    picked = sum(1 for i in ids if _should_sample_ai_check(i, 0.1))
    assert 120 <= picked <= 280, picked  # 10% ± 相当宽的容差
