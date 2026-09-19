"""f2 历史素材「真实作品 ID」对齐覆盖率报告（**只读**，绝不写库）。

背景
----
历史 f2 素材的 `source_platform_id` 是「文件名算出来的合成哈希」、`source_url`
留空，一条都点不回抖音原帖。文件名里带着「发布时间 + 描述」，而抖音的作品清单
接口能给出同一对信息，于是可以按 `(发布时间, 描述)` 做 join 找回真实作品 ID。

日志这条路已被证伪（见 TODO.md：52 个日志里 `[aweme_id]` 行与 `[完成]：文件名`
行**分行输出且并发下载**，按顺序分组严重串味——2,703 个 ID 行对到 3,588 个磁盘
作品，21.9% 多义）。本脚本走「**只枚举作品清单、不下载媒体**」这条路。

为什么要先出报告
----------------
对齐必然存在未命中（f2 写文件名时会中段截断长描述）与多义（同作者同描述）。
按 TODO 的验收标准：**多义与未命中一律不写库**，先出三档计数与命中明细交人工
确认覆盖率。落库是另一个动作，本脚本不含任何写库代码。

用法
----
    cd backend
    python -m scripts.report_f2_id_alignment --limit 1          # 先跑 1 个作者试算
    python -m scripts.report_f2_id_alignment --output report.json
    python -m scripts.report_f2_id_alignment --authors 不养羊    # 指定作者
    python -m scripts.report_f2_id_alignment --auto-cookie chrome  # 从浏览器读 Cookie
    python -m scripts.report_f2_id_alignment --disk-only        # 不发请求：只看磁盘侧风险画像

不在联网也能得结论的部分
------------------------
`--disk-only` 会输出**磁盘侧对齐风险画像**：同作者内主干重复的作品数（必然落多义
档）、时间戳为 `00-00-00` 的作品数（接口给真实时间则必然未命中）等。这些与接口
返回什么无关，可先据此判断对齐键够不够用。

Cookie 与 f2 版本（两个坑，先读再跑）
-------------------------------------
1. **必须用 f2 工作目录里那份 f2**（项目靠 `cwd=f2_dir` 跑 `python -m f2` 用的就是
   它）。PyPI 上的 f2 最新只到 `0.0.1.7`（2024-12-31），其抖音签名已失效——用旧版
   调作品清单接口**稳定 403**。本工具用 `ensure_clone_f2_on_path` 自动处理，并在
   启动时打印实际用的 f2 路径。**这是 403 的真正成因**（实测：同一份 f2 生成的签名
   URL，换 3 种浏览器 TLS 指纹仍然 403；改回克隆版同一请求 200）。
2. **Cookie**：`--cookie` / `--cookie-file` / `--auto-cookie chrome`（需先关闭浏览器）
   / 否则回落 f2 配置。本次跑通用的是从 Chrome 读出的登录态 Cookie。
   ⚠ **「游客 Cookie 是否也能枚举」尚未验证**：早先「游客 → 403」的结论是在旧版 f2
   下得到的，不能归因于 Cookie；后来想复测时 f2 配置里的 cookie 已是空值（字段数 0，
   返回 `status=None`），没有得到干净结论。别把「必须有登录态」当成已证事实。

对齐键的精确性
--------------
文件名里的描述是 f2 自己变换过的：非法字符替换成 `_`（`replaceT`）+ 超长中段
截断（`split_filename`）。本脚本**直接复用 f2 的 `format_file_name`**，而不是另写
一套归一化——两边差一个字符就会全判未命中，报告就失去意义。
"""

import argparse
import asyncio
import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# 与 backend/scripts 下其它脚本一致：把 backend 加入 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from .import_f2_downloads import (  # noqa: E402
    DEFAULT_F2_DIR,
    DEFAULT_F2_LIKE_ROOT,
    DEFAULT_F2_ROOT,
    F2_AUTHOR_DB,
    LIKE_NAMING_TEMPLATE,
    normalize_author,
    scan_directory,
)

"""发布模式的「无作品 ID」历史命名模板：对齐时按它重建预期文件名。

不能用 :data:`POST_NAMING_TEMPLATE`——那是**新**模板（含 `{aweme_id}`），而这里
要对齐的是**已经下载到磁盘的历史文件**，它们用的是旧模板。
"""
LEGACY_POST_NAMING = "{create}_{desc}"

