"""合集导出服务（P2，2026-11-20）：CSV / Markdown / ZIP / PDF 四种格式。

设计要点：
- 只导出**手动/智能合集的未删除素材**（复用 ``collection_service.list_collection_inspirations``，
  垃圾桶自动排除）；单次最多 500 条，空合集 400、超限 400 提示分批；
- 生成物落在 ``storage_root/exports/<token>/``，token 用 ``secrets.token_urlsafe``；
  响应只暴露 token 与相对下载地址，不暴露绝对路径；下载接口只接受安全字符 token；
- 每个导出目录带 ``meta.json``（格式/文件名/计数/过期时间），生成新导出前清理超过 24h 的旧目录；
- CSV 用 UTF-8 BOM + ``sanitize_csv_cell``（防公式注入）；ZIP 打包原图/视频并附 manifest.csv
  与 README.md，缺失文件跳过计入 skipped；PDF 用 Pillow 在**线程池**里生成（不阻塞事件循环），
  中文字体按 Windows/macOS/Linux 回退，找不到字体也不崩溃（仅图片/英文回退）。
"""

import asyncio
import csv
import io
import json
import logging
import re
import secrets
import shutil
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.config import settings
from app.services import collection_service
from app.utils.csv_safety import sanitize_csv_cell
from app.utils.time import format_utc, utcnow

logger = logging.getLogger(__name__)

# 单次导出上限（超过提示分批）与导出文件保留时长
MAX_EXPORT_ITEMS = 500
EXPORT_TTL_HOURS = 24

# 格式 → 文件扩展名 / Content-Type / 展示名后缀
FORMAT_EXT = {"pdf": "pdf", "zip": "zip", "csv": "csv", "markdown": "md"}
FORMAT_MEDIA_TYPE = {
    "pdf": "application/pdf",
    "zip": "application/zip",
    "csv": "text/csv; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
}
# token 白名单：token_urlsafe 只产生这些字符；校验同时防路径穿越
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
# 文件名不安全字符（保留中文，去掉路径分隔符/控制字符等）
_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|\r\n\t\x00-\x1f]+')

# CSV 列契约（manifest.csv 同列）
CSV_COLUMNS = [
    "序号",
    "素材ID",
    "文件名",
    "媒体类型",
    "来源",
    "作者",
    "收藏时间",
    "评分",
    "标签",
    "原链接",
    "相对路径",
]

# PDF 页面：A4 @96dpi（约 72~100dpi 区间内），2×2 网格
PDF_PAGE_W, PDF_PAGE_H = 794, 1123
PDF_MARGIN = 36
PDF_GAP = 16
PDF_CAPTION_H = 64
_PDF_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
)
_PDF_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}


def exports_root() -> Path:
    """导出目录根（storage_root/exports；测试可整体替换 storage_root）。"""
    return settings.storage_root / "exports"


def _sanitize_name(name: str | None, fallback: str = "collection") -> str:
    """文件名清洗：去掉路径分隔符/控制字符，保留中文，限制长度。"""
    cleaned = _UNSAFE_FILENAME_RE.sub("_", (name or "").strip())
    cleaned = cleaned.strip(" ._")
    return cleaned[:60] or fallback


def _resolve_storage_file(relative_path: str | None) -> Path | None:
    """把素材相对路径安全解析到 storage_root 内；越界/缺失/非文件返回 None。"""
    if not relative_path:
        return None
    try:
        root = settings.storage_root.resolve()
        full_path = (root / relative_path).resolve()
        if not full_path.is_relative_to(root):
            return None
        if not full_path.is_file():
            return None
    except (OSError, ValueError) as exc:
        logger.debug("导出跳过非法/无法解析的素材路径 %r：%s", relative_path, exc)
        return None
    return full_path


def _tags_text(item) -> str:
    """素材标签名（顿号连接）；关联未加载/空时返回空串。"""
    names = []
    for link in getattr(item, "tags", []) or []:
        tag = getattr(link, "tag", None)
        if tag is not None and tag.name:
            names.append(tag.name)
    return "、".join(names)


def _build_rows(items) -> list[dict]:
    """把 ORM 素材转成导出用的行数据（CSV / Markdown / ZIP manifest 共用）。"""
    rows = []
    for index, item in enumerate(items, start=1):
        rows.append(
            {
                "序号": index,
                "素材ID": item.id,
                "文件名": Path(item.file_path or "").name,
                "媒体类型": item.media_type or "",
                "来源": item.source_type or "",
                "作者": item.source_author or "",
                "收藏时间": format_utc(item.favorited_at) or "",
                "评分": item.rating if item.rating is not None else "",
                "标签": _tags_text(item),
                "原链接": item.source_url or "",
                "相对路径": item.file_path or "",
            }
        )
    return rows


