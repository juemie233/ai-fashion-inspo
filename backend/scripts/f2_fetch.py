"""f2 命令构造与增量下载（自 scripts/import_f2_downloads.py 拆出，行为不变）。"""

import importlib.util
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

# 与 backend/scripts 下其它脚本一致：把 backend 加入 sys.path，便于模块方式执行
sys.path.insert(0, str(Path(__file__).parent.parent))

from .scraper_common import utcnow  # noqa: E402

from .f2_common import (  # noqa: E402
    COLLECT_NAMING_TEMPLATE,
    DEFAULT_FETCH_SINCE_DAYS,
    F2_AUTHOR_DB,
    F2_DOWNLOAD_SUBDIR,
    LIKE_NAMING_TEMPLATE,
    POST_NAMING_TEMPLATE,
    WINDOW_MARGIN_DAYS,
    normalize_author,
)
from .f2_plan import (  # noqa: E402
    load_douyin_bloggers,
    select_known_authors,
)


# ═══════════════════════════════════════════════════════════════
#  调用 f2 增量下载（--fetch）
# ═══════════════════════════════════════════════════════════════


def f2_available() -> bool:
    """f2 是否已安装（以模块方式 `python -m f2` 调用）。"""
    return importlib.util.find_spec("f2") is not None


def author_last_download(post_root: Path) -> dict[str, datetime]:
    """归一化作者名 → 该作者目录的最近下载时间（目录 mtime）。

    f2 把作品文件直接放在作者目录下（实测 24 个作者目录下 0 个子目录），
    因此目录 mtime 就是「上次为这个作者下到东西的时间」，读取成本是 1 次
    `scandir`（Windows 上 DirEntry 的 stat 不再额外落盘）。

    `√` / 波浪号等同名变体（`不养羊` 与 `不养羊√`）按归一化名合并，取最新的那个。

    Args:
        post_root: f2 的 post 目录（`Download/douyin/post`）。

    Returns:
        归一化名 → mtime；目录不存在时返回空字典。
    """
    if not post_root.exists():
        return {}
    newest: dict[str, datetime] = {}
    for entry in os.scandir(post_root):
        if not entry.is_dir():
            continue
        key = normalize_author(entry.name)
        if not key:
            continue
        stamp = datetime.fromtimestamp(entry.stat().st_mtime)
        if key not in newest or stamp > newest[key]:
            newest[key] = stamp
    return newest


def compute_fetch_interval(
    since_days: int | None,
    last_download_at: datetime | None,
    today: date | None = None,
) -> str:
    """决定传给 f2 的 `-i` 值：``all`` 或 ``YYYY-MM-DD|YYYY-MM-DD``（纯函数）。

    规则：
      - ``since_days`` 为 None 或 <= 0 → ``all``（首次全量 / 显式要求全历史）
      - 否则窗口起点取 ``min(今天 - since_days, 上次下载日 - 1 天)``：
        超过 ``since_days`` 没跑过时窗口自动放大到「上次下载之前」，保证长时间
        不跑也不漏作品；``since_days`` 本身作为最小宽度留作安全余量（覆盖上次
        列表里下载失败/只落了 .tmp 的作品）
      - 没有任何本地下载记录（新博主）→ 按最小窗口取，**首次也是增量而非全量重扫**
        （见 ``test_compute_fetch_interval_uses_minimum_window``：批量增量时一次跑
        几十个作者，让新博主也翻完整历史代价太大）。要注意这与
        :func:`compute_profile_interval` 的口径**故意不同**——后者用于「按博主
        全量下载」，首次必须是 `all`。
    Args:
        since_days: 最小窗口天数；None/<=0 表示全历史。
        last_download_at: 该作者目录的最近下载时间（:func:`author_last_download`）。
        today: 今天（便于测试注入）。

    Returns:
        f2 `-i` 参数值。
    """
    if not since_days or since_days <= 0:
        return "all"
    end = today or utcnow().date()
    start = end - timedelta(days=since_days)
    if last_download_at is not None:
        widened = last_download_at.date() - timedelta(days=WINDOW_MARGIN_DAYS)
        if widened < start:
            start = widened
    return f"{start:%Y-%m-%d}|{end:%Y-%m-%d}"