"""点赞模式的历史命名模板（同上，去掉 `{aweme_id}`）。"""
LEGACY_LIKE_NAMING = LIKE_NAMING_TEMPLATE.replace("_{aweme_id}", "")

"""抖音作品清单接口的每页条数（f2 帮助里建议不超过 20）。"""
DEFAULT_PAGE_COUNTS = 20
"""翻页间隔（秒）：接口自带风控，别打太快。"""
DEFAULT_PAGE_SLEEP = 3.0

"""f2 配置里 cookie 行的样子（yaml 单行）。"""
_COOKIE_LINE_RE = re.compile(r"^\s*cookie\s*:\s*(?P<value>.*)$", re.M)

"""对齐主干里的发布时间（与 f2 的 {create} 同形：YYYY-MM-DD HH-MM-SS）。"""
_TIMESTAMP_IN_STEM_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2}")


@dataclass
class ApiWork:
    """接口枚举到的一个作品（只含对齐需要的三项）。"""

    aweme_id: str
    created: str  # f2 口径的发布时间（东八区 %Y-%m-%d %H-%M-%S，与文件名一致）
    desc: str  # 原始描述（未做 f2 的字符替换/截断）
    stem: str  # 该作品在磁盘上应有的文件名主干（由 f2 的 format_file_name 生成）


# ═══════════════════════════════════════════════════════════════
#  Cookie
# ═══════════════════════════════════════════════════════════════


def read_cookie_from_conf(conf_path: Path) -> str:
    """从 f2 配置里读 cookie 行。

    Args:
        conf_path: f2 配置文件（app.yaml）。

    Returns:
        cookie 字符串；文件不存在或无 cookie 行时返回空串。
    """
    try:
        text = conf_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = _COOKIE_LINE_RE.search(text)
    if not match:
        return ""
    return match.group("value").strip().strip("\"'")


def find_conf_path(f2_dir: Path) -> Path | None:
    """定位 f2 配置文件（两种实际布局都试）。

    Args:
        f2_dir: f2 工作目录。

    Returns:
        存在的配置文件路径；都没有则 None。
    """
    for cand in (f2_dir / "f2" / "conf" / "app.yaml", f2_dir / "conf" / "app.yaml"):
        if cand.is_file():
            return cand
    return None


def normalize_cookie(raw: str) -> str:
    """把各种形态的 Cookie 输入归一成 `a=b; c=d` 字符串。

    支持：已经是 header 串、浏览器扩展导出的 JSON 数组、`{name: value}` 字典。

    Args:
        raw: 原始文本（文件内容或命令行值）。

    Returns:
        Cookie 字符串；输入为空返回空串。
    """
    text = (raw or "").strip()
    if not text:
        return ""
    if text.startswith(("[", "{")):
        try:
            data = json.loads(text)
        except ValueError:
            return text
        if isinstance(data, dict):
            pairs = [(k, v) for k, v in data.items() if isinstance(v, str)]
        else:
            pairs = [
                (c.get("name"), c.get("value") or "")
                for c in data
                if isinstance(c, dict) and c.get("name")
            ]
        return "; ".join(f"{k}={v}" for k, v in pairs)
    return text


def cookie_field_names(cookie: str) -> list[str]:
    """列出 Cookie 字段名（用于提示「是不是游客 Cookie」）。"""
    return [kv.split("=", 1)[0].strip() for kv in cookie.split(";") if "=" in kv]


def looks_logged_out(cookie: str) -> bool:
    """是否明显是**游客** Cookie（缺 sessionid 系列）。

    只用于给出提示，**不是**失败判据：早先「游客 Cookie → 403」的观察是在旧版 f2
    （签名已失效）下得到的，不能归因于 Cookie 本身，故此处仅提示「本次跑通用的是
    登录态」。

    Args:
        cookie: Cookie 字符串。

    Returns:
        True 表示疑似未登录。
    """
    names = cookie_field_names(cookie)
    return not any(n.startswith(("sessionid", "sid_tt", "sid_guard")) for n in names)


# ═══════════════════════════════════════════════════════════════
#  对齐（纯函数，可离线单测）
# ═══════════════════════════════════════════════════════════════