def _csv_bytes(rows: list[dict]) -> bytes:
    """CSV：UTF-8 BOM + 列契约；所有单元格过 sanitize_csv_cell（防公式注入）。"""
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow([sanitize_csv_cell(row.get(column)) for column in CSV_COLUMNS])
    return buffer.getvalue().encode("utf-8")


def _markdown_bytes(collection, rows: list[dict]) -> bytes:
    """Markdown：合集元信息 + 条目列表（序号/作者/标签/原链接/本地路径）。"""
    kind = "智能合集" if collection.query_json else "手动合集"
    lines = [
        f"# {collection.name}",
        "",
        "## 合集信息",
        "",
        f"- 合集类型：{kind}",
        f"- 导出时间：{format_utc(utcnow())}",
        f"- 条目数：{len(rows)}",
        "",
        "## 条目",
        "",
    ]
    for row in rows:
        title = row["作者"] or row["文件名"] or row["素材ID"]
        lines.append(f"{row['序号']}. **{title}**")
        if row["标签"]:
            lines.append(f"   - 标签：{row['标签']}")
        if row["原链接"]:
            lines.append(f"   - 原链接：{row['原链接']}")
        if row["相对路径"]:
            lines.append(f"   - 本地路径：`{row['相对路径']}`")
        lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _zip_bytes(rows: list[dict], manifest_csv: bytes, readme_md: bytes) -> tuple[bytes, int, int]:
    """ZIP：原图/视频按 ``0001_<safe>.<ext>`` 打包 + manifest.csv + README.md。

    缺失/越界/非文件素材跳过并计入 skipped；返回 (zip 字节, 成功条数, 跳过条数)。
    """
    added = 0
    skipped = 0
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.csv", manifest_csv)
        archive.writestr("README.md", readme_md)
        for row in rows:
            source = _resolve_storage_file(row["相对路径"])
            if source is None:
                skipped += 1
                continue
            extension = source.suffix.lower() or ".bin"
            stem = _sanitize_name(
                row["作者"] or Path(row["文件名"]).stem or row["素材ID"],
                fallback=row["素材ID"],
            )[:40]
            archive_name = f"{row['序号']:04d}_{stem}{extension}"
            archive.write(source, archive_name)
            added += 1
    return buffer.getvalue(), added, skipped


def _load_pdf_font(size: int):
    """加载中文字体；全部候选不存在/失败时回退 Pillow 默认字体（不崩溃）。"""
    for candidate in _PDF_FONT_CANDIDATES:
        try:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, size)
        except OSError as exc:
            logger.debug("PDF 字体加载失败，尝试下一个 %s：%s", candidate, exc)
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # 老版本 Pillow 没有 size 参数
        logger.debug("当前 Pillow 不支持 load_default(size=)，回退无字号默认字体")
        return ImageFont.load_default()


def _draw_text(draw: ImageDraw.ImageDraw, xy: tuple, text: str, font, fill="black") -> None:
    """画文字；字体不支持中文/编码异常时降级为 ASCII 或忽略（PDF 不能整体失败）。"""
    if not text:
        return
    try:
        draw.text(xy, text, font=font, fill=fill)
    except (UnicodeEncodeError, OSError):
        safe = text.encode("ascii", "ignore").decode("ascii")
        if not safe:
            return
        try:
            draw.text(xy, safe, font=font, fill=fill)
        except (UnicodeEncodeError, OSError):
            logger.debug("PDF 文字绘制失败，已跳过：%r", text[:30])


