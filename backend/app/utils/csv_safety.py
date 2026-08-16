"""CSV 导出安全工具：防止 Excel/表格软件的公式注入。

CSV 单元格若以 = + - @ 或制表符/回车开头，会被 Excel、WPS、Google Sheets
等当作公式执行（如 ``=cmd|...`` 触发命令提示），属经典 CSV 注入漏洞。
导出用户/模型可控数据（标签名、作者、来源 URL、错误信息等）前应对每个
单元格调用 sanitize_csv_cell。
"""

# 以这些字符开头的单元格会被表格软件当作公式
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_csv_cell(value: object) -> str:
    """返回安全的 CSV 单元格文本；公式前缀加单引号转义，None 返回空串。"""
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text
