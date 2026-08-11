"""API 认证依赖：简单的 API Key 校验，保护破坏性接口。"""

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(api_key: str | None = Security(api_key_header)):
    """验证 API Key。如果未配置 api_key（开发模式）则跳过。"""
    if not settings.api_key:
        return  # 开发模式：未设置密钥则跳过认证

    if not api_key:
        raise HTTPException(status_code=401, detail="缺少 API 密钥，请在请求头中提供 X-API-Key")

    if api_key != settings.api_key:
        raise HTTPException(status_code=403, detail="API 密钥无效")

    return api_key