def disk_work_stem(item, like_mode: bool) -> str:
    """磁盘文件 → 对齐用的文件名主干（去掉 `_{类型}[_{序号}]` 后缀）。

    Args:
        item: :class:`ParsedFile`（已解析的磁盘文件）。
        like_mode: 是否点赞模式（点赞产物带作者前缀）。

    Returns:
        文件名主干。
    """
    if like_mode:
        return f"{item.author_dir}_{item.created}_{item.body}"
    return f"{item.created}_{item.body}"


def api_work_stem(created: str, desc: str, like_mode: bool, nickname: str = "") -> str:
    """接口作品 → 预期文件名主干（复用 f2 自己的变换，保证逐字符一致）。

    `format_file_name` 内部做 `replaceT`（非法字符→`_`）+ `split_filename`
    （超长中段截断），与 f2 下载时写文件名走的是同一段代码。

    Args:
        created: f2 口径的发布时间。
        desc: 原始描述（未变换）。
        like_mode: 是否点赞模式。
        nickname: 点赞模式的作者名（发布模式忽略）。

    Returns:
        预期文件名主干。
    """
    from f2.apps.douyin.utils import format_file_name  # 懒加载：未装 f2 也能导入本模块

    from f2.utils.utils import replaceT

    template = LEGACY_LIKE_NAMING if like_mode else LEGACY_POST_NAMING
    return format_file_name(
        template,
        {"create_time": created, "desc": replaceT(desc), "nickname": nickname},
    )


def classify_alignment(
    disk_stems: list[str], api_works: list[ApiWork]
) -> dict:
    """按文件名主干对齐磁盘作品与接口作品，分三档。

    档位定义（严格口径，**不做模糊匹配**——猜错等于把 A 作品的链接写到 B 素材上）：

    - **唯一对齐**：该主干在接口侧恰好一个作品 → 可回填
    - **多义**：接口侧多个作品共用同一主干（同作者同秒同描述）→ 不回填
    - **未命中**：接口侧没有该主干（描述被截断/作品已删/枚举不全）→ 不回填

    Args:
        disk_stems: 磁盘作品的主干列表（已去重）。
        api_works: 接口枚举到的作品列表。

    Returns:
        ``{"unique": [(stem, aweme_id)], "ambiguous": [(stem, [aweme_id, ...])],
        "unmatched": [stem], "api_only": [aweme_id], "counts": {...}}``。
    """
    index: dict[str, list[ApiWork]] = {}
    for work in api_works:
        index.setdefault(work.stem, []).append(work)

    disk_set = set(disk_stems)
    unique: list[tuple[str, str]] = []
    ambiguous: list[tuple[str, list[str]]] = []
    unmatched: list[str] = []

    for stem in disk_stems:
        candidates = index.get(stem, [])
        if not candidates:
            unmatched.append(stem)
        elif len(candidates) == 1:
            unique.append((stem, candidates[0].aweme_id))
        else:
            ambiguous.append((stem, [c.aweme_id for c in candidates]))

    api_only = [
        work.aweme_id for stem, works in index.items() if stem not in disk_set for work in works
    ]

    total = len(disk_stems)
    counts = {
        "disk_works": total,
        "api_works": len(api_works),
        "unique": len(unique),
        "ambiguous": len(ambiguous),
        "unmatched": len(unmatched),
        "api_only": len(api_only),
        # 覆盖率按磁盘作品算：唯一对齐 / 全部磁盘作品
        "coverage": round(len(unique) / total, 4) if total else 0.0,
    }
    return {
        "unique": unique,
        "ambiguous": ambiguous,
        "unmatched": unmatched,
        "api_only": api_only,
        "counts": counts,
    }


