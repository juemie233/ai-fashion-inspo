"""双腿姿态关键点特征提取（只读、不写库）。

用本地 MediaPipe PoseLandmarker 提取髋/膝/踝关键点，输出可复核的几何特征：
- 腿部可见性（髋/膝/踝是否在画面内且置信度足够）
- 膝间距、踝间距 / 髋宽
- 是否交叉、是否单腿抬起
- 站立/蹲跪的膝角近似

用法：
    python backend/scripts/leg_posture_keypoints.py \
        --gold-dir G:/fashion-inspo-storage/audit/leg-posture-gold-20261124-205716 \
        --json G:/fashion-inspo-storage/audit/leg-keypoints-pilot.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import cv2
from mediapipe import Image, ImageFormat
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import (
    PoseLandmarker,
    PoseLandmarkerOptions,
    RunningMode,
)

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.config import settings  # noqa: E402

MODEL_PATH = Path(settings.storage_root) / "models" / "pose" / "pose_landmarker_lite.task"
# MediaPipe Pose 33 点索引
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28
L_FOOT, R_FOOT = 31, 32


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="腿部姿态关键点特征提取（只读）")
    parser.add_argument(
        "--gold-dir",
        type=Path,
        default=Path(settings.storage_root) / "audit" / "leg-posture-gold-20261124-205716",
    )
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument(
        "--labels-file",
        type=Path,
        default=None,
        help="人工标签文件（默认 pilot-labels-40.json）",
    )
    return parser.parse_args()


def _landmarker() -> PoseLandmarker:
    options = PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(MODEL_PATH)),
        running_mode=RunningMode.IMAGE,
        num_poses=1,
        min_pose_detection_confidence=0.3,
        min_pose_presence_confidence=0.3,
        min_tracking_confidence=0.3,
    )
    return PoseLandmarker.create_from_options(options)


def _visible(point, threshold: float = 0.5) -> bool:
    return (
        float(getattr(point, "visibility", 0) or 0) >= threshold
        and float(getattr(point, "presence", 0) or 0) >= threshold
        and 0.0 <= point.x <= 1.0
        and 0.0 <= point.y <= 1.0
    )


def _dist(a, b, width: int, height: int) -> float:
    return math.hypot((a.x - b.x) * width, (a.y - b.y) * height)


def _angle(a, b, c, width: int, height: int) -> float:
    ax, ay = (a.x - b.x) * width, (a.y - b.y) * height
    cx, cy = (c.x - b.x) * width, (c.y - b.y) * height
    denom = math.hypot(ax, ay) * math.hypot(cx, cy)
    if denom <= 0:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, (ax * cx + ay * cy) / denom))))


def _extract(landmarker: PoseLandmarker, path: Path) -> dict | None:
    image = cv2.imread(str(path))
    if image is None:
        return None
    height, width = image.shape[:2]
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    result = landmarker.detect(Image(image_format=ImageFormat.SRGB, data=rgb))
    if not result.pose_landmarks:
        return None
    points = result.pose_landmarks[0]
    if len(points) < 33:
        return None
    hips = _visible(points[L_HIP]) and _visible(points[R_HIP])
    knees = _visible(points[L_KNEE]) and _visible(points[R_KNEE])
    ankles = _visible(points[L_ANKLE]) and _visible(points[R_ANKLE])
    feet = _visible(points[L_FOOT], 0.3) and _visible(points[R_FOOT], 0.3)
    if hips and knees and ankles:
        visible = "full" if feet else "partial"
    elif hips and knees:
        visible = "partial"
    else:
        visible = "none"

    hip_width = _dist(points[L_HIP], points[R_HIP], width, height)
    if hip_width <= 1:
        return None
    knee_gap = _dist(points[L_KNEE], points[R_KNEE], width, height) / hip_width
    ankle_gap = _dist(points[L_ANKLE], points[R_ANKLE], width, height) / hip_width

    hip_order = math.copysign(1.0, points[L_HIP].x - points[R_HIP].x)
    ankle_order = math.copysign(1.0, points[L_ANKLE].x - points[R_ANKLE].x)
    crossed = hip_order != ankle_order
    raised = abs(points[L_ANKLE].y - points[R_ANKLE].y) > 0.08
    knee_angle = min(
        _angle(points[L_HIP], points[L_KNEE], points[L_ANKLE], width, height),
        _angle(points[R_HIP], points[R_KNEE], points[R_ANKLE], width, height),
    )
    return {
        "legs_visible": visible,
        "hip_width_px": round(hip_width, 2),
        "knee_gap_ratio": round(knee_gap, 4),
        "ankle_gap_ratio": round(ankle_gap, 4),
        "crossed": crossed,
        "one_leg_raised": raised,
        "min_knee_angle": round(knee_angle, 1),
    }


def main() -> int:
    args = parse_args()
    gold = json.loads((args.gold_dir / "gold-set.json").read_text(encoding="utf-8"))
    labels_path = args.labels_file or (args.gold_dir / "pilot-labels-40.json")
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    label_by_no = {row["gold_no"]: row for row in labels["rows"]}
    records = [r for r in gold["records"] if r["gold_no"] in label_by_no][: args.limit]
    if not MODEL_PATH.is_file():
        raise SystemExit(f"缺少 pose 模型：{MODEL_PATH}")

    landmarker = _landmarker()
    rows = []
    try:
        for index, record in enumerate(records, start=1):
            # 优先用原图：缩略图可能裁掉脚部，关键点会被外推出画面
            path = Path(settings.storage_root) / record["file_path"]
            if not path.is_file():
                path = Path(settings.storage_root) / (record.get("thumbnail_path") or record["file_path"])
            if not path.is_file():
                continue
            features = _extract(landmarker, path)
            label = label_by_no[record["gold_no"]]
            rows.append(
                {
                    "gold_no": record["gold_no"],
                    "gold_id": record["gold_id"],
                    "truth": bool(label["human_meaningful"]),
                    "features": features,
                }
            )
            if index % 10 == 0:
                print(f"  {index}/{len(records)}")
    finally:
        landmarker.close()

    report = {"model": str(MODEL_PATH), "sample_size": len(rows), "rows": rows}
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"sample_size": len(rows), "output": str(args.json)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
