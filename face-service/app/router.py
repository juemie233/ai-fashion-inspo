"""人脸识别微服务 API 路由。

接口约定（供主后端 FaceRecognitionClient 调用）：
    GET  /health                       健康检查 + 模型状态
    POST /api/face/embed               上传单张图片，返回人脸检测与特征
    POST /api/face/embed-batch         批量上传多张图片，逐张返回人脸检测与特征
                                       （无脸为正常结果 face_count=0；单张解码失败
                                        记 item 级 error，不阻塞整体）
    POST /api/face/register            注册人脸（person_id + 姓名 + 图片）
    POST /api/face/match               上传图片，返回 top-k 匹配
    GET  /api/face/persons             已注册列表
    DELETE /api/face/persons/{id}      删除注册
"""

from __future__ import annotations

import io
import logging

import cv2
import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .face import face_engine
from .storage import (
    all_embeddings,
    delete_person,
    get_person,
    init_db,
    list_persons,
    upsert_person,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# 匹配阈值：低于该余弦相似度视为「未匹配到」（insightface 惯例 0.4~0.5 区间）
MATCH_THRESHOLD = 0.45


def _decode_image(data: bytes) -> np.ndarray:
    """解码上传图片为 BGR ndarray（失败抛 400）。"""
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="无法解析图片，请上传有效的 JPG/PNG 文件")
    return img


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """余弦相似度（特征已归一化，等价于点积）。"""
    return float(np.dot(a, b))


@router.get("/health")
async def health() -> dict:
    """健康检查：返回服务状态与人脸模型加载情况。"""
    return {
        "status": "ok",
        "model_loaded": face_engine.loaded,
        "registered_count": len(list_persons()),
    }


@router.post("/api/face/embed")
async def embed_face(file: UploadFile = File(...)) -> dict:
    """上传一张图片，检测其中的人脸并返回 512 维特征向量。"""
    data = await file.read()
    img = _decode_image(data)
    try:
        faces = face_engine.extract(img)
    except Exception as e:  # noqa: BLE001
        logger.exception("特征提取失败")
        raise HTTPException(status_code=503, detail=f"人脸模型不可用: {e}") from e
    if not faces:
        raise HTTPException(status_code=404, detail="未检测到人脸")
    return {
        "face_count": len(faces),
        "faces": [
            {
                "bbox": f.bbox,
                "det_score": round(f.det_score, 4),
                "embedding": f.embedding,
            }
            for f in faces
        ],
    }


@router.post("/api/face/embed-batch")
async def embed_face_batch(files: list[UploadFile] = File(...)) -> dict:
    """批量检测人脸并提取特征（一次请求多张图，供素材库扫描使用）。

    与单图 embed 的区别：
    - 单张无脸是正常结果（face_count=0），不返回 404；
    - 单张解码失败不整体失败，记 item 级 error 并继续处理其余图片；
    - 人脸模型不可用（影响全部图片）时仍整体返回 503。
    """
    items: list[dict] = []
    for index, file in enumerate(files):
        data = await file.read()
        try:
            img = _decode_image(data)
        except HTTPException as e:
            items.append(
                {
                    "index": index,
                    "face_count": 0,
                    "faces": [],
                    "error": "decode_failed",
                    "message": e.detail,
                }
            )
            continue
        try:
            faces = face_engine.extract(img)
        except Exception as e:  # noqa: BLE001
            logger.exception("批量特征提取失败")
            raise HTTPException(status_code=503, detail=f"人脸模型不可用: {e}") from e
        items.append(
            {
                "index": index,
                "face_count": len(faces),
                "faces": [
                    {
                        "bbox": f.bbox,
                        "det_score": round(f.det_score, 4),
                        "embedding": f.embedding,
                    }
                    for f in faces
                ],
            }
        )
    return {"items": items, "failed": sum(1 for item in items if "error" in item)}


@router.post("/api/face/register")
async def register_face(
    person_id: str = Form(...),
    person_name: str = Form(...),
    file: UploadFile = File(...),
) -> dict:
    """注册人脸：提取第一张人脸的特征并持久化（同 person_id 重复注册即更新）。"""
    if not person_id.strip() or not person_name.strip():
        raise HTTPException(status_code=422, detail="person_id 与 person_name 不能为空")
    data = await file.read()
    img = _decode_image(data)
    try:
        faces = face_engine.extract(img)
    except Exception as e:  # noqa: BLE001
        logger.exception("特征提取失败")
        raise HTTPException(status_code=503, detail=f"人脸模型不可用: {e}") from e
    if not faces:
        raise HTTPException(status_code=404, detail="图片中未检测到人脸，无法注册")
    upsert_person(person_id.strip(), person_name.strip(), np.asarray(faces[0].embedding, dtype=np.float32))
    return {"registered": True, "person_id": person_id.strip(), "person_name": person_name.strip()}


@router.post("/api/face/match")
async def match_face(
    file: UploadFile = File(...),
    top_k: int = Form(5),
) -> dict:
    """上传图片，与全部已注册人脸做余弦匹配，返回 top-k（低于阈值的不返回）。"""
    data = await file.read()
    img = _decode_image(data)
    try:
        faces = face_engine.extract(img)
    except Exception as e:  # noqa: BLE001
        logger.exception("特征提取失败")
        raise HTTPException(status_code=503, detail=f"人脸模型不可用: {e}") from e
    if not faces:
        raise HTTPException(status_code=404, detail="未检测到人脸")
    registered = all_embeddings()
    if not registered:
        return {"matches": [], "face_count": len(faces)}

    query = np.asarray(faces[0].embedding, dtype=np.float32)
    scored = []
    for r in registered:
        score = _cosine_similarity(query, r["embedding"])
        if score >= MATCH_THRESHOLD:
            scored.append({"person_id": r["id"], "person_name": r["name"], "score": round(score, 4)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return {"matches": scored[: max(1, top_k)], "face_count": len(faces)}


@router.get("/api/face/persons")
async def persons() -> dict:
    """已注册人脸列表（不含特征向量）。"""
    return {"items": list_persons(), "total": len(list_persons())}


@router.delete("/api/face/persons/{person_id}")
async def remove_person(person_id: str) -> dict:
    """删除指定注册。"""
    if not delete_person(person_id):
        raise HTTPException(status_code=404, detail="未找到该注册")
    return {"deleted": True, "person_id": person_id}


init_db()