def analyze_disk_keys(disk: dict[str, list[str]]) -> dict:
    """只用磁盘数据得出的**对齐风险画像**（不联网）。

    为什么值得单独算：接口受 Cookie 限制暂时跑不了，但「对齐键本身是否够用」完全
    可以从磁盘侧判定——如果同一作者下已存在重复主干，那些作品**必然**落进多义档，
    与接口返回什么无关。

    统计口径：

    - ``duplicate_stems``：同一作者内主干重复的作品数。对齐键完全相同 → 接口侧若
      有 ≥2 条即多义，若只 1 条则说明这两份是同一作品被下载了两次
    - ``placeholder_time``：发布时间是 ``00-00-00`` 的作品。实测存在（f2 拿到的
      create_time 为整点 0 分 0 秒），若接口给的是真实时间则**必然未命中**
    - ``missing_time``：主干里找不到 ``YYYY-MM-DD HH-MM-SS`` 的作品（解析异常）
    - ``empty_body``：描述为空的作品（对齐键退化成只有时间，同秒即撞）
    - ``truncated_body``：描述被 f2 中段截断的作品（**信息性**：截断是确定性的，
      用原始描述能复现同样结果，不构成未命中风险）

    Args:
        disk: 归一化作者名 → 作品主干列表（:func:`collect_disk_works` 的结果）。

    Returns:
        画像字典（含计数与示例）。
    """
    duplicate_examples: list[dict] = []
    placeholder_examples: list[str] = []
    missing_examples: list[str] = []
    duplicate_works = 0
    placeholder_time = 0
    missing_time = 0
    empty_body = 0
    truncated_body = 0
    total = 0

    for author, stems in disk.items():
        seen: dict[str, int] = {}
        for stem in stems:
            total += 1
            seen[stem] = seen.get(stem, 0) + 1

            match = _TIMESTAMP_IN_STEM_RE.search(stem)
            if not match:
                missing_time += 1
                if len(missing_examples) < 5:
                    missing_examples.append(f"{author}: {stem[:60]}")
            elif match.group(0).endswith("00-00-00"):
                placeholder_time += 1
                if len(placeholder_examples) < 5:
                    placeholder_examples.append(f"{author}: {stem[:60]}")

            if stem.endswith("_") or stem.endswith("__"):
                empty_body += 1
            if "......" in stem:
                truncated_body += 1

        dups = sorted(s for s, n in seen.items() if n > 1)
        if dups:
            duplicate_works += sum(seen[s] for s in dups)
            if len(duplicate_examples) < 5:
                duplicate_examples.append(
                    {"author": author, "count": len(dups), "sample": dups[0][:60]}
                )

    return {
        "works": total,
        "authors": len(disk),
        "duplicate_stems": duplicate_works,
        "duplicate_examples": duplicate_examples,
        "placeholder_time": placeholder_time,
        "placeholder_examples": placeholder_examples,
        "missing_time": missing_time,
        "missing_examples": missing_examples,
        "empty_body": empty_body,
        "truncated_body": truncated_body,
    }


def render_disk_analysis(analysis: dict) -> str:
    """把磁盘侧风险画像渲染成人读文本。

    Args:
        analysis: :func:`analyze_disk_keys` 的结果。

    Returns:
        多行文本。
    """
    lines = [
        "─" * 62,
        "  磁盘侧对齐风险画像（不联网可得；解释下面三档时的背景）",
        "─" * 62,
        f"  作品总数            {analysis['works']}（{analysis['authors']} 个作者）",
        f"  主干重复            {analysis['duplicate_stems']}"
        f"（同作者内对齐键相同 → 必然落多义档）",
        f"  时间戳为 00-00-00   {analysis['placeholder_time']}"
        f"（若接口给真实时间则必然未命中）",
        f"  时间戳缺失          {analysis['missing_time']}（解析异常，需排查）",
        f"  描述为空            {analysis['empty_body']}（键退化为只有时间）",
        f"  描述被截断          {analysis['truncated_body']}"
        f"（信息性：截断确定性，不构成风险）",
    ]
    for item in analysis["duplicate_examples"]:
        lines.append(f"    · 重复 {item['count']} 组：{item['author']} / {item['sample']}")
    for sample in analysis["placeholder_examples"][:2]:
        lines.append(f"    · 占位时间：{sample}")
    for sample in analysis["missing_examples"][:2]:
        lines.append(f"    · 缺时间戳：{sample}")
    return "\n".join(lines)


def merge_reports(per_author: dict[str, dict]) -> dict:
    """把各作者的对齐结果汇总成总报告。

    Args:
        per_author: 归一化作者名 → :func:`classify_alignment` 的结果。

    Returns:
        总报告（含每作者明细与总体计数）。
    """
    totals = {
        "authors": len(per_author),
        "disk_works": 0,
        "api_works": 0,
        "unique": 0,
        "ambiguous": 0,
        "unmatched": 0,
        "api_only": 0,
    }
    for result in per_author.values():
        for key in ("disk_works", "api_works", "unique", "ambiguous", "unmatched", "api_only"):
            totals[key] += result["counts"][key]
    totals["coverage"] = (
        round(totals["unique"] / totals["disk_works"], 4) if totals["disk_works"] else 0.0
    )
    return {"totals": totals, "authors": per_author}


