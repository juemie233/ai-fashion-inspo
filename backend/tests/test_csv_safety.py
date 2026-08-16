"""CSV 导出安全工具单测：防 Excel 公式注入。"""

from app.utils.csv_safety import sanitize_csv_cell


def test_sanitize_formula_prefixes():
    """以 = + - @ 或制表符/回车开头的单元格加单引号转义。"""
    assert sanitize_csv_cell("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0"
    assert sanitize_csv_cell("+1+1") == "'+1+1"
    assert sanitize_csv_cell("-2-2") == "'-2-2"
    assert sanitize_csv_cell("@SUM(A1)") == "'@SUM(A1)"


def test_sanitize_plain_values_unchanged():
    """普通文本与 None 不做改动。"""
    assert sanitize_csv_cell("法式") == "法式"
    assert sanitize_csv_cell("hello world") == "hello world"
    assert sanitize_csv_cell(123) == "123"
    assert sanitize_csv_cell(None) == ""
    assert sanitize_csv_cell("") == ""
