"""人脸识别引擎：onnxruntime 直接加载 buffalo_l 的 onnx 模型。

无需 insightface 包（Python 3.12 不兼容且源码编译需要 MSVC），
模型本身就是 onnx 格式：
    det_10g.onnx      RetinaFace 人脸检测（immich 重构版：动态输入 + 每 stride 独立输出）
    w600k_r50.onnx    ArcFace 512 维特征提取

检测模型输出（输入 640x640 时）：
    score: (12800,1) / (3200,1) / (800,1)     三个 stride（8/16/32）的人脸概率
    bbox:  (12800,4) / (3200,4) / (800,4)      中心距离表示的框
    kps:   (12800,10) / (3200,10) / (800,10)   5 点关键点
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
DET_MODEL = MODELS_DIR / "buffalo_l" / "det_10g.onnx"
REC_MODEL = MODELS_DIR / "buffalo_l" / "w600k_r50.onnx"

DET_SIZE = 640  # 检测输入边长（方形，immich 动态输入模型按此固定）
REC_SIZE = 112  # 识别输入边长
DET_THRESHOLD = 0.5  # 检测置信度阈值
NMS_THRESHOLD = 0.4  # NMS IoU 阈值
# det_10g 的 anchor 配置：stride 8/16/32，每格 2 个 anchor
DET_STRIDES = (8, 16, 32)
DET_ANCHORS_PER_POINT = 2

# ArcFace 对齐目标 5 点（112x112：左眼/右眼/鼻尖/左嘴角/右嘴角）
ARC_FACE_SRC = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


@dataclass
class FaceResult:
    """单张人脸的检测与特征结果。"""

    bbox: list[float] = field(default_factory=list)  # [x1, y1, x2, y2]（原图坐标）
    det_score: float = 0.0
    embedding: list[float] = field(default_factory=list)  # 512 维归一化特征


def _distance2bbox(points: np.ndarray, distance: np.ndarray, stride: int) -> np.ndarray:
    """RetinaFace 中心距离表示 → xyxy 框。

    immich 重构版 det_10g 的 bbox 输出为「stride 归一化距离」
    （距离以 stride 为单位），必须乘以 stride 才是像素距离；
    直接当像素使用会导致框被缩小 stride 倍（漏检/误判小脸）。
    """
    x1 = points[:, 0] - distance[:, 0] * stride
    y1 = points[:, 1] - distance[:, 1] * stride
    x2 = points[:, 0] + distance[:, 2] * stride
    y2 = points[:, 1] + distance[:, 3] * stride
    return np.stack([x1, y1, x2, y2], axis=-1)


def _distance2kps(points: np.ndarray, distance: np.ndarray, stride: int) -> np.ndarray:
    """中心距离表示 → 5 点关键点（10 维），同样乘 stride 还原像素坐标。"""
    preds = []
    for i in range(0, distance.shape[1], 2):
        preds.append(points[:, 0] + distance[:, i] * stride)
        preds.append(points[:, 1] + distance[:, i + 1] * stride)
    return np.stack(preds, axis=-1)


def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float = 0.4) -> list[int]:
    """标准 NMS（xyxy 框），返回保留索引。"""
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_o = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        iou = inter / (area_i + area_o - inter + 1e-9)
        order = rest[iou <= threshold]
    return keep


class FaceEngine:
    """buffalo_l（RetinaFace + ArcFace）onnx 推理封装：懒加载 + 线程安全。"""

    def __init__(self) -> None:
        self._det: ort.InferenceSession | None = None
        self._rec: ort.InferenceSession | None = None
        self._lock = threading.Lock()
        self._anchors: np.ndarray | None = None

    @property
    def loaded(self) -> bool:
        return self._det is not None and self._rec is not None

    def ensure_loaded(self) -> None:
        """加载检测与识别模型（GPU 优先，自动回退 CPU）。"""
        if self.loaded:
            return
        with self._lock:
            if self.loaded:
                return
            for path in (DET_MODEL, REC_MODEL):
                if not path.exists():
                    raise FileNotFoundError(
                        f"缺少模型文件: {path}（请将 buffalo_l 模型放入 models/buffalo_l/）"
                    )
            gpu_providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            cpu_providers = ["CPUExecutionProvider"]
            try:
                self._det = ort.InferenceSession(str(DET_MODEL), providers=gpu_providers)
            except Exception as e:  # noqa: BLE001
                logger.warning("检测模型 GPU 推理不可用，回退 CPU: %s", e)
                self._det = ort.InferenceSession(str(DET_MODEL), providers=cpu_providers)
            try:
                self._rec = ort.InferenceSession(str(REC_MODEL), providers=gpu_providers)
            except Exception as e:  # noqa: BLE001
                logger.warning("识别模型 GPU 推理不可用，回退 CPU: %s", e)
                self._rec = ort.InferenceSession(str(REC_MODEL), providers=cpu_providers)
            logger.info(
                "人脸模型加载完成（det: %s, rec: %s, 实际后端: %s）",
                DET_MODEL.name,
                REC_MODEL.name,
                self._det.get_providers(),
            )

    def _get_anchors(self, n_anchors: int) -> np.ndarray:
        """生成 det_10g 的 anchor 中心点（stride 8/16/32 顺序拼接，每格 2 个）。"""
        if self._anchors is not None and self._anchors.shape[0] == n_anchors:
            return self._anchors
        centers: list[np.ndarray] = []
        for stride in DET_STRIDES:
            h = DET_SIZE // stride
            w = DET_SIZE // stride
            grid = np.stack(np.mgrid[:h, :w][::-1], axis=-1)  # (h, w, 2) 每格 (列, 行)
            grid = (grid.reshape(-1, 2) * stride).astype(np.float32)
            if DET_ANCHORS_PER_POINT > 1:
                grid = np.stack([grid] * DET_ANCHORS_PER_POINT, axis=1).reshape(-1, 2)
            centers.append(grid)
        self._anchors = np.concatenate(centers, axis=0)
        return self._anchors

    def detect(self, image_bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """检测人脸。

        返回:
            boxes: [n, 4] xyxy（原图坐标）
            kpss: [n, 10] 5 点关键点（原图坐标）
            scores: [n] 检测置信度
        """
        self.ensure_loaded()
        assert self._det is not None
        h, w = image_bgr.shape[:2]
        scale = DET_SIZE / max(h, w)
        nh, nw = int(round(h * scale)), int(round(w * scale))
        resized = cv2.resize(image_bgr, (nw, nh))
        canvas = np.zeros((DET_SIZE, DET_SIZE, 3), dtype=np.uint8)
        canvas[:nh, :nw] = resized
        blob = (canvas.astype(np.float32) - 127.5) / 128.0
        blob = np.transpose(blob, (2, 0, 1))[None, ...]  # (1, 3, 640, 640)

        input_name = self._det.get_inputs()[0].name
        outs = self._det.run(None, {input_name: blob})
        # immich 版输出：outs[0:3] 为各 stride 的 score，outs[3:6] 为 bbox，outs[6:9] 为 kps；
        # 输出顺序不保证按 stride 排列，按行数（N = h*w*2）降序匹配 stride 8/16/32
        groups = sorted(
            zip(outs[0:3], outs[3:6], outs[6:9]),
            key=lambda t: t[0].shape[0],
            reverse=True,
        )
        centers = self._get_anchors(sum(g[0].shape[0] for g in groups))
        all_boxes: list[np.ndarray] = []
        all_kpss: list[np.ndarray] = []
        all_scores: list[np.ndarray] = []
        offset = 0
        for (score, bbox, kps), stride in zip(groups, DET_STRIDES):
            n = score.shape[0]
            seg = centers[offset : offset + n]
            offset += n
            s = score[:, 0]
            pos = np.where(s >= DET_THRESHOLD)[0]
            if pos.size == 0:
                continue
            boxes = _distance2bbox(seg[pos], bbox[pos], stride)
            kpss = _distance2kps(seg[pos], kps[pos], stride)
            scores = s[pos]
            keep = _nms(boxes, scores, NMS_THRESHOLD)
            all_boxes.append(boxes[keep])
            all_kpss.append(kpss[keep])
            all_scores.append(scores[keep])

        if not all_boxes:
            return (
                np.zeros((0, 4), dtype=np.float32),
                np.zeros((0, 10), dtype=np.float32),
                np.zeros((0,), dtype=np.float32),
            )
        boxes = np.concatenate(all_boxes, axis=0)
        kpss = np.concatenate(all_kpss, axis=0)
        scores = np.concatenate(all_scores, axis=0)
        # 跨尺度重复框再做一次整体 NMS
        keep = _nms(boxes, scores, NMS_THRESHOLD)
        # 映射回原图坐标
        return boxes[keep] / scale, kpss[keep] / scale, scores[keep]

    def _align_crops(self, image_bgr: np.ndarray, kpss: np.ndarray) -> np.ndarray:
        """按 5 点关键点仿射对齐到 112x112，输出 NCHW 归一化 RGB。"""
        crops = []
        for kps in kpss:
            pts = kps.reshape(5, 2).astype(np.float32)
            M, _ = cv2.estimateAffinePartial2D(pts, ARC_FACE_SRC, method=cv2.LMEDS)
            if M is None:
                M, _ = cv2.estimateAffinePartial2D(pts, ARC_FACE_SRC)
            aligned = cv2.warpAffine(image_bgr, M, (REC_SIZE, REC_SIZE), borderValue=0.0)
            aligned = cv2.cvtColor(aligned, cv2.COLOR_BGR2RGB)
            aligned = (aligned.astype(np.float32) - 127.5) / 128.0
            aligned = np.transpose(aligned, (2, 0, 1))
            crops.append(aligned)
        return np.stack(crops, axis=0)

    def _get_embeddings(self, crops: np.ndarray) -> np.ndarray:
        """提取归一化 512 维特征（模型仅支持单张，逐张推理）。"""
        assert self._rec is not None
        input_name = self._rec.get_inputs()[0].name
        embs = []
        for crop in crops:
            emb = self._rec.run(None, {input_name: crop[None, ...]})[0][0]
            norm = float(np.linalg.norm(emb))
            embs.append(emb / (norm + 1e-9))
        return np.stack(embs, axis=0)

    def extract(self, image_bgr: np.ndarray) -> list[FaceResult]:
        """从 BGR 图像中检测人脸并提取特征。"""
        boxes, kpss, scores = self.detect(image_bgr)
        if boxes.shape[0] == 0:
            return []
        crops = self._align_crops(image_bgr, kpss)
        embeddings = self._get_embeddings(crops)
        results: list[FaceResult] = []
        for box, kps, s, emb in zip(boxes, kpss, scores, embeddings):
            results.append(
                FaceResult(
                    bbox=[float(v) for v in box],
                    det_score=float(s),
                    embedding=[float(v) for v in emb],
                )
            )
        return results


face_engine = FaceEngine()
