"""人脸识别子服务客户端：主后端（Python 3.12）通过 HTTP 调用独立人脸识别微服务。

子服务（face-service）在独立 Python 3.10 环境运行 insightface（主后端 3.12
不兼容），提供人脸检测、特征提取、注册与匹配能力。本客户端统一封装调用，
业务方通过 ``face_client`` 单例使用；子服务未部署（FACE_SERVICE_URL 留空）
时调用会抛出 ``FaceServiceUnavailableError``。
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class FaceServiceUnavailableError(RuntimeError):
    """人脸识别子服务不可用（未配置或请求失败）。"""


class FaceServiceHttpError(FaceServiceUnavailableError):
    """人脸识别子服务返回了非 2xx 响应（如 embed 未检测到人脸返回 404）。

    携带子服务状态码与业务消息，供调用方区分「业务结果」与「服务故障」。
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"人脸识别子服务错误 {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class FaceRecognitionClient:
    """人脸识别子服务的异步 HTTP 客户端。"""

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self.base_url = (base_url or settings.face_service_url).rstrip("/")
        self.timeout = timeout or settings.face_service_timeout

    @property
    def enabled(self) -> bool:
        """是否已配置子服务地址。"""
        return bool(self.base_url)

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        if not self.enabled:
            raise FaceServiceUnavailableError("未配置人脸识别子服务（FACE_SERVICE_URL）")
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.request(method, url, **kwargs)
                r.raise_for_status()
                return r.json()
        except httpx.HTTPStatusError as e:
            logger.warning(
                "人脸识别子服务返回错误 %s: %s", e.response.status_code, e.response.text
            )
            # 解析子服务的业务消息（FastAPI 错误响应 {"detail": "..."}）
            try:
                detail = e.response.json().get("detail", e.response.text)
            except ValueError:
                detail = e.response.text
            raise FaceServiceHttpError(e.response.status_code, detail) from e
        except httpx.HTTPError as e:
            logger.warning("人脸识别子服务请求失败: %s", e)
            raise FaceServiceUnavailableError(f"人脸识别子服务不可用: {e}") from e

    async def health(self) -> dict:
        """健康检查：服务状态 + 模型加载情况 + 已注册数量。"""
        return await self._request("GET", "/health")

    async def embed(self, image_bytes: bytes, filename: str = "image.jpg") -> dict:
        """人脸检测 + 特征提取（图片字节 → 人脸列表与 512 维特征）。"""
        return await self._request(
            "POST",
            "/api/face/embed",
            files={"file": (filename, image_bytes, "application/octet-stream")},
        )

    async def register(self, person_id: str, person_name: str, image_bytes: bytes) -> dict:
        """注册人脸（同 person_id 重复注册即更新）。"""
        return await self._request(
            "POST",
            "/api/face/register",
            data={"person_id": person_id, "person_name": person_name},
            files={"file": ("image.jpg", image_bytes, "application/octet-stream")},
        )

    async def match(self, image_bytes: bytes, top_k: int = 5) -> dict:
        """人脸匹配：返回 top-k（余弦相似度，低于阈值不返回）。"""
        return await self._request(
            "POST",
            "/api/face/match",
            data={"top_k": top_k},
            files={"file": ("image.jpg", image_bytes, "application/octet-stream")},
        )

    async def list_persons(self) -> dict:
        """已注册人脸列表。"""
        return await self._request("GET", "/api/face/persons")

    async def delete_person(self, person_id: str) -> dict:
        """删除指定注册。"""
        return await self._request("DELETE", f"/api/face/persons/{person_id}")


# 全局单例（URL 从配置读取，测试可自行实例化覆盖）
face_client = FaceRecognitionClient()
