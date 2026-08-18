"""穿搭博主 CSV 导入（按 xhs_id upsert）。

博主专属能力：模特无小红书号导入入口。逻辑从 BloggerService 独立成模块，
保持服务类精简。
"""

from __future__ import annotations

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.person import Blogger

# CSV 导入错误明细上限：避免超大文件撑爆响应体
_IMPORT_ERROR_LIMIT = 100


async def import_bloggers_csv(db: AsyncSession, file: UploadFile) -> dict:
    """从 CSV 批量导入博主（按 xhs_id upsert），返回导入统计。

    CSV 要求:
        - 编码 UTF-8（自动去除 BOM）
        - 表头含 ``nickname`` 与 ``xhs_id``（必填），``ip_location`` 可选；
          列顺序不限，按表头名称匹配（大小写/首尾空白容错）
        - nickname 与 xhs_id 非空，ip_location 可为空
        - xhs_id 已存在 → 更新昵称与 IP 属地（upsert，避免重复导入）
        - CSV 文件内重复的 xhs_id 合并为一行（后出现者覆盖，计入 skipped）

    返回:
        {"imported": 新增数, "updated": 更新数, "skipped": 跳过数,
         "failed": 失败行数, "errors": [{"row", "nickname", "reason"}, ...]}
    """
    import csv
    import io

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")  # utf-8-sig 自动去除 BOM
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400, detail="文件编码不是 UTF-8，请转换为 UTF-8 后重试"
        )

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV 文件为空或缺少表头")

    # 表头名规范化：去首尾空白 + 小写，兼容 nickname/NickName/昵称等变体
    header_map: dict[str, str] = {}
    for h in reader.fieldnames:
        if h is None:
            continue
        key = h.strip().lower()
        if key and key not in header_map:
            header_map[key] = h.strip()
    for required in ("nickname", "xhs_id"):
        if required not in header_map:
            raise HTTPException(status_code=400, detail=f"CSV 缺少必填列：{required}")

    errors: list[dict] = []
    # 合法行按 xhs_id 合并（CSV 内重复 → 后出现者覆盖昵称/IP）
    merged: dict[str, dict] = {}
    duplicate_in_file = 0
    row_no = 0  # 数据行号（表头为第 0 行，数据从 1 起）
    for row in reader:
        row_no += 1
        # 行键规范化：DictReader 的键是原始表头（不自动 strip），
        # 表头带首尾空白时直接 row.get("nickname") 会恒为 None——
        # 统一按 strip+lower 后的键读取，兑现「首尾空白容错」声明
        norm_row = {
            (k.strip().lower() if k else ""): v for k, v in row.items()
        }
        nickname = (norm_row.get("nickname") or "").strip()
        xhs_id = (norm_row.get("xhs_id") or "").strip()
        ip_location = (norm_row.get("ip_location") or "").strip()

        if not nickname:
            errors.append({"row": row_no, "nickname": None, "reason": "昵称为空"})
            continue
        if not xhs_id:
            errors.append({"row": row_no, "nickname": nickname, "reason": "小红书号为空"})
            continue
        if len(xhs_id) > 64:
            errors.append(
                {"row": row_no, "nickname": nickname, "reason": "小红书号超过 64 字符"}
            )
            continue

        if xhs_id in merged:
            duplicate_in_file += 1  # CSV 内重复：保留后出现者
        merged[xhs_id] = {
            "nickname": nickname,
            "ip_location": ip_location,
            "row": row_no,
        }

    # 批量查库：一次取出所有已存在的 xhs_id，避免逐行查询（N+1）
    existing_result = await db.execute(
        select(Blogger).where(Blogger.xhs_id.in_(list(merged.keys())))
    )
    existing_map = {p.xhs_id: p for p in existing_result.scalars().all()}

    imported = 0
    updated = 0
    for xhs_id, entry in merged.items():
        person = existing_map.get(xhs_id)
        new_person: Blogger | None = None
        try:
            if person:
                # upsert：更新昵称与 IP 属地（小红书号本身不变）
                person.name = entry["nickname"]
                person.ip_location = entry["ip_location"] or None
                updated += 1
            else:
                new_person = Blogger(
                    name=entry["nickname"],
                    platform="xiaohongshu",
                    xhs_id=xhs_id,
                    ip_location=entry["ip_location"] or None,
                    source="manual",
                )
                db.add(new_person)
                imported += 1
            async with db.begin_nested():
                await db.flush()
        except IntegrityError:
            # SAVEPOINT 已回滚：新建对象仍在 pending，移除避免后续 commit 重复插入
            if new_person is not None:
                db.expunge(new_person)
            # 并发下同一 xhs_id 已被其它请求插入：重查后按「更新」处理
            retry = (
                await db.execute(select(Blogger).where(Blogger.xhs_id == xhs_id))
            ).scalar_one_or_none()
            if retry:
                retry.name = entry["nickname"]
                retry.ip_location = entry["ip_location"] or None
                if imported > 0:
                    imported -= 1
                updated += 1
            else:
                errors.append(
                    {
                        "row": entry["row"],
                        "nickname": entry["nickname"],
                        "reason": "导入冲突",
                    }
                )

    await db.commit()

    return {
        "imported": imported,
        "updated": updated,
        "skipped": duplicate_in_file,
        "failed": len(errors),
        "errors": errors[:_IMPORT_ERROR_LIMIT],
    }