def render_report(report: dict, sample: int = 5) -> str:
    """把报告渲染成人读文本。

    Args:
        report: :func:`merge_reports` 的结果。
        sample: 每档最多示例几条。

    Returns:
        多行文本。
    """
    lines: list[str] = []
    totals = report["totals"]
    lines.append("=" * 62)
    lines.append("  f2 历史素材『真实作品 ID』对齐覆盖率报告（只读，未写库）")
    lines.append("=" * 62)
    lines.append(f"  作者数        {totals['authors']}")
    lines.append(f"  磁盘作品数    {totals['disk_works']}")
    lines.append(f"  接口作品数    {totals['api_works']}")
    lines.append(f"  唯一对齐      {totals['unique']}")
    lines.append(f"  多义（不回填）{totals['ambiguous']}")
    lines.append(f"  未命中        {totals['unmatched']}")
    lines.append(f"  接口独有      {totals['api_only']}（接口有、磁盘没有，信息性）")
    lines.append(f"  >> 覆盖率     {totals['coverage'] * 100:.2f}%")
    lines.append("")

    for author, result in sorted(report["authors"].items()):
        c = result["counts"]
        lines.append(
            f"[{author}] 磁盘 {c['disk_works']} / 接口 {c['api_works']} "
            f"→ 唯一 {c['unique']}（{c['coverage'] * 100:.1f}%）"
            f" 多义 {c['ambiguous']} 未命中 {c['unmatched']}"
        )
        for stem, aweme_id in result["unique"][:sample]:
            lines.append(f"    ✓ {stem[:58]}  →  {aweme_id}")
        for stem, ids in result["ambiguous"][:sample]:
            lines.append(f"    ? {stem[:46]}  →  多义 {ids[:3]}")
        for stem in result["unmatched"][:sample]:
            lines.append(f"    ✗ {stem[:58]}")
        if result["unmatched"]:
            lines.append("      （未命中示例：描述可能被 f2 截断，或作品已删/未枚举到）")

    if report.get("disk_analysis"):
        lines.append("")
        lines.append(render_disk_analysis(report["disk_analysis"]))
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  活体：枚举接口作品清单（不下载媒体）
# ═══════════════════════════════════════════════════════════════


def ensure_clone_f2_on_path(f2_dir: Path) -> str:
    """把 f2 工作目录插到 `sys.path` 最前，确保导入的是**项目实际在用的那份 f2**。

    ⚠ 这一步不能省（2026-09 实测踩坑）：PyPI 上的 f2 最新只到 `0.0.1.7`
    （2024-12-31 发布），其抖音签名已失效——用**旧版**调作品清单接口稳定返回
    **HTTP 403**，即使带完全有效的登录态 Cookie。而 f2 工作目录是 git clone，
    项目通过「`cwd=f2_dir` 跑 `python -m f2`」用的正是克隆里那一份。

    本工具在**进程内** import f2（不像项目那样起子进程），若不明式插路径，拿到的是
    site-packages 里的旧版 → 403。插路径后需清掉已导入的旧模块，否则不生效。

    Args:
        f2_dir: f2 工作目录（内含 `f2/` 包）。

    Returns:
        实际生效的 f2 包文件路径；该目录下没有 `f2/` 包时返回空串（回退 site-packages）。
    """
    root = str(f2_dir)
    if not (f2_dir / "f2" / "__init__.py").is_file():
        return ""

    loaded = sys.modules.get("f2")
    if loaded is not None and getattr(loaded, "__file__", "").startswith(root):
        return loaded.__file__  # 已经是这一份，无需重导

    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    if loaded is not None:
        for name in [m for m in list(sys.modules) if m == "f2" or m.startswith("f2.")]:
            del sys.modules[name]

    import f2  # noqa: PLC0415 —— 必须在插路径之后导入

    return getattr(f2, "__file__", "")


