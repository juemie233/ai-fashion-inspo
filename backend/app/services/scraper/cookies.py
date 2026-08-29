"""采集平台 Cookie 管理：状态检查、导入与删除（按平台白名单隔离）。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from app.config import settings

# Cookie 平台白名单
_COOKIE_PLATFORMS = {"xiaohongshu", "douyin"}


def _validate_cookie_platform(platform: str) -> str:
    """校验并标准化平台名，防止路径穿越。"""
    p = platform.strip().lower()
    if p not in _COOKIE_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"不支持的平台: {platform}，允许: {_COOKIE_PLATFORMS}")
    return p


async def get_cookie_status(platform: str = "xiaohongshu") -> dict:
    """检查指定平台的 Cookie 文件状态。"""
    platform = _validate_cookie_platform(platform)
    cookie_dir = Path(settings.storage_root) / "cookies"
    cookie_file = cookie_dir / f"{platform}_cookies.json"

    if not cookie_file.exists():
        return {
            "platform": platform,
            "exists": False,
            "size_bytes": 0,
            "modified": None,
            "valid": False,
            "verify": None,
            "hint": f"尚未导入 {platform} 的 Cookie，采集可能无法获取完整数据",
        }

    stat = cookie_file.stat()
    age_hours = (datetime.now().timestamp() - stat.st_mtime) / 3600

    # 附带最近一次真实登录态校验结果（无则 None，管理页显示「未验证」）
    from app.services.scraper.cookie_verify import peek_verification

    return {
        "platform": platform,
        "exists": True,
        "size_bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "age_hours": round(age_hours, 1),
        "valid": age_hours < 72,  # Cookie 通常在 72 小时内有效
        "verify": peek_verification(platform),
        "hint": "Cookie 可用" if age_hours < 72 else f"Cookie 已过期 {round(age_hours)} 小时，建议重新导入",
    }


async def import_cookies(payload: dict) -> dict:
    """导入平台 Cookie（JSON 格式，自动校验平台合法性）。"""
    platform = _validate_cookie_platform(payload.get("platform", "xiaohongshu"))
    cookie_data = payload.get("cookies")

    if not cookie_data:
        raise HTTPException(status_code=400, detail="请提供 Cookie 数据")

    cookie_dir = Path(settings.storage_root) / "cookies"
    cookie_dir.mkdir(parents=True, exist_ok=True)
    cookie_file = cookie_dir / f"{platform}_cookies.json"

    cookie_file.write_text(json.dumps(cookie_data, ensure_ascii=False, indent=2), encoding="utf-8")
    count = len(cookie_data) if isinstance(cookie_data, list) else 0

    # 新 Cookie 写入后清掉旧校验缓存（下次校验按新文件内容探测）
    from app.services.scraper.cookie_verify import invalidate_verification
    invalidate_verification(platform)

    return {
        "message": f"已导入 {platform} Cookie",
        "platform": platform,
        "imported": count,
        "valid": True,  # 刚写入的文件视为有效
    }


async def delete_cookies(platform: str) -> dict:
    """删除指定平台的 Cookie 文件（不影响已导入的素材与任务）。"""
    platform = _validate_cookie_platform(platform)
    cookie_dir = Path(settings.storage_root) / "cookies"
    cookie_file = cookie_dir / f"{platform}_cookies.json"

    if not cookie_file.exists():
        raise HTTPException(status_code=404, detail="Cookie 文件不存在")

    cookie_file.unlink()

    from app.services.scraper.cookie_verify import invalidate_verification
    invalidate_verification(platform)

    return {"message": f"已删除 {platform} Cookie", "platform": platform}
