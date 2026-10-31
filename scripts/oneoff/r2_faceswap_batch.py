"""R2 换脸增强：把 10 样本两段式结果的人脸换成素材主体的人脸（InsightFace inswapper）。

背景：R2 两段式（SDXL+ControlNet → Qwen 清洗）身份只有 ~0.9/5，Qwen 的参考图方式传不过身份。
本脚本用 InsightFace 做**后处理换脸**（不改生成链路，只替换人脸区域），验证身份能否救回来。

依赖（单独 venv，避免污染后端环境）：
    pip install insightface onnxruntime opencv-python-headless
    # 模型：det_10g.onnx / w600k_r50.onnx / inswapper_128.onnx
    # 默认放 D:/AI/ComfyUI/ComfyUI/models/insightface/buffalo_l/（--models-dir 可改）

用法：
    C:/Users/Administrator/AppData/Local/Temp/fsvenv/Scripts/python.exe \
        scripts/oneoff/r2_faceswap_batch.py \
        --batch-dir G:/fashion-inspo-storage/poc/r2_batch10_20261030 \
        --out-dir G:/fashion-inspo-storage/poc/r2_batch10_swap
产物：out_dir/s{idx}.png（换脸后） + swap_summary.json（状态/耗时）。
**许可提示**：inswapper 权重为 InsightFace 非商用模型，只用于个人本地实验，不得对外分发。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

import cv2
import numpy as np
from insightface.app.common import Face
from insightface.model_zoo import get_model

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_MODELS = Path(r"D:/AI/ComfyUI/ComfyUI/models/insightface/buffalo_l")
DEFAULT_STORAGE = Path(r"G:/fashion-inspo-storage")
DEFAULT_DB = ROOT / "backend" / "fashion_inspo.db"


class Swapper:
    """人脸检测 + 识别 + 换脸（只用 buffalo_l 里的 det/rec + inswapper）。"""

    def __init__(self, models_dir: Path) -> None:
        self.det = get_model(str(models_dir / "det_10g.onnx"))
        self.det.prepare(-1)
        self.rec = get_model(str(models_dir / "w600k_r50.onnx"))
        self.swap = get_model(str(models_dir / "inswapper_128.onnx"))

    def _faces(self, img: np.ndarray) -> list[Face]:
        bboxes, kpss = self.det.detect(img, input_size=(640, 640), max_num=0)
        faces: list[Face] = []
        if bboxes is None or len(bboxes) == 0:
            return faces
        for i in range(len(bboxes)):
            face = Face(bbox=bboxes[i].astype(np.float32), kps=kpss[i].astype(np.float32))
            self.rec.get(img, face)
            faces.append(face)
        return faces

    @staticmethod
    def _largest(faces: list[Face]) -> Face:
        def area(face: Face) -> float:
            x1, y1, x2, y2 = face.bbox[:4]
            return float((x2 - x1) * (y2 - y1))

        return max(faces, key=area)

    def swap_face(self, source: np.ndarray, target: np.ndarray) -> np.ndarray:
        src_faces = self._faces(source)
        tgt_faces = self._faces(target)
        if not src_faces:
            raise ValueError("素材图未检出人脸")
        if not tgt_faces:
            raise ValueError("目标图未检出人脸")
        return self.swap.get(
            target, self._largest(tgt_faces), self._largest(src_faces), paste_back=True
        )


def _subject_path(db: Path, subject_id: str, storage_root: Path) -> Path:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select file_path from inspirations where id=? and deleted_at is null",
            (subject_id,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        raise SystemExit(f"素材不存在：{subject_id}")
    return storage_root / str(row[0])


def main() -> None:
    parser = argparse.ArgumentParser(description="R2 换脸增强批处理")
    parser.add_argument("--batch-dir", required=True, help="两段式批量结果目录（含 summary.json）")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--models-dir", default=str(DEFAULT_MODELS))
    parser.add_argument("--storage-root", default=str(DEFAULT_STORAGE))
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()

    batch_dir = Path(args.batch_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = json.loads((batch_dir / "summary.json").read_text(encoding="utf-8"))
    db = Path(args.db)
    storage_root = Path(args.storage_root)
    swapper = Swapper(Path(args.models_dir))

    results = []
    for row in rows:
        idx = int(row["idx"])
        source_path = _subject_path(db, row["subject_id"], storage_root)
        target_path = batch_dir / row["stage2"]
        started = time.monotonic()
        try:
            source = cv2.imread(str(source_path))
            target = cv2.imread(str(target_path))
            if source is None or target is None:
                raise ValueError("读图失败")
            out = swapper.swap_face(source, target)
            target_out = out_dir / f"s{idx:02d}.png"
            cv2.imwrite(str(target_out), out)
            status = "ok"
        except Exception as exc:  # noqa: BLE001
            status = f"failed: {exc}"
            target_out = None
        elapsed = round(time.monotonic() - started, 2)
        results.append(
            {
                "idx": idx,
                "subject_id": row["subject_id"],
                "pose_id": row["pose_id"],
                "status": status,
                "elapsed_s": elapsed,
                "image": target_out.name if target_out else None,
            }
        )
        print(f"s{idx:02d}: {status}（{elapsed}s）")

    summary = out_dir / "swap_summary.json"
    summary.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"完成 {ok}/{len(results)}；summary: {summary}")


if __name__ == "__main__":
    main()