async def enumerate_author_works(
    sec_user_id: str,
    cookie: str,
    *,
    like_mode: bool = False,
    nickname: str = "",
    max_works: int = 0,
    page_counts: int = DEFAULT_PAGE_COUNTS,
    sleep_seconds: float = DEFAULT_PAGE_SLEEP,
) -> list[ApiWork]:
    """枚举单个博主的作品清单（**只取元数据，不下载任何媒体**）。

    复用 f2 自己的签名（a_bogus）与数据过滤器，只替换掉「拿到清单之后去下载」
    这一步——这是与 f2 下载命令唯一的区别。

    Args:
        sec_user_id: 博主 sec_user_id。
        cookie: 登录态 Cookie 字符串。
        like_mode: 点赞模式（影响预期文件名的作者前缀）。
        nickname: 点赞模式下的作者名。
        max_works: 最多枚举多少个作品（0 表示不限）。
        page_counts: 每页条数。
        sleep_seconds: 翻页间隔（秒）。

    Returns:
        作品列表。

    Raises:
        RuntimeError: f2 未安装，或接口返回错误（含 403 未登录）。
    """
    from f2.apps.douyin.crawler import DouyinCrawler
    from f2.apps.douyin.filter import UserPostFilter
    from f2.apps.douyin.model import UserPost

    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    kwargs = {
        "cookie": cookie,
        "headers": {"User-Agent": user_agent, "Referer": "https://www.douyin.com/"},
        "proxies": {"http://": None, "https://": None},
        "timeout": 10,
        "max_tasks": 1,
        "max_connections": 1,
        "app_name": "douyin",
    }

    works: list[ApiWork] = []
    cursor = 0
    seen: set[str] = set()
    while True:
        async with DouyinCrawler(kwargs) as crawler:
            params = UserPost(
                max_cursor=cursor,
                count=min(page_counts, max_works - len(works)) if max_works else page_counts,
                sec_user_id=sec_user_id,
            )
            response = await crawler.fetch_user_post(params)
        page = UserPostFilter(response)

        ids = page.aweme_id or []
        created_list = page.create_time or []
        desc_list = page.desc_raw or []
        for aweme_id, created, desc in zip(ids, created_list, desc_list, strict=False):
            aweme_id = str(aweme_id)
            if not aweme_id or aweme_id in seen:
                continue
            seen.add(aweme_id)
            works.append(
                ApiWork(
                    aweme_id=aweme_id,
                    created=str(created),
                    desc=str(desc or ""),
                    stem=api_work_stem(str(created), str(desc or ""), like_mode, nickname),
                )
            )
            if max_works and len(works) >= max_works:
                return works

        if not getattr(page, "has_more", False):
            break
        next_cursor = page.max_cursor
        if next_cursor is None or next_cursor == cursor:
            break  # 游标不前进：防死循环
        cursor = next_cursor
        await asyncio.sleep(sleep_seconds)

    return works


# ═══════════════════════════════════════════════════════════════
#  磁盘侧
# ═══════════════════════════════════════════════════════════════


def collect_disk_works(root: Path, mode: str) -> dict[str, list[str]]:
    """扫描磁盘，按**归一化作者名**归集作品主干（去重保序）。

    注：同一作者可能落在多个目录（`不养羊` / `不养羊√`），归一化后合并——与
    导入侧的绑定口径一致。

    Args:
        root: f2 下载根目录（post 或 like）。
        mode: ``post`` | ``like``。

    Returns:
        归一化作者名 → 作品主干列表。
    """
    like_mode = mode == "like"
    grouped: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    for item in scan_directory(root):
        stem = disk_work_stem(item, like_mode)
        key = (item.author_key, stem)
        if key in seen:
            continue
        seen.add(key)
        grouped.setdefault(item.author_key, []).append(stem)
    return grouped


def load_author_sec_ids(f2_dir: Path) -> dict[str, str]:
    """读 f2 用户库：归一化昵称 → sec_user_id。

    Args:
        f2_dir: f2 工作目录。

    Returns:
        映射；库不存在时返回空字典。
    """
    db = f2_dir / F2_AUTHOR_DB
    if not db.is_file():
        return {}
    try:
        conn = sqlite3.connect(str(db))
        rows = conn.execute("SELECT nickname, sec_user_id FROM user_info_web").fetchall()
        conn.close()
    except sqlite3.Error:
        return {}
    return {normalize_author(str(n or "")): str(s or "") for n, s in rows if s}


