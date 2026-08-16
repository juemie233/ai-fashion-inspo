"""API 认证依赖与中间件：API Key 校验，保护破坏性接口。"""

import re
from typing import Pattern

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(api_key: str | None = Security(api_key_header)) -> str | None:
    """验证 API Key。如果未配置 api_key（开发模式）则跳过。"""
    if not settings.api_key:
        return  # 开发模式：未设置密钥则跳过认证

    if not api_key:
        raise HTTPException(status_code=401, detail="缺少 API 密钥，请在请求头中提供 X-API-Key")

    if api_key != settings.api_key:
        raise HTTPException(status_code=403, detail="API 密钥无效")

    return api_key


# ── 破坏性接口清单（认证中间件使用） ──
# 原则：**不可恢复的数据丢失或批量破坏性操作**需要 API Key 认证；
# 读接口、可恢复操作（移入垃圾桶/恢复）、配置修改、任务取消等一律不受影响。
# 路径参数用 {name} 占位；{name:path} 表示可含斜杠的路径段。
# 新增破坏性接口时在此追加一行即可。
DESTRUCTIVE_ROUTES: list[tuple[str, str]] = [
    # 数据重置
    ("DELETE", "/api/ai/reset"),
    ("POST", "/api/ai/quality-learner/reset"),
    # 素材物理删除 / 清空（不可恢复）
    ("DELETE", "/api/inspirations/trash"),
    ("DELETE", "/api/inspirations/quality-rejected"),
    ("DELETE", "/api/inspirations/{inspiration_id}"),
    ("POST", "/api/admin/batch-delete"),
    ("POST", "/api/admin/deduplicate"),
    ("POST", "/api/admin/cleanup-orphans"),
    # 标签删除 / 合并
    ("DELETE", "/api/tags/unused"),
    ("POST", "/api/tags/batch-delete"),
    ("POST", "/api/tags/merge"),
    ("POST", "/api/tags/{tag_id}/inspirations/batch-remove"),
    # 分析日志批量删除
    ("POST", "/api/ai/history/batch-delete"),
    ("DELETE", "/api/ai/history/failed/all"),
    ("DELETE", "/api/ai/history/{log_id}"),
    # 采集任务删除
    ("DELETE", "/api/scraper/tasks/{task_id}"),
    ("DELETE", "/api/scraper/tasks"),
    ("POST", "/api/scraper/tasks/{task_id}/results/batch-delete"),
    # 模型卸载（删除磁盘模型）
    ("DELETE", "/api/ai/models/{model_name:path}"),
    # 人物删除（物理删除）
    ("DELETE", "/api/persons/{person_id}"),
]


def _compile_pattern(path_template: str) -> Pattern[str]:
    """将路径模板编译为正则：{name} 匹配单段，{name:path} 匹配含斜杠的多段。"""
    # 先处理带 :path 转换器的（贪婪匹配任意路径段），再处理普通参数（单段）
    pattern = re.sub(r"\{[^}]+\:path\}", ".+", path_template)
    pattern = re.sub(r"\{[^}]+\}", "[^/]+", pattern)
    return re.compile("^" + pattern + "$")


# 预编译清单，避免每次请求重复编译
_DESTRUCTIVE_PATTERNS: list[tuple[str, Pattern[str]]] = [
    (method, _compile_pattern(path_template))
    for method, path_template in DESTRUCTIVE_ROUTES
]


def is_destructive_route(method: str, path: str) -> bool:
    """判断 (method, path) 是否命中破坏性接口清单。"""
    return any(
        m == method and pattern.match(path)
        for m, pattern in _DESTRUCTIVE_PATTERNS
    )