def compute_profile_interval(
    since_days: int | None,
    last_download_at: datetime | None,
    today: date | None = None,
) -> str:
    """「按博主全量下载」的日期窗口：**首次必须全量**（纯函数）。

    为什么不能用 :func:`compute_fetch_interval`：那个是给**批量增量**用的，它刻意
    让「没有本地下载记录的新博主」也走最小窗口（避免一次给几十个作者翻完整历史，
    其测试 ``test_compute_fetch_interval_uses_minimum_window`` 锁死了这个行为）。

    但「按博主全量下载」的语义就是**把她的作品拿全**：用户点名一个博主，如果只拿到
    最近 ``since_days`` 天，他会以为下全了其实没有。所以这里首次给 `all`；已经下过
    之后再跑就退回窗口增量（f2 本来也会跳过已下载的文件，不容易漏）。

    Args:
        since_days: 最小窗口天数（None/<=0 表示全历史）。
        last_download_at: 该作者目录的最近下载时间；None = 从未下过。
        today: 今天（便于测试注入）。

    Returns:
        f2 `-i` 参数值。
    """
    if last_download_at is None:
        return "all"
    return compute_fetch_interval(since_days, last_download_at, today)


def load_f2_authors(f2_dir: Path) -> list[dict]:
    """读取 f2 用户库里的作者清单（增量下载的「关注了谁」来源）。

    为什么以 f2 的库为准：素材库的 `bloggers` 表**没有 sec_user_id**
    （实测 24 个抖音博主的 platform_user_id 多为 NULL、profile_url 全空），
    而 f2 的 `user_info_web` 存了 sec_user_id / nickname / aweme_count。

    Args:
        f2_dir: f2 工作目录（其下有 douyin_users.db）。

    Returns:
        [{"sec_user_id", "nickname", "aweme_count"}]；库或表缺失时返回空列表。
    """
    db = f2_dir / F2_AUTHOR_DB
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT sec_user_id, nickname, aweme_count FROM user_info_web"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [
        {
            "sec_user_id": row[0],
            "nickname": row[1] or "",
            "aweme_count": int(row[2] or 0),
        }
        for row in rows
        if row[0]
    ]


