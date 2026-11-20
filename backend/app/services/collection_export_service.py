"""合集导出服务（P2，2026-11-20）：CSV / Markdown / ZIP / PDF 四种格式。

设计要点：
- 只导出**手动/智能合集的未删除素材**（复用 ``collection_service.list_collection_inspirations``，
  垃圾桶自动排除）；单次最多 500 条，空合集 400、超限 400 提示分批；
- 生成物落在 ``storage_root/exports/<token>/``，token 用 ``secrets.token_urlsafe``；
  响应只暴露 token 与相对下载地址，不暴露绝对路径；下载接口只接受安全字符 token；
- 每个导出目录带 ``meta.json``（格式/文件名/计数/过期时间），生成新导出前清理超过 24h 的旧目录；
- CSV 用 UTF-8 BOM + ``sanitize_csv_cell``（防公式注入）；ZIP 打包原图/视频并附 manifest.csv
  与 README.md，缺失文件跳过计入 skipped；PDF 用 ReportLab 在**线程池**里生成（不阻塞事件循环），
  文字矢量可选中、原帖链接可点击；图片等比完整放入格子（不裁剪），封面含描述/封面图/本地导出时间。
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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageOps
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas

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

# PDF 页面：A4 点尺寸（矢量排版，图片保留原图分辨率，不再整页栅格化）
PDF_PAGE_W, PDF_PAGE_H = A4
PDF_MARGIN = 36
PDF_GAP = 16
PDF_CAPTION_H = 58
PDF_FOOTER_H = 20
PDF_COLS = 2
PDF_ROWS = 2
# PDF 内嵌图片的最长边：1600px 足够 2×2 画册在屏幕/普通打印下清晰，
# 同时避免 52 张大图把 PDF 撑到 80MB+（原图保留在 ZIP 导出里）
PDF_IMAGE_MAX_SIDE = 1600


@dataclass(frozen=True)
class ExportCollection:
    """导出所需的合集快照。

    为什么要快照：生成 PDF/ZIP 可能跑几分钟，期间必须结束只读事务、释放数据库连接
    （见 ``create_export``）。会话一旦回滚，ORM 对象属性就失效，所以先把标量字段拷出来。
    """

    id: int
    name: str
    is_smart: bool
    description: str | None = None
    # PDF 封面用：合集手动封面；没有则取第一条图片素材
    cover_relative_path: str | None = None
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


def _markdown_bytes(collection: ExportCollection, rows: list[dict]) -> bytes:
    """Markdown：合集元信息 + 条目列表（序号/作者/标签/原链接/本地路径）。"""
    kind = "智能合集" if collection.is_smart else "手动合集"
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


_PDF_FONT_NAME: str | None = None
_PDF_FONT_CANDIDATES = (
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


def _register_pdf_font() -> str:
    """注册中文 PDF 字体：优先系统 TTF，失败回退 ReportLab 内置 CJK CID 字体。

    不再用 Pillow 把整页栅格化：文字保持可选中/可搜索，图片按原图分辨率嵌入。
    """
    global _PDF_FONT_NAME
    if _PDF_FONT_NAME is not None:
        return _PDF_FONT_NAME
    for candidate in _PDF_FONT_CANDIDATES:
        try:
            if Path(candidate).is_file():
                pdfmetrics.registerFont(TTFont("CJK", candidate))
                _PDF_FONT_NAME = "CJK"
                return _PDF_FONT_NAME
        except (OSError, ValueError) as exc:
            logger.debug("PDF 字体加载失败，尝试下一个 %s：%s", candidate, exc)
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        _PDF_FONT_NAME = "STSong-Light"
    except (OSError, ValueError) as exc:
        logger.warning("PDF 中文字体注册失败，回退 Helvetica：%s", exc)
        _PDF_FONT_NAME = "Helvetica"
    return _PDF_FONT_NAME


def _text_width(text: str, font: str, size: float) -> float:
    return pdfmetrics.stringWidth(text, font, size)


def _ellipsize(text: str, font: str, size: float, max_width: float) -> str:
    """按实际像素宽度截断并补省略号，避免长标签/长作者名冲出单元格。"""
    if not text or _text_width(text, font, size) <= max_width:
        return text
    ellipsis = "…"
    body = text
    while body and _text_width(body + ellipsis, font, size) > max_width:
        body = body[:-1]
    return (body or "") + ellipsis


def _wrap_text(
    text: str, font: str, size: float, max_width: float, max_lines: int
) -> list[str]:
    """按像素宽度逐字换行，最多 max_lines 行；超出部分省略。"""
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and _text_width(candidate, font, size) > max_width:
            lines.append(current)
            current = char
            if len(lines) == max_lines:
                break
        else:
            current = candidate
    if len(lines) < max_lines and current:
        lines.append(current)
    if not lines:
        return []
    consumed = sum(len(line) for line in lines)
    if consumed < len(text) and lines:
        lines[-1] = _ellipsize(lines[-1] + "…", font, size, max_width)
    return lines


def _draw_wrapped(
    c: pdf_canvas.Canvas,
    text: str,
    x: float,
    y: float,
    max_width: float,
    font: str,
    size: float,
    leading: float,
    max_lines: int,
    color: str = "#4B4A47",
) -> float:
    """画多行文字，返回最后一行下方基线坐标。"""
    lines = _wrap_text(text or "", font, size, max_width, max_lines)
    c.setFont(font, size)
    c.setFillColor(HexColor(color))
    for line in lines:
        c.drawString(x, y, line)
        y -= leading
    return y


def _draw_pdf_image(
    c: pdf_canvas.Canvas,
    relative_path: str | None,
    x: float,
    y: float,
    width: float,
    height: float,
) -> bool:
    """把图片等比放入矩形（不裁剪），返回是否成功绘制。"""
    source = _resolve_storage_file(relative_path)
    if source is None or source.suffix.lower() not in _PDF_EXTENSIONS:
        return False
    try:
        with Image.open(source) as raw:
            image = ImageOps.exif_transpose(raw)
            if image.mode in ("RGBA", "LA"):
                background = Image.new("RGB", image.size, "white")
                background.paste(image, mask=image.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            # 限制最长边，兼顾清晰度与 PDF 体积；ZIP 仍保留原图
            image.thumbnail(
                (PDF_IMAGE_MAX_SIDE, PDF_IMAGE_MAX_SIDE),
                Image.Resampling.LANCZOS,
            )
            buffer = io.BytesIO()
            # 统一转高质量 JPEG，避开 ReportLab 对 webp/透明通道的兼容问题
            image.save(buffer, "JPEG", quality=88, optimize=True)
            buffer.seek(0)
            c.drawImage(
                ImageReader(buffer),
                x,
                y,
                width=width,
                height=height,
                preserveAspectRatio=True,
                anchor="c",
                mask="auto",
            )
        return True
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        logger.warning("PDF 导出跳过无法解码的图片 %s：%s", source, exc)
        return False


def _valid_source_url(url: str | None) -> str | None:
    if url and url.startswith(("http://", "https://")):
        return url
    return None


def _draw_cover(
    c: pdf_canvas.Canvas,
    collection: ExportCollection,
    rows: list[dict],
    font: str,
) -> None:
    """封面：标题 + 描述 + 封面图 + 本地时间/数量 + 底部版权说明。"""
    margin = 42
    has_image = bool(collection.cover_relative_path)
    text_width = 250 if has_image else 500
    x = margin

    c.setTitle(f"{collection.name} · 收藏画册")
    c.setAuthor("AI 穿搭素材库")
    c.setFillColor(HexColor("#FAF8F3"))
    c.rect(0, 0, PDF_PAGE_W, PDF_PAGE_H, fill=1, stroke=0)

    c.setFillColor(HexColor("#8A857C"))
    c.setFont(font, 9)
    c.drawString(x, 790, "LOOKBOOK / 收藏画册")
    c.setFillColor(HexColor("#1F1D1A"))
    c.setFont(font, 28)
    c.drawString(x, 748, _ellipsize(collection.name, font, 28, text_width))
    c.setStrokeColor(HexColor("#D8D2C6"))
    c.setLineWidth(0.8)
    c.line(x, 732, x + text_width, 732)

    description = collection.description or "用收藏整理出的风格样本。"
    _draw_wrapped(c, description, x, 706, text_width, font, 10.5, 16, 6)

    kind = "智能合集" if collection.is_smart else "手动合集"
    c.setFillColor(HexColor("#4B4A47"))
    c.setFont(font, 10)
    c.drawString(x, 380, kind)
    c.drawString(x, 358, f"共 {len(rows)} 个素材")
    c.drawString(x, 336, f"导出于 {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')}")
    c.setFillColor(HexColor("#8A857C"))
    c.setFont(font, 8.5)
    c.drawString(x, 310, "PDF 仅收录可读取的图片/封面，视频与缺失文件不嵌入")

    if has_image:
        c.setFillColor(HexColor("#F1EEE7"))
        c.roundRect(320, 290, 230, 360, 8, fill=1, stroke=0)
        _draw_pdf_image(c, collection.cover_relative_path, 330, 300, 210, 340)

    c.setFillColor(HexColor("#8A857C"))
    c.setFont(font, 8)
    c.drawString(x, 58, "由 AI 穿搭素材库导出 · 仅供个人归档，素材版权归原作者")


def _draw_caption(
    c: pdf_canvas.Canvas,
    row: dict,
    x: float,
    cell_y: float,
    cell_w: float,
    font: str,
) -> None:
    """单元格底部：编号/作者 + 最多两行标签 + 可点击原帖。"""
    title = f"{row['序号']}. {row['作者'] or row['文件名'] or row['素材ID']}"
    url = _valid_source_url(row.get("原链接"))
    title_width = cell_w - 12 - (24 if url else 0)
    title = _ellipsize(title, font, 9.5, title_width)
    baseline = cell_y + PDF_CAPTION_H - 13
    c.setFont(font, 9.5)
    c.setFillColor(HexColor("#1F1D1A"))
    c.drawString(x + 6, baseline, title)

    if url:
        link_text = "原帖"
        link_font_size = 7.5
        link_w = _text_width(link_text, font, link_font_size)
        link_x = x + cell_w - 8 - link_w
        c.setFont(font, link_font_size)
        c.setFillColor(HexColor("#2A6FDB"))
        c.drawString(link_x, baseline, link_text)
        c.linkURL(url, (link_x, baseline - 2, link_x + link_w, baseline + 9), relative=0)

    tags = [part.strip() for part in (row.get("标签") or "").split("、") if part.strip()]
    tag_text = " · ".join(tags[:5])
    if len(tags) > 5:
        tag_text += "…"
    tag_lines = _wrap_text(tag_text, font, 7.5, cell_w - 12, 2)
    c.setFont(font, 7.5)
    c.setFillColor(HexColor("#6D6A63"))
    tag_y = baseline - 13
    for line in tag_lines:
        c.drawString(x + 6, tag_y, line)
        tag_y -= 9.5


def _pdf_bytes(collection: ExportCollection, rows: list[dict]) -> tuple[bytes, int, int]:
    """ReportLab 生成矢量 PDF：封面 + 每页 2×2 网格，图片不裁剪。

    返回 (PDF 字节, 成功嵌入图片数, 跳过数)。图片逐张打开并立即绘制，
    不把整集合图片同时留在内存里；文字可选中/搜索，原帖链接可点击。
    """
    font = _register_pdf_font()
    buffer = io.BytesIO()
    c = pdf_canvas.Canvas(buffer, pagesize=A4, pageCompression=1)
    _draw_cover(c, collection, rows, font)

    content_top = PDF_PAGE_H - PDF_MARGIN
    content_bottom = PDF_MARGIN + PDF_FOOTER_H
    cell_w = (PDF_PAGE_W - 2 * PDF_MARGIN - PDF_GAP) / PDF_COLS
    cell_h = (content_top - content_bottom - PDF_GAP) / PDF_ROWS
    image_y_offset = PDF_CAPTION_H + 4
    image_h = cell_h - PDF_CAPTION_H - 8

    embedded = 0
    skipped = 0
    page_no = 0
    total_pages = 1 + (len(rows) + PDF_COLS * PDF_ROWS - 1) // (PDF_COLS * PDF_ROWS)
    for start in range(0, len(rows), PDF_COLS * PDF_ROWS):
        c.showPage()
        page_no += 1
        batch = rows[start : start + PDF_COLS * PDF_ROWS]
        for offset, row in enumerate(batch):
            column = offset % PDF_COLS
            line = offset // PDF_COLS
            x = PDF_MARGIN + column * (cell_w + PDF_GAP)
            cell_y = content_top - (line + 1) * cell_h - line * PDF_GAP
            c.setFillColor(HexColor("#F6F4EE"))
            c.roundRect(x, cell_y, cell_w, cell_h, 5, fill=1, stroke=0)
            if _draw_pdf_image(
                c,
                row.get("相对路径"),
                x + 4,
                cell_y + image_y_offset,
                cell_w - 8,
                image_h,
            ):
                embedded += 1
            else:
                skipped += 1
            _draw_caption(c, row, x, cell_y, cell_w, font)
        c.setFillColor(HexColor("#8A857C"))
        c.setFont(font, 8)
        c.drawCentredString(PDF_PAGE_W / 2, 16, f"第 {page_no} / {total_pages} 页")

    c.save()
    return buffer.getvalue(), embedded, skipped


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
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                # TypeError：meta 里 expires_at 不是字符串（None/数字）时 fromisoformat 会抛；
                # 不兜住会让清理函数每次生成导出都 500
                expires_at = None
        if expires_at is None:
            # 没有 meta（中断的残留）按目录修改时间判断。
            # mtime 用 UTC 口径取，才能和 utcnow() 比较（本地时区会差 8 小时）
            try:
                expires_at = datetime.fromtimestamp(
                    entry.stat().st_mtime, tz=UTC
                ).replace(tzinfo=None) + timedelta(hours=EXPORT_TTL_HOURS)
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


def _display_filename(collection: ExportCollection, fmt: str) -> str:
    """展示用文件名（保留合集名，中文可用；下载头另用英文安全名）。"""
    date = utcnow().strftime("%Y%m%d")
    return f"{_sanitize_name(collection.name, f'collection-{collection.id}')}_{date}.{FORMAT_EXT[fmt]}"


def _download_filename(collection: ExportCollection, fmt: str) -> str:
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

    # 快照合集字段并结束只读事务、释放数据库连接：下面进入几十秒到 10 分钟的文件生成
    # （PDF 尤其慢），一直占着连接会让并发导出/其它请求排队。回滚后 ORM 对象失效，
    # 所以上面必须先把要用的标量字段拷进快照。
    cover_relative_path = None
    if collection.cover_inspiration_id:
        cover_relative_path = next(
            (
                item.file_path
                for item in items
                if item.id == collection.cover_inspiration_id and item.media_type == "image"
            ),
            None,
        )
    if cover_relative_path is None:
        cover_relative_path = next(
            (item.file_path for item in items if item.media_type == "image"),
            rows[0]["相对路径"] if rows else None,
        )
    snapshot = ExportCollection(
        id=collection.id,
        name=collection.name,
        is_smart=collection.query_json is not None,
        description=collection.description,
        cover_relative_path=cover_relative_path,
    )
    await db.rollback()

    token = secrets.token_urlsafe(24)
    export_dir = exports_root() / token
    stored_name = f"export.{FORMAT_EXT[export_format]}"
    try:
        export_dir.mkdir(parents=True, exist_ok=False)
        manifest_csv = _csv_bytes(rows)
        readme_md = _markdown_bytes(snapshot, rows)
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
                _pdf_bytes, snapshot, rows
            )
        await asyncio.to_thread((export_dir / stored_name).write_bytes, content)

        expires_at = utcnow() + timedelta(hours=EXPORT_TTL_HOURS)
        meta = {
            "collection_id": snapshot.id,
            "collection_name": snapshot.name,
            "format": export_format,
            "stored_file": stored_name,
            "filename": _display_filename(snapshot, export_format),
            "download_filename": _download_filename(snapshot, export_format),
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
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise HTTPException(
            status_code=404, detail="导出文件不存在或已过期"
        ) from None
    if utcnow() >= expires_at:
        shutil.rmtree(export_dir, ignore_errors=True)
        raise HTTPException(status_code=404, detail="导出文件不存在或已过期")
    # stored_name 只允许单层文件名（meta 正常由本服务写入；仍按安全字符校验）。
    # 先校验类型：meta 被写坏时 stored_file 可能是数字/None，直接 in 会抛 TypeError
    if (
        not isinstance(stored_name, str)
        or "/" in stored_name
        or "\\" in stored_name
        or stored_name.startswith(".")
    ):
        raise HTTPException(status_code=404, detail="导出文件不存在或已过期")
    file_path = (export_dir / stored_name).resolve()
    try:
        file_path.relative_to(export_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="导出文件不存在或已过期") from None
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在或已过期")
    return file_path, meta