# ═══════════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════════


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="f2 历史素材真实作品 ID 对齐覆盖率报告（只读，不写库）"
    )
    parser.add_argument("--f2-dir", default=str(DEFAULT_F2_DIR), help="f2 工作目录")
    parser.add_argument("--mode", default="post", choices=["post", "like"], help="采集模式")
    parser.add_argument("--authors", nargs="*", default=None, help="只跑这些作者（归一化名）")
    parser.add_argument("--limit", type=int, default=0, help="作者数上限（0=全部）")
    parser.add_argument(
        "--max-works", type=int, default=0, help="每位作者最多枚举多少作品（0=不限）"
    )
    parser.add_argument("--cookie", default="", help="抖音 Cookie 字符串")
    parser.add_argument("--cookie-file", default="", help="从文件读 Cookie（JSON 或文本）")
    parser.add_argument(
        "--auto-cookie",
        default="",
        help=(
            "从本机浏览器读抖音 Cookie（chrome/edge/firefox/chromium/brave/vivaldi…）。"
            "会读取浏览器保存的登录凭证，执行前请关闭该浏览器"
        ),
    )
    parser.add_argument("--conf", default="", help="f2 配置文件路径（缺省自动定位）")
    parser.add_argument("--page-counts", type=int, default=DEFAULT_PAGE_COUNTS)
    parser.add_argument("--sleep", type=float, default=DEFAULT_PAGE_SLEEP, help="翻页间隔秒")
    parser.add_argument("--output", default="", help="报告 JSON 落盘路径（缺省只打印）")
    parser.add_argument("--sample", type=int, default=5, help="每档打印示例条数")
    parser.add_argument(
        "--disk-only",
        action="store_true",
        help="只扫磁盘、不调接口（核对解析与作者归集，不发请求）",
    )
    return parser.parse_args(argv)


def cookie_from_browser(browser: str, domain: str = "douyin.com") -> str:
    """从本机浏览器读指定域名的 Cookie（f2 的 `--auto-cookie` 同款机制）。

    ⚠ 这会读取浏览器保存的 Cookie（含登录凭证）。**由使用者显式传 `--auto-cookie`
    触发**——本工具不会替你决定去读。f2 官方要求执行前**关闭该浏览器**（Cookie
    库被占用时读不到）。

    Args:
        browser: 浏览器名（chrome / edge / firefox / chromium / brave / vivaldi …）。
        domain: 域名后缀过滤。

    Returns:
        `a=b; c=d` 形式的 Cookie 字符串。

    Raises:
        RuntimeError: 未装 f2，或该浏览器里没有该域名的 Cookie。
    """
    try:
        from f2.utils.utils import get_cookie_from_browser, split_dict_cookie
    except ImportError as e:  # pragma: no cover - 未装 f2 的环境
        raise RuntimeError("未安装 f2，无法使用 --auto-cookie") from e

    cookie = split_dict_cookie(get_cookie_from_browser(browser, domain))
    if not cookie:
        raise RuntimeError(
            f"无法从 {browser} 浏览器中获取 {domain} 的 Cookie"
            "（请先关闭该浏览器，并确认已登录抖音）"
        )
    return cookie