def _truncate(text: str, limit: int) -> str:
    """按字符数截断（避免中文字体宽度导致越界；不追求精确像素）。"""
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _pdf_bytes(collection, rows: list[dict]) -> tuple[bytes, int, int]:
    """Pillow 生成 PDF：封面 + 每页 2×2 网格（图片等比裁剪并编号/作者/标签）。

    在调用方线程池里执行（读图/缩放/编码是阻塞 CPU/IO）。
    返回 (pdf 字节, 成功嵌入图片数, 跳过数)。
    """
    title_font = _load_pdf_font(46)
    meta_font = _load_pdf_font(22)
    caption_font = _load_pdf_font(18)
    small_font = _load_pdf_font(16)

    # 1) 先按顺序加载可嵌入的图片（视频/缺失/损坏 → skipped）
    loaded: list[tuple[dict, Image.Image]] = []
    skipped = 0
    for row in rows:
        source = _resolve_storage_file(row["相对路径"])
        if source is None or source.suffix.lower() not in _PDF_EXTENSIONS:
            skipped += 1
            continue
        try:
            with Image.open(source) as raw:
                loaded.append((row, raw.convert("RGB").copy()))
        except (OSError, ValueError) as exc:
            logger.warning("PDF 导出跳过无法解码的图片 %s：%s", source, exc)
            skipped += 1

    pages: list[Image.Image] = []

    # 2) 封面：标题 + 元信息 + 数量（无可用图片也出封面，保证格式合法）
    cover = Image.new("RGB", (PDF_PAGE_W, PDF_PAGE_H), "white")
    draw = ImageDraw.Draw(cover)
    _draw_text(draw, (PDF_MARGIN + 8, 220), _truncate(collection.name, 24), title_font)
    kind = "智能合集" if collection.query_json else "手动合集"
    _draw_text(draw, (PDF_MARGIN + 8, 300), f"{kind} · 导出时间 {format_utc(utcnow())}", meta_font)
    _draw_text(draw, (PDF_MARGIN + 8, 340), f"共 {len(loaded)} 张图片 · {len(rows)} 条素材", meta_font)
    _draw_text(
        draw,
        (PDF_MARGIN + 8, PDF_PAGE_H - 80),
        "由 AI 穿搭素材库导出",
        small_font,
    )
    pages.append(cover)

    # 3) 2×2 网格页：图片等比裁剪 + 下方编号/作者/标签
    columns = 2
    cell_w = (PDF_PAGE_W - 2 * PDF_MARGIN - PDF_GAP) // columns
    cell_h = (PDF_PAGE_H - 2 * PDF_MARGIN - PDF_GAP) // 2
    image_w = cell_w
    image_h = cell_h - PDF_CAPTION_H
    for start in range(0, len(loaded), 4):
        page = Image.new("RGB", (PDF_PAGE_W, PDF_PAGE_H), "white")
        page_draw = ImageDraw.Draw(page)
        for offset, (row, image) in enumerate(loaded[start : start + 4]):
            column = offset % columns
            line = offset // columns
            x = PDF_MARGIN + column * (cell_w + PDF_GAP)
            y = PDF_MARGIN + line * (cell_h + PDF_GAP)
            fitted = ImageOps.fit(image, (image_w, image_h), centering=(0.5, 0.5))
            page.paste(fitted, (x, y))
            _draw_text(
                page_draw,
                (x, y + image_h + 4),
                _truncate(f"{row['序号']}. {row['作者'] or row['文件名'] or row['素材ID']}", 26),
                caption_font,
            )
            _draw_text(
                page_draw,
                (x, y + image_h + 30),
                _truncate(row["标签"] or "", 30),
                small_font,
                fill="#666666",
            )
        pages.append(page)

    buffer = io.BytesIO()
    pages[0].save(
        buffer,
        "PDF",
        resolution=96.0,
        save_all=True,
        append_images=pages[1:],
    )
    for _, image in loaded:
        image.close()
    return buffer.getvalue(), len(loaded), skipped


def _cleanup_expired_exports() -> int:
    """清理超过保留期的旧导出目录；返回删除数量（生成新导出前调用）。"""
    root = exports_root()
    if not root.exists():
        return 0
    removed = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        expires_at = None
        meta_path = entry / "meta.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                expires_at = datetime.fromisoformat(meta["expires_at"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                expires_at = None
        if expires_at is None:
            # 没有 meta（中断的残留）按目录修改时间判断
            try:
                expires_at = datetime.fromtimestamp(entry.stat().st_mtime) + timedelta(
                    hours=EXPORT_TTL_HOURS
                )
            except OSError as exc:
                logger.debug("导出目录 mtime 读取失败，跳过清理 %s：%s", entry, exc)
                continue
        if utcnow() >= expires_at:
            try:
                shutil.rmtree(entry)
                removed += 1
            except OSError as exc:
                logger.warning("清理过期导出失败 %s：%s", entry, exc)
    return removed


def _display_filename(collection, fmt: str) -> str:
    """展示用文件名（保留合集名，中文可用；下载头另用英文安全名）。"""
    date = utcnow().strftime("%Y%m%d")
    return f"{_sanitize_name(collection.name, f'collection-{collection.id}')}_{date}.{FORMAT_EXT[fmt]}"


def _download_filename(collection, fmt: str) -> str:
    """Content-Disposition 用安全英文文件名（不含中文/空格）。"""
    date = utcnow().strftime("%Y%m%d")
    return f"collection-{collection.id}-{fmt}-{date}.{FORMAT_EXT[fmt]}"