def load_f2_profiles(f2_dir: Path) -> dict[str, dict]:
    """读取 f2 用户库里的账号资料，按 sec_user_id 索引（供离线回填博主 IP 属地）。

    为什么能用：f2 的 ``user_info_web`` 存着它见过账号的 ``ip_location``
    （形如「IP属地：浙江」）——素材库的 ``bloggers.ip_location`` 正好缺这块，
    而这份数据**离线可读**（不联网、无风控），比重新去抓主页划算得多。

    Args:
        f2_dir: f2 工作目录（其下有 douyin_users.db）。

    Returns:
        {sec_user_id: {"nickname", "ip_location"}}；``ip_location`` 已按既有口径剥掉
        「IP属地：」前缀（复用 ``fetch_xhs_following.parse_ip_location``，全角/半角
        冒号都认，裸值不会误判）。库/表/列缺失时返回空 dict。
    """
    from .fetch_xhs_following import parse_ip_location

    db = f2_dir / F2_AUTHOR_DB
    if not db.exists():
        return {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        try:
            rows = conn.execute(
                "SELECT sec_user_id, nickname, ip_location FROM user_info_web"
            ).fetchall()
        except sqlite3.OperationalError:
            # 极小/旧版本的库可能没有 ip_location 列：没有可回填的字段，直接当空
            return {}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    profiles: dict[str, dict] = {}
    for sec_user_id, nickname, ip_location in rows:
        if not sec_user_id:
            continue
        profiles[sec_user_id] = {
            "nickname": nickname or "",
            "ip_location": parse_ip_location(ip_location or ""),
        }
    return profiles


def build_f2_command(
    author: dict,
    download_root: Path | None = None,
    naming: str | None = None,
    auto_cookie: str | None = None,
    interval: str = "all",
) -> list[str]:
    """构造单个作者的 f2 下载命令（主页作品；增量由 f2 保证）。

    - `-M post`：主页发布的作品（f2 支持 post/like/collect/mix…）
    - `-i`：日期区间。**缺省调用方会给窗口**（见 :func:`compute_fetch_interval`），
      `all` 表示翻作者全部历史——`all` 时 f2 不设 `min_cursor`，必须一路翻到底，
      每页还固定 sleep 一次 `timeout`（本机 10 秒），所以日常增量务必给窗口
    - `-p`：下载根目录（缺省就是 f2 工作目录下的 Download/）
    - `-n`：命名模板。**缺省传 :data:`POST_NAMING_TEMPLATE`**（含 `{aweme_id}`），
      这样新素材带真实作品 ID、能写回抖音原帖链接；不再沿用 f2 配置里的模板
      ——配置里的 `{create}_{desc}` 不含作品 ID，会让新素材继续失去可追溯性。
      解析器与新模板同步（见 :data:`KIND_RE_WITH_ID`），并兼容旧命名的历史文件
    - `--auto-cookie`：从浏览器自动取 cookie（需先关闭该浏览器）

    Args:
        author: :func:`load_f2_authors` 的一项。
        download_root: 下载根目录（传给 f2 的 -p）。
        naming: 命名模板（缺省 :data:`POST_NAMING_TEMPLATE`；覆盖时**必须**保留
            `{aweme_id}`，否则该批素材失去作品 ID）。
        auto_cookie: 浏览器名（chrome / chromium / edge …）。
        interval: 传给 f2 的 `-i` 值：`all` 或 `YYYY-MM-DD|YYYY-MM-DD`。

    Returns:
        可直接交给 subprocess 的参数列表。
    """
    cmd = [
        sys.executable,
        "-m",
        "f2",
        "dy",
        "-u",
        f"https://www.douyin.com/user/{author['sec_user_id']}",
        "-M",
        "post",
        "-i",
        interval or "all",
        "-n",
        naming or POST_NAMING_TEMPLATE,
    ]
    if download_root is not None:
        cmd += ["-p", str(download_root)]
    if auto_cookie:
        cmd += ["--auto-cookie", auto_cookie]
    return cmd


def like_user_url(raw: str) -> str:
    """把「我的主页链接 / sec_user_id」统一成 f2 的 `-u` 取值。

    f2 的点赞/收藏模式要求填**自己的主页链接**；用户常常只粘贴 sec_user_id
    （或整段主页 URL），这里都归一成 URL，避免让用户自己拼。

    Args:
        raw: 主页链接或 sec_user_id（允许含空白）。

    Returns:
        可直接作为 `-u` 的字符串；输入为空时返回空串。
    """
    value = (raw or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    return f"https://www.douyin.com/user/{value}"


"""抖音 sec_user_id 形态：`MS4wLjABAAAA` 开头 + 长串（网页端与 f2 都用它做用户标识）。"""
_SEC_USER_ID_RE = re.compile(r"^MS4wLjABAAAA[\w-]{8,}$")


def profile_author(raw: str) -> dict | None:
    """「博主主页链接 / sec_user_id」→ f2 命令所需的作者字典（昵称可能未知）。

    为什么需要这个入口：f2 的下载目标**只从它自己的用户库取**，库里没有的账号
    根本跑不到。用户给一个博主要「下她全部作品」时，唯一需要的就是她的主页链接
    或 sec_user_id —— 有了它就能让 f2 去认识这个账号。

    支持两种输入：
      - 完整主页链接（可带 query）：`https://www.douyin.com/user/MS4wLjABAAAA…`
      - 裸 sec_user_id：`MS4wLjABAAAA…`

    ⚠ 不接受抖音号（如 `72906514384`）或短链（`v.douyin.com/…`）：前者拼不出主页
    地址，后者要先跟一次跳转才知道落点——两者都会返回 None，由调用方明确报错，
    而不是拼一个必然失败的 URL。

    Args:
        raw: 主页链接或 sec_user_id（允许首尾空白）。

    Returns:
        ``{"sec_user_id", "nickname", "aweme_count"}``（昵称未知留空串）；
        无法识别时返回 None。
    """
    value = (raw or "").strip()
    if not value:
        return None
    if "/user/" in value:
        # 去掉 query（分享链接常带 ?...）再取 /user/ 后面那一段，最后去掉尾斜杠。
        # ⚠ 顺序不能反：先 rstrip 会把 `.../user/` 变成 `.../user`，split 就取不到第二段
        value = value.split("?", 1)[0].split("/user/", 1)[1]
        value = value.rstrip("/").split("/")[0].strip()
    if not _SEC_USER_ID_RE.match(value):
        return None
    return {"sec_user_id": value, "nickname": "", "aweme_count": 0}


def profile_display(author: dict) -> str:
    """作者字典的展示名：昵称优先，昵称未知时用 sec_user_id 前 12 位。"""
    nickname = str(author.get("nickname") or "").strip()
    if nickname:
        return nickname
    return f"（未命名 {str(author.get('sec_user_id') or '')[:12]}…）"


def resolve_profile_nicknames(f2_dir: Path, sec_ids: list[str]) -> dict[str, str]:
    """按 sec_user_id 反查昵称：``{sec_user_id: nickname}``。

    为什么必须**下载后**才查：f2 是「见过一次才写进用户库」，用户在界面上填的
    可能只是主页链接（没有昵称）。下载跑完 f2 已把账号登记进 `douyin_users.db`，
    这时才能拿到昵称——而入库阶段的白名单是按**归一化昵称**匹配的（见
    :func:`build_import_plan` 的 ``authors`` 过滤），所以缺了昵称就会「下载成功、
    入库 0」。

    Args:
        f2_dir: f2 工作目录。
        sec_ids: 待反查的 sec_user_id 列表。

    Returns:
        能查到昵称的 ``{sec_user_id: nickname}``；查不到的键不会出现。
    """
    wanted = {s for s in sec_ids if s}
    if not wanted:
        return {}
    out: dict[str, str] = {}
    for author in load_f2_authors(f2_dir):
        sec = str(author.get("sec_user_id") or "")
        if sec in wanted and author.get("nickname"):
            out[sec] = str(author["nickname"])
    return out


def _build_personal_command(
    user: str,
    mode: str,
    naming: str,
    download_root: Path | None = None,
    auto_cookie: str | None = None,
    max_counts: int = 0,
) -> list[str]:
    """构造「我的列表」类模式（点赞 `like` / 收藏 `collection`）的 f2 命令。

    这两种模式同形，差别只有 mode 与命名模板：

    1. `-M like|collection`：拉**登录账号自己**的列表（只有本人可见，故 `-u` 必须是你的主页）
    2. 必传带 `{nickname}` 的 `-n`：产物统统落在「我的昵称」目录下，原作者与作品 ID
       只能靠文件名保留
    3. **不传 `-i`**：实测 f2 的 `handle_user_like` / `handle_user_collection` 都不读
       `interval`，传进去是无效参数（不是「会漏」的问题，是这个参数在此模式下不存在）。
       真正能收窄翻页量的是 `-o/--max-counts`：分页从 `cursor=0` 一路翻到底、没有
       「遇到已下载就停」，每页还固定 `asyncio.sleep(timeout)`。

    Args:
        user: 我的主页链接或 sec_user_id。
        mode: f2 的 `-M` 取值（`like` / `collection`）。
        naming: `-n` 命名模板。
        download_root: 下载根目录（传给 f2 的 -p）。
        auto_cookie: 浏览器名（chrome / chromium / edge …）。
        max_counts: 最多翻多少条（`-o`）。0 表示全量翻到底。

    Returns:
        可直接交给 subprocess 的参数列表。
    """
    cmd = [
        sys.executable,
        "-m",
        "f2",
        "dy",
        "-u",
        like_user_url(user),
        "-M",
        mode,
        "-n",
        naming,
    ]
    if max_counts > 0:
        cmd += ["-o", str(max_counts)]
    if download_root is not None:
        cmd += ["-p", str(download_root)]
    if auto_cookie:
        cmd += ["--auto-cookie", auto_cookie]
    return cmd


def build_f2_like_command(
    like_user: str,
    download_root: Path | None = None,
    auto_cookie: str | None = None,
    max_counts: int = 0,
) -> list[str]:
    """构造「我的喜欢」（点赞作品）的 f2 命令。

    参数口径见 :func:`_build_personal_command`（点赞 / 收藏同形）。这里单独保留一个
    入口是因为 CLI 与测试都按名字引用它。

    Args:
        like_user: 我的主页链接或 sec_user_id。
        download_root: 下载根目录（传给 f2 的 -p）。
        auto_cookie: 浏览器名（chrome / chromium / edge …）。
        max_counts: 最多翻多少条点赞作品（`-o`）。0 表示全量翻到底。

    Returns:
        可直接交给 subprocess 的参数列表。
    """
    return _build_personal_command(
        like_user,
        "like",
        LIKE_NAMING_TEMPLATE,
        download_root=download_root,
        auto_cookie=auto_cookie,
        max_counts=max_counts,
    )


def build_f2_collect_command(
    collect_user: str,
    download_root: Path | None = None,
    auto_cookie: str | None = None,
    max_counts: int = 0,
) -> list[str]:
    """构造「我的收藏」（抖音收藏列表）的 f2 命令。

    与「我的喜欢」同形（见 :func:`_build_personal_command`），差别只有 `-M collection`
    与命名模板；f2 的收藏接口是 POST + 纯 cookie，同样不支持 `-i` 日期窗口。

    Args:
        collect_user: 我的主页链接或 sec_user_id。
        download_root: 下载根目录（传给 f2 的 -p）。
        auto_cookie: 浏览器名（chrome / chromium / edge …）。
        max_counts: 最多翻多少条收藏作品（`-o`）。0 表示全量翻到底。

    Returns:
        可直接交给 subprocess 的参数列表。
    """
    return _build_personal_command(
        collect_user,
        "collection",
        COLLECT_NAMING_TEMPLATE,
        download_root=download_root,
        auto_cookie=auto_cookie,
        max_counts=max_counts,
    )


def _run_personal_fetch(
    f2_dir: Path,
    user: str,
    mode: str,
    naming: str,
    label: str,
    download_root: Path | None = None,
    auto_cookie: str | None = None,
    max_counts: int = 0,
    runner=None,
) -> dict:
    """拉取「我的列表」（点赞 / 收藏）：单条命令，不逐作者循环。

    Args:
        f2_dir: f2 工作目录（cwd 必须是它，否则另起空作者库、下载落到别处）。
        user: 我的主页链接或 sec_user_id。
        mode: f2 的 `-M` 取值（`like` / `collection`）。
        naming: `-n` 命名模板。
        label: 结果里的展示名（「我的喜欢」/「我的收藏」）。
        download_root: 传给 f2 的 -p 下载根目录。
        auto_cookie: 传给 f2 的 --auto-cookie 浏览器名。
        max_counts: 最多翻多少条（`-o`）。0 = 全量翻到底。
        runner: 可注入的执行器（签名 (cmd, cwd) -> (returncode, info)），便于单测。

    Returns:
        {"total", "ok", "failed", "results", "cmd", "error", "max_counts"}（结构与
        :func:`run_fetch` 对齐，便于复用同一套任务结果展示）。
    """
    runner = runner or _default_runner
    if not (user or "").strip():
        return {
            "total": 0,
            "ok": 0,
            "failed": 0,
            "results": [],
            "cmd": [],
            "error": (
                f"未配置「我的主页链接」：{label}只有本人可见，请先填写你的抖音主页链接"
            ),
        }

    url = like_user_url(user)
    cmd = _build_personal_command(
        user,
        mode,
        naming,
        download_root=download_root,
        auto_cookie=auto_cookie,
        max_counts=max_counts,
    )
    print(f"  ▶ {label}（{url}）")
    if max_counts > 0:
        print(
            f"    · 增量模式：只翻最近 {max_counts} 条（-o {max_counts}）；"
            "已下载过的文件 f2 会跳过，入库侧还有五层判重"
        )
    else:
        print(
            "    · 全量翻页（f2 的分页没有「遇到已下载就停」，每页还要固定等一次"
            " timeout，故慢）；已下载过的文件 f2 会跳过，入库侧还有五层判重"
        )
    try:
        rc, _info = runner(cmd, f2_dir)
    except Exception as exc:  # noqa: BLE001 —— 与 run_fetch 一致：单次失败不抛，交由上层报错
        rc = -1
        print(f"    ✗ 调用 f2 失败：{type(exc).__name__}: {exc}")
    if rc == 0:
        print("    ✓ 完成")
    else:
        print(f"    ✗ 退出码 {rc}，详情见 {f2_dir / 'logs'}")

    return {
        "total": 1,
        "ok": 1 if rc == 0 else 0,
        "failed": 0 if rc == 0 else 1,
        "results": [
            {
                "nickname": label,
                "sec_user_id": url,
                "rc": rc,
                "max_counts": max_counts,
                "cmd": " ".join(cmd),
            }
        ],
        "cmd": cmd,
        "max_counts": max_counts,
    }


def run_fetch_likes(
    f2_dir: Path,
    like_user: str,
    download_root: Path | None = None,
    auto_cookie: str | None = None,
    max_counts: int = 0,
    runner=None,
) -> dict:
    """拉取「我的喜欢」（点赞的作品）（实现见 :func:`_run_personal_fetch`）。"""
    return _run_personal_fetch(
        f2_dir,
        like_user,
        "like",
        LIKE_NAMING_TEMPLATE,
        "我的喜欢",
        download_root=download_root,
        auto_cookie=auto_cookie,
        max_counts=max_counts,
        runner=runner,
    )


def run_fetch_collects(
    f2_dir: Path,
    collect_user: str,
    download_root: Path | None = None,
    auto_cookie: str | None = None,
    max_counts: int = 0,
    runner=None,
) -> dict:
    """拉取「我的收藏」（抖音收藏列表）（实现见 :func:`_run_personal_fetch`）。"""
    return _run_personal_fetch(
        f2_dir,
        collect_user,
        "collection",
        COLLECT_NAMING_TEMPLATE,
        "我的收藏",
        download_root=download_root,
        auto_cookie=auto_cookie,
        max_counts=max_counts,
        runner=runner,
    )


def _default_runner(cmd: list[str], cwd: Path) -> tuple[int, str]:
    """默认执行器：继承标准输出，让 f2 的下载进度实时可见。

    Args:
        cmd: 命令参数列表。
        cwd: 工作目录（必须是 f2 工作目录）。

    Returns:
        (退出码, 附加信息)。
    """
    proc = subprocess.run(cmd, cwd=str(cwd), check=False)
    return proc.returncode, ""


def run_fetch(
    f2_dir: Path,
    authors: set[str] | None = None,
    download_root: Path | None = None,
    naming: str | None = None,
    auto_cookie: str | None = None,
    limit: int | None = None,
    runner=None,
    since_days: int | None = DEFAULT_FETCH_SINCE_DAYS,
    post_root: Path | None = None,
    include_unknown: bool = False,
    profiles: list[str] | None = None,
) -> dict:
    """串行调 f2 下载各作者的作品（一次一个，避免并发触发风控）。

    两种模式：

    1. **增量**（缺省）：遍历 f2 用户库里已登记的账号，按日期窗口只看新作品。
    2. **按博主全量**（传 ``profiles``）：只下这些**显式点名**的博主，且它们
       **不需要先在 f2 用户库里**——这正是「f2 只认自己见过的账号」这个限制的
       出口（见 :func:`profile_author`）。首次采某博主时 `compute_fetch_interval`
       会给出 `all`，即把她全部作品翻一遍。

    设计要点：
    - **cwd 必须是 f2 工作目录**：f2 在 cwd 下读写 douyin_users.db 与 Download/，
      cwd 不对会另起一个空作者库、把下载落到别处
    - **按作者给日期窗口**（`since_days`）：`-i all` 会让 f2 把作者全部历史翻完
      且每页固定 sleep 一次 timeout，实测 84% 的时间耗在翻页等待上；窗口按
      「上次下载时间」自动放大，见 :func:`compute_fetch_interval`
    - 单个作者失败（风控 / 网络 / cookie 失效）只记录并继续下一个，不阻断整批；
      失败细节在 f2/logs/ 下
    - 作者过滤复用 `--authors`（归一化名匹配），与导入阶段的语义一致
    - **默认只下库里已登记的博主**（见 :func:`select_known_authors`）：f2 用户库
      装的是它见过的所有账号，混进来的无关账号（如官方游戏号）不该被下载入库；
      `include_unknown=True` 才恢复「f2 里有谁都下」
    - 传了 ``profiles`` 时**只跑这些博主**，且跳过「已登记博主」白名单（用户已经
      明确点名，白名单在这里只会把目标挡掉）

    Args:
        f2_dir: f2 工作目录。
        authors: 只下载这些作者（归一化名，None 表示全部）。
        download_root: 传给 f2 的 -p 下载根目录。
        naming: 传给 f2 的 -n 命名模板（缺省 :data:`POST_NAMING_TEMPLATE`，含作品 ID）。
        auto_cookie: 传给 f2 的 --auto-cookie 浏览器名。
        limit: 最多下载多少个作者（试跑用）。
        runner: 可注入的执行器（签名 (cmd, cwd) -> (returncode, info)），便于单测。
        since_days: 日期窗口最小天数；None/<=0 表示翻全历史（首次全量）。
        post_root: 作者目录所在位置（缺省按 `download_root`/f2 目录推导）。
        include_unknown: 是否连「未登记到博主库」的 f2 账号一起下载。
        profiles: 显式点名的博主（主页链接 / sec_user_id）。非空时只跑这些。

    Returns:
        {"total", "ok", "failed", "results", "windows", "skipped_authors",
         "invalid_profiles", "error"}；results 每项含
        nickname / sec_user_id / rc / cmd / interval。
    """
    runner = runner or _default_runner

    # 显式点名的博主：先归一化，无法识别的单独收集（由调用方明确报错）
    extra_authors: list[dict] = []
    invalid_profiles: list[str] = []
    for raw in profiles or []:
        parsed = profile_author(raw)
        if parsed is None:
            invalid_profiles.append(str(raw))
        else:
            extra_authors.append(parsed)

    all_authors = load_f2_authors(f2_dir)
    if not all_authors and not extra_authors:
        return {
            "total": 0,
            "ok": 0,
            "failed": 0,
            "results": [],
            "windows": {},
            "skipped_authors": [],
            "invalid_profiles": invalid_profiles,
            "error": (
                f"未找到 f2 作者清单：{f2_dir / F2_AUTHOR_DB}（先手动跑一次 f2 "
                f"确认能登录并下载，本命令只做「增量」）"
            ),
        }

    unknown: list[dict] = []
    if extra_authors:
        # 显式点名：只跑这些，且不做「已登记博主」过滤
        targets = extra_authors
    else:
        if not include_unknown:
            bloggers = load_douyin_bloggers()
            if bloggers:
                # 只有库内登记过抖音博主时才有白名单依据；一个都没有则退回旧口径
                all_authors, unknown = select_known_authors(all_authors, bloggers)

        wanted = {normalize_author(a) for a in authors} if authors is not None else None
        targets = [
            a
            for a in all_authors
            if wanted is None or normalize_author(a["nickname"]) in wanted
        ]
    if limit is not None:
        targets = targets[:limit]

    if unknown:
        print(
            f"  ⏭ 跳过 {len(unknown)} 个未登记到博主库的账号："
            f"{'、'.join(a['nickname'] for a in unknown)}"
            "（要一起处理请加 --include-unknown-authors）"
        )

    root = post_root or (
        download_root / "douyin" / "post" if download_root else f2_dir / F2_DOWNLOAD_SUBDIR
    )
    last_download = author_last_download(root)

    results: list[dict] = []
    windows: Counter = Counter()
    ok = failed = 0
    for author in targets:
        # 昵称未知（用户只给了主页链接、f2 还没登记过这个账号）时用 sec 前 12 位代称
        display = profile_display(author)
        last_at = last_download.get(normalize_author(author["nickname"] or ""))
        # 点名博主用 compute_profile_interval：**首次全量**（否则只会拿到最近
        # since_days 天，用户以为下全了其实没有）；批量增量仍用原口径
        interval = (
            compute_profile_interval(since_days, last_at)
            if extra_authors
            else compute_fetch_interval(since_days, last_at)
        )
        windows[interval] += 1
        cmd = build_f2_command(author, download_root, naming, auto_cookie, interval=interval)
        aweme_count = author.get("aweme_count") or 0
        print(f"  ▶ {display}（抖音作品总数 {aweme_count or '未知'}，窗口 {interval}）")
        try:
            rc, _info = runner(cmd, f2_dir)
        except Exception as exc:  # noqa: BLE001 —— 单个作者失败不阻断整批
            rc = -1
            print(f"    ✗ 调用 f2 失败：{type(exc).__name__}: {exc}")
        if rc == 0:
            ok += 1
            print("    ✓ 完成（窗口内已下载的作品会被 f2 跳过，不会重复下载）")
        else:
            failed += 1
            print(f"    ✗ 退出码 {rc}，跳过该作者（详情见 {f2_dir / 'logs'}）")
        results.append(
            {
                "nickname": author["nickname"],
                "display": display,
                "sec_user_id": author["sec_user_id"],
                "rc": rc,
                "interval": interval,
                "cmd": " ".join(cmd),
            }
        )

    # 下载后按 sec_user_id 反查昵称：新账号此时已被 f2 写进用户库，
    # 入库阶段的白名单按昵称匹配，缺了它就会「下载成功、入库 0」
    resolved: dict[str, str] = {}
    if extra_authors:
        resolved = resolve_profile_nicknames(
            f2_dir, [a["sec_user_id"] for a in extra_authors]
        )

    return {
        "total": len(targets),
        "ok": ok,
        "failed": failed,
        "results": results,
        "windows": dict(windows),
        "skipped_authors": [a["nickname"] for a in unknown],
        "invalid_profiles": invalid_profiles,
        "resolved_profiles": resolved,
    }