def resolve_cookie(args: argparse.Namespace, f2_dir: Path) -> str:
    """按优先级取 Cookie：--cookie > --cookie-file > --auto-cookie > f2 配置。"""
    if args.cookie:
        return normalize_cookie(args.cookie)
    if args.cookie_file:
        try:
            return normalize_cookie(Path(args.cookie_file).read_text(encoding="utf-8"))
        except OSError as e:
            print(f"⚠️  读 Cookie 文件失败：{e}")
    if getattr(args, "auto_cookie", ""):
        try:
            cookie = cookie_from_browser(args.auto_cookie)
            print(f"已从 {args.auto_cookie} 浏览器读取 Cookie")
            return cookie
        except Exception as e:  # noqa: BLE001 —— 读不到就继续回落，别中断
            print(f"⚠️  --auto-cookie {args.auto_cookie} 失败：{str(e)[:160]}")
    conf = Path(args.conf) if args.conf else find_conf_path(f2_dir)
    return normalize_cookie(read_cookie_from_conf(conf)) if conf else ""


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    f2_dir = Path(args.f2_dir)
    root = DEFAULT_F2_LIKE_ROOT if args.mode == "like" else DEFAULT_F2_ROOT
    # 允许 --f2-dir 指向非缺省位置时按同样结构推导
    if args.f2_dir != str(DEFAULT_F2_DIR):
        sub = Path("Download/douyin/like" if args.mode == "like" else "Download/douyin/post")
        root = f2_dir / sub

    print(f"扫描磁盘：{root}")
    disk = collect_disk_works(root, args.mode)
    if not disk:
        print("❌ 未扫到任何作品（确认 --f2-dir / --mode 是否正确）")
        return 1
    print(f"  作者 {len(disk)} 个，作品 {sum(len(v) for v in disk.values())} 个")

    # 磁盘侧风险画像：不联网可得，且是解释后续三档的背景（--disk-only 也能看）
    disk_analysis = analyze_disk_keys(disk)
    print()
    print(render_disk_analysis(disk_analysis))

    if args.disk_only:
        return 0

    # ⚠ 必须在**任何** f2 用法之前把克隆目录插到 sys.path 最前：否则用到
    # site-packages 里那份 2024-12-31 的旧 f2，签名失效 → 稳定 403
    # （详见 ensure_clone_f2_on_path；含 --auto-cookie 内部的 f2 导入）
    f2_pkg = ensure_clone_f2_on_path(f2_dir)
    if f2_pkg:
        print(f"f2 包：{f2_pkg}")
    else:
        print(
            f"⚠️  {f2_dir} 下没有 f2/ 包，将使用 site-packages 的 f2——"
            "PyPI 最新版（0.0.1.7, 2024-12-31）签名可能已失效并返回 403"
        )

    cookie = resolve_cookie(args, f2_dir)
    if not cookie:
        print(
            "❌ 没有 Cookie。抖音作品清单接口对游客返回 403，必须提供登录态 Cookie：\n"
            "   --cookie '<cookie 串>' / --cookie-file <文件> / --auto-cookie chrome"
            "（需先关闭浏览器）/ 或先刷新 f2 配置里的 cookie"
        )
        return 1

    names = cookie_field_names(cookie)
    print(f"Cookie 字段 {len(names)} 个：{names[:8]}{'…' if len(names) > 8 else ''}")
    if looks_logged_out(cookie):
        print(
            "⚠️  Cookie 里没有 sessionid 系列字段（疑似游客态）——不一定会失败，\n"
            "    但本次跑通用的是登录态 Cookie；若这里 403，先换成登录态再试。"
        )

    sec_ids = load_author_sec_ids(f2_dir)
    targets = sorted(disk)
    if args.authors:
        wanted = {normalize_author(a) for a in args.authors}
        targets = [a for a in targets if a in wanted]
    if args.limit:
        targets = targets[: args.limit]
    if not targets:
        print("❌ 没有可处理的作者（--authors / --limit 过滤后为空）")
        return 1

    per_author: dict[str, dict] = {}
    for index, author in enumerate(targets, 1):
        sec = sec_ids.get(author, "")
        if not sec:
            print(f"[{index}/{len(targets)}] {author}：f2 用户库里没有 sec_user_id，跳过")
            continue
        print(f"[{index}/{len(targets)}] 枚举 {author}（{sec[:18]}…）", flush=True)
        try:
            works = asyncio.run(
                enumerate_author_works(
                    sec,
                    cookie,
                    like_mode=args.mode == "like",
                    nickname=author,
                    max_works=args.max_works,
                    page_counts=args.page_counts,
                    sleep_seconds=args.sleep,
                )
            )
        except Exception as e:  # noqa: BLE001 —— 单个作者失败不阻断整轮
            print(f"    ❌ 枚举失败：{type(e).__name__}: {str(e)[:160]}")
            continue
        print(f"    接口返回 {len(works)} 个作品")
        per_author[author] = classify_alignment(disk[author], works)
        if not works:
            continue
        time.sleep(0.2)  # 让打印不被下一行冲掉

    if not per_author:
        print("❌ 没有任何作者枚举成功，无法产出报告")
        return 1

    report = merge_reports(per_author)
    report["disk_analysis"] = disk_analysis
    print()
    print(render_report(report, sample=args.sample))

    if args.output:
        Path(args.output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n报告已写入：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