async def create_export(db, collection_id: int, export_format: str) -> dict:
    """生成合集导出文件，返回 token / 下载地址 / 计数 / 过期时间。

    Raises:
        HTTPException: 404 合集不存在；400 格式不支持/空合集/超过 500 条；
                       500 生成失败（文件已清理）。
    """
    if export_format not in FORMAT_EXT:
        raise HTTPException(status_code=400, detail=f"不支持的导出格式：{export_format}")

    # 1) 清理过期导出（懒清理；不用后台任务，避免增加调度复杂度）
    await asyncio.to_thread(_cleanup_expired_exports)

    collection = await collection_service.get_collection(db, collection_id)
    # 2) 取内容：手动按 position / 智能按 newest；垃圾桶已由内容查询排除
    items, total = await collection_service.list_collection_inspirations(
        db,
        collection_id,
        page=1,
        size=MAX_EXPORT_ITEMS + 1,
    )
    if total == 0:
        raise HTTPException(status_code=400, detail="合集为空，无法导出")
    if total > MAX_EXPORT_ITEMS:
        raise HTTPException(
            status_code=400,
            detail=f"合集共 {total} 条素材，超过单次导出上限 {MAX_EXPORT_ITEMS} 条，请分批导出",
        )
    items = items[:MAX_EXPORT_ITEMS]
    rows = _build_rows(items)

    token = secrets.token_urlsafe(24)
    export_dir = exports_root() / token
    stored_name = f"export.{FORMAT_EXT[export_format]}"
    try:
        export_dir.mkdir(parents=True, exist_ok=False)
        manifest_csv = _csv_bytes(rows)
        readme_md = _markdown_bytes(collection, rows)
        if export_format == "csv":
            content = manifest_csv
            item_count, skipped = len(rows), 0
        elif export_format == "markdown":
            content = readme_md
            item_count, skipped = len(rows), 0
        elif export_format == "zip":
            content, item_count, skipped = await asyncio.to_thread(
                _zip_bytes, rows, manifest_csv, readme_md
            )
        else:  # pdf
            content, item_count, skipped = await asyncio.to_thread(
                _pdf_bytes, collection, rows
            )
        await asyncio.to_thread((export_dir / stored_name).write_bytes, content)

        expires_at = utcnow() + timedelta(hours=EXPORT_TTL_HOURS)
        meta = {
            "collection_id": collection.id,
            "collection_name": collection.name,
            "format": export_format,
            "stored_file": stored_name,
            "filename": _display_filename(collection, export_format),
            "download_filename": _download_filename(collection, export_format),
            "item_count": item_count,
            "skipped_count": skipped,
            "size_bytes": len(content),
            "created_at": utcnow().isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        await asyncio.to_thread(
            (export_dir / "meta.json").write_text,
            json.dumps(meta, ensure_ascii=False),
            "utf-8",
        )
    except HTTPException:
        raise
    except Exception as exc:  # 生成/落盘失败：清理半成品，不把内部路径抛给前端
        shutil.rmtree(export_dir, ignore_errors=True)
        logger.warning("导出生成失败：collection=%s format=%s", collection_id, export_format, exc_info=exc)
        raise HTTPException(status_code=500, detail="导出生成失败，请稍后重试") from exc

    return {
        "token": token,
        "filename": meta["filename"],
        "format": export_format,
        "item_count": item_count,
        "skipped_count": skipped,
        "size_bytes": len(content),
        "download_url": f"/api/exports/{token}",
        "expires_at": format_utc(expires_at),
    }


def get_export_file(token: str) -> tuple[Path, dict]:
    """按 token 取导出文件；非法 token / 不存在 / 已过期一律 404。"""
    if not TOKEN_RE.fullmatch(token or ""):
        raise HTTPException(status_code=404, detail="导出文件不存在或已过期")
    export_dir = exports_root() / token
    meta_path = export_dir / "meta.json"
    if not meta_path.is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在或已过期")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        expires_at = datetime.fromisoformat(meta["expires_at"])
        stored_name = meta["stored_file"]
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        raise HTTPException(
            status_code=404, detail="导出文件不存在或已过期"
        ) from None
    if utcnow() >= expires_at:
        shutil.rmtree(export_dir, ignore_errors=True)
        raise HTTPException(status_code=404, detail="导出文件不存在或已过期")
    # stored_name 只允许单层文件名（meta 正常由本服务写入；仍按安全字符校验）
    if "/" in stored_name or "\\" in stored_name or stored_name.startswith("."):
        raise HTTPException(status_code=404, detail="导出文件不存在或已过期")
    file_path = (export_dir / stored_name).resolve()
    try:
        file_path.relative_to(export_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="导出文件不存在或已过期") from None
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在或已过期")
    return file_path, meta
