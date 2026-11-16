"""IP 属地表征解析（采集路径与 f2 资料回填共用）。"""

import re


def parse_ip_location(text: str) -> str:
    """从文本中解析 IP 属地（如「IP属地：浙江」→ 浙江）。

    仅识别带「IP属地：」前缀的写法，避免把昵称等其它文本误判为属地。
    """
    if not text:
        return ""
    match = re.search(r"IP属地[:：]\s*([^\s，,；;()（）]+)", text)
    return match.group(1).strip() if match else ""
