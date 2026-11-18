"""API 认证依赖与中间件：API Key 校验，保护破坏性接口。"""

import hmac
import ipaddress
import logging
import re

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from app.config import settings

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# 未配置 API_KEY 时拒绝非回环访问的统一文案（中间件与依赖共用，避免两处漂移）
NO_API_KEY_LOOPBACK_ONLY_DETAIL = "未配置 API_KEY，仅允许本机访问；局域网/手机访问请设置 API_KEY"

# 主机名形式的本机来源；TestClient 的默认 client host 固定为 testclient
_LOOPBACK_HOSTNAMES = frozenset({"localhost", "testclient"})


def is_loopback_host(host: str | None) -> bool:
    """判断请求的 TCP 对端地址是否来自本机回环。

    只用于 ``request.client.host``；**禁止**把 ``X-Forwarded-For`` / ``X-Real-IP``
    这类客户端可控请求头传进来——那等于让调用方自证来源，限制形同虚设。

    放行范围：127.0.0.0/8、::1、IPv4-mapped IPv6（``::ffff:127.0.0.1``）、
    localhost 与 testclient（pytest TestClient 的固定 client host）。
    """
    if not host:
        return False
    normalized = host.strip().lower()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    if normalized in _LOOPBACK_HOSTNAMES:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError as exc:
        # 非 IP 形式（如 DNS 主机名）：按非回环处理（放行名单已在上面单独判断）
        logger.debug("is_loopback_host: 忽略异常（非 IP 主机名）：%s（%s）", normalized, exc)
        return False
    if address.is_loopback:
        return True
    # IPv4-mapped IPv6 形式（部分平台/代理链会把 127.0.0.1 报成 ::ffff:127.0.0.1）
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


async def require_api_key(
    request: Request,
    api_key: str | None = Security(api_key_header),
) -> str | None:
    """验证 API Key；未配置 api_key 时只允许回环地址访问。

    与 ``destructive_api_key_middleware`` 口径一致：未配置密钥 ≠ 无鉴权，而是把
    安全边界收到本机。``DESTRUCTIVE_ROUTES`` 清单由中间件统一覆盖；清单之外仍
    需要显式鉴权的破坏性接口用本依赖兜底。
    """
    if not settings.api_key:
        client_host = request.client.host if request.client else None
        if not is_loopback_host(client_host):
            logger.warning("拒绝非回环访问：未配置 API_KEY（client=%s）", client_host)
            raise HTTPException(status_code=401, detail=NO_API_KEY_LOOPBACK_ONLY_DETAIL)
        return None

    if not api_key:
        raise HTTPException(status_code=401, detail="缺少 API 密钥，请在请求头中提供 X-API-Key")

    # 常数时间比较，避免时序侧信道泄露密钥前缀
    if not hmac.compare_digest(api_key, settings.api_key):
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
    # 标签批量高级编辑（dry_run=false 改名/合并）
    ("POST", "/api/tags/batch-edit"),
    # 同效的 PATCH 批量改名 / 批量改类别：与 batch-edit 的效果相同（批量覆盖写），
    # 2026-10-20 审查发现此前只有 POST batch-edit 进了清单——同一个破坏效果换个
    # 方法就能绕过认证，属于「清单与路由漂移」。
    ("PATCH", "/api/tags/batch-rename"),
    ("PATCH", "/api/tags/batch-category"),
    # 自动聚类应用（批量合并标签，删除源标签）
    ("POST", "/api/tags/clusters/apply"),
    # 标签操作历史回滚（批量写回数据）
    ("POST", "/api/tags/history/{history_id}/rollback"),
    # 分析日志批量删除
    ("POST", "/api/ai/history/batch-delete"),
    ("DELETE", "/api/ai/history/failed/all"),
    ("DELETE", "/api/ai/history/{log_id}"),
    # 采集任务删除
    ("DELETE", "/api/scraper/tasks/{task_id}"),
    ("DELETE", "/api/scraper/tasks"),
    ("POST", "/api/scraper/tasks/{task_id}/results/batch-delete"),
    # f2 结果审查的「彻底删除」（不可恢复；同批的移入垃圾桶/还原属可恢复操作，不进清单）
    ("POST", "/api/scraper/f2-tasks/{task_id}/results/delete"),
    # 出图批次丢弃：删除该批次尚未入库的候选目录，文件不可恢复
    # （DELETE /api/scraper/schedules/{id} 只是删一条可重建的计划配置，
    #  与「配置修改不进清单」同口径，刻意不列入）
    ("DELETE", "/api/generate/runs/{run_id}"),
    # 合集删除：手动合集会级联删掉全部成员关系（成员清单不可恢复）
    ("DELETE", "/api/collections/{collection_id}"),
    # 任务队列历史记录删除（仅终态任务，物理删除）
    ("DELETE", "/api/tasks/{task_id}"),
    # 模型卸载（删除磁盘模型）
    ("DELETE", "/api/ai/models/{model_name:path}"),
    # 人物删除（物理删除）：博主与模特已拆分两表
    ("DELETE", "/api/bloggers/{blogger_id}"),
    ("DELETE", "/api/models/{model_id}"),
    # 博主人物组绑定/解绑/切主（方案 B：跨平台账号合并声明，写操作）
    ("POST", "/api/bloggers/groups/link"),
    ("POST", "/api/bloggers/groups/unlink"),
    ("POST", "/api/bloggers/groups/{group_id}/set-primary"),
    # 模特照片删除（物理删除）
    ("DELETE", "/api/models/{model_id}/photo-sets/{set_id}"),
    ("DELETE", "/api/models/{model_id}/photo-sets/{set_id}/photos/{photo_id}"),
    # 手机截图裁剪「应用」：会**不可逆改写原图**（备份在 storage/_crop_backup，需人工恢复），
    # 并同步重算内容哈希 / 缩略图 / 向量 —— 属批量破坏性写，2026-10-20 审查补入。
    ("POST", "/api/admin/crop-phone-screenshots/apply"),
]


def _compile_pattern(path_template: str) -> re.Pattern[str]:
    """将路径模板编译为正则：{name} 匹配单段，{name:path} 匹配含斜杠的多段。"""
    # 先处理带 :path 转换器的（贪婪匹配任意路径段），再处理普通参数（单段）
    pattern = re.sub(r"\{[^}]+\:path\}", ".+", path_template)
    pattern = re.sub(r"\{[^}]+\}", "[^/]+", pattern)
    return re.compile("^" + pattern + "$")


# 预编译清单，避免每次请求重复编译
_DESTRUCTIVE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (method, _compile_pattern(path_template))
    for method, path_template in DESTRUCTIVE_ROUTES
]


def is_destructive_route(method: str, path: str) -> bool:
    """判断 (method, path) 是否命中破坏性接口清单。"""
    return any(
        m == method and pattern.match(path)
        for m, pattern in _DESTRUCTIVE_PATTERNS
    )
