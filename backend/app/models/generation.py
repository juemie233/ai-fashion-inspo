"""本地出图（ComfyUI）批次模型。

设计要点（见 docs/ComfyUI出图接入方案.md 第 4 节）：

- **参数与批次进表，图片文件落目录**：DB 不存二进制；候选区的每张图用文件名编码
  seed（`001_seed-123456.png`），因此不需要 per-image 表也能复现。
- `workflow_hash` 记录模板文件指纹：ComfyUI 升级导致节点改名时，能判断「这批图是
  用哪个版本的模板生成的」，而不是只能靠时间猜。
- 删除 run **不级联删素材**：`inspirations.generation_run_id` 是 `ON DELETE SET NULL`，
  用户清空生成历史时，已入库的图仍保留（它们已是独立素材）。
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.utils.time import utcnow

"""批次状态取值（与前端状态机一一对应）。"""
GENERATION_STATUSES = ("queued", "running", "waiting_vram", "done", "failed", "cancelled")

"""生成方式。"""
GENERATION_KINDS = ("t2i", "i2i")


class GenerationRun(Base):
    """一次「出图批次」：同一组参数、可能产出多张图。"""

    __tablename__ = "generation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), default="t2i")  # t2i | i2i

    # queued（已建任务，等 worker 认领）→ running → waiting_vram（等显存，可反复）
    # → done / failed / cancelled
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)

    prompt: Mapped[str] = mapped_column(Text)
    negative: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 尺寸/步数/cfg/sampler 等原始请求参数快照（JSON 字符串），用于「同参再生成」
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    model_key: Mapped[str] = mapped_column(String(32), default="z_image_int8")
    workflow_name: Mapped[str] = mapped_column(String(64), default="z_image_t2i")
    workflow_hash: Mapped[str] = mapped_column(String(16), default="")

    # i2i 的参考素材 id 列表（JSON 数组字符串）
    reference_ids: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 候选图目录（相对 storage_root，如 generated/12）
    batch_dir: Mapped[str] = mapped_column(String(255), default="")

    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 关联任务队列 id（任务记录被清理后置空的场景由 GET 侧容忍）
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<GenerationRun(id={self.id}, model={self.model_key}, "
            f"status={self.status}, {self.done}/{self.total})>"
        )
