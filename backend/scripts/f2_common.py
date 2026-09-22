"""f2 素材文件名解析与目录扫描（自 scripts/import_f2_downloads.py 拆出，行为不变）。"""

import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# 与 backend/scripts 下其它脚本一致：把 backend 加入 sys.path，便于模块方式执行
sys.path.insert(0, str(Path(__file__).parent.parent))


# ═══════════════════════════════════════════════════════════════
#  常量
# ═══════════════════════════════════════════════════════════════

"""f2 默认工作目录（可用 --f2-dir 覆盖）。

f2 在**自己的工作目录**下创建 `Download/` 与 `douyin_users.db`（相对路径），
所以调用它时必须把 cwd 设到该目录，作者清单也从这里的 DB 读。
"""
DEFAULT_F2_DIR = Path(r"C:\Users\Administrator\Desktop\f2")

"""下载产物相对 f2 工作目录的位置（f2 在 cwd 下建 Download/douyin/post/）。"""
F2_DOWNLOAD_SUBDIR = Path("Download/douyin/post")

"""f2 的作者库文件名（含 user_info_web 表：sec_user_id / nickname / aweme_count）。"""
F2_AUTHOR_DB = "douyin_users.db"

"""f2 默认下载根目录（可用 --root 覆盖；与 --f2-dir 联动）。"""
DEFAULT_F2_ROOT = DEFAULT_F2_DIR / F2_DOWNLOAD_SUBDIR

"""f2 点赞（喜欢）模式的产物根目录。

f2 的目录规则是 `{path}/douyin/{mode}/{nickname}/`，而点赞模式的目标用户是
**你自己**——所有喜欢的作品都下在「我的昵称」这一个目录下，原作者只存在于
文件名里（见 :data:`LIKE_NAMING_TEMPLATE`）。
"""
F2_LIKE_SUBDIR = Path("Download/douyin/like")

DEFAULT_F2_LIKE_ROOT = DEFAULT_F2_DIR / F2_LIKE_SUBDIR

"""f2 收藏模式的产物根目录（`-M collection`）。

与点赞模式同形：f2 的目录规则是 `{path}/douyin/{mode}/{nickname}/`，收藏的目标用户也是
**你自己**——所有收藏的作品都下在「我的昵称」这一个目录下，原作者只存在于文件名里
（见 :data:`COLLECT_NAMING_TEMPLATE`）。f2 的 mode 目录名就是 `collection`（实测
`handler.handle_user_collection` → `get_or_add_user_data` 拼 mode 子目录）。
"""
F2_COLLECT_SUBDIR = Path("Download/douyin/collection")

DEFAULT_F2_COLLECT_ROOT = DEFAULT_F2_DIR / F2_COLLECT_SUBDIR

"""发布模式的命名模板：**必须带 `{aweme_id}`**，否则素材失去真实作品 ID。

不传 `-n` 时 f2 用配置里的 `{create}_{desc}`——文件名里没有作品 ID，导入后
`source_platform_id` 只能用「文件名算出来的合成哈希」、`source_url` 只能留空，
于是历史素材一条都点不回抖音原帖（见 TODO「f2 素材可追溯」）。

`{create}` 固定 19 字符（`YYYY-MM-DD HH-MM-SS`）、`{aweme_id}` 固定 19 位数字，
两者都含数字，解析靠「19 位数字 + 紧随类型标记」锚定（见 :data:`KIND_RE_WITH_ID`）。
"""
POST_NAMING_TEMPLATE = "{create}_{desc}_{aweme_id}"

"""点赞模式必须传的命名模板：带 `{nickname}` 才能把**原作者**留在文件名里。

不传时 f2 用配置里的 `{create}_{desc}`，文件落到「我的昵称」目录下后就再也
认不出原作者了——素材的来源作者会全变成你自己、也绑不到原作者博主。
`{aweme_id}` 的作用同发布模式：真实作品 ID（同一作品从主页与点赞两条路进来
时，靠它而不是「作者+时间+正文」对齐，判重更稳）。
"""
LIKE_NAMING_TEMPLATE = "{nickname}_{create}_{desc}_{aweme_id}"

"""收藏模式必须传的命名模板：与点赞模式同形（`{nickname}` 保留原作者 + 真实作品 ID）。

收藏列表（`-M collection`）同样把作品全下在**你自己**的昵称目录下，原作者与作品 ID
只能靠文件名保留；理由与 :data:`LIKE_NAMING_TEMPLATE` 完全一致（判重、绑博主、
点回原帖）。同一作品既被点赞又被收藏时，两个入口算出的作品键与平台 ID 一致，
入库侧的五层判重才会挡住重复。
"""
COLLECT_NAMING_TEMPLATE = "{nickname}_{create}_{desc}_{aweme_id}"

"""「我的喜欢」入库后自动登记的博主所用的 `bloggers.source` 取值。

为什么需要这个标记：点赞列表天然跨作者（实测 3452 个文件涉及 585 个原作者，
其中只有 20 个已在博主库），入库时把这些来源作者补建成博主能让素材归属清楚；
但**它们不该进 `一键获取素材` 的下载白名单**——否则下次增量下载会去翻 565 个
主页的全部历史（f2 每页固定 sleep，量级是小时/天，且风控风险高）。
:func:`load_douyin_bloggers` 默认把这些账号排除在「已登记博主」之外，
用户在博主列表里确认后可以「纳入追踪」（source 改回 manual）。
"""
AUTO_BLOGGER_SOURCE = "auto_collect"

"""传给 f2 的日期窗口缺省天数（`-i`）。

为什么必须给窗口：f2 的 `handle_user_post` 只在传了日期区间时才设 `min_cursor`，
`-i all` 时 `min_cursor = 0`，那句「翻到范围起点就 break」永不触发——于是它会把
作者**全部历史**翻一遍，而每翻一页（`page_counts` 默认 20）固定 sleep 一次
`timeout`（本机配置 10 秒）。实测单个 376 作品作者的 263 秒里 220 秒（84%）花在
这个翻页 sleep 上，真正下载只有 36 个文件。给窗口后 f2 翻过起点即停。
"""
DEFAULT_FETCH_SINCE_DAYS = 14

"""窗口起点的回退余量（天）：避免「刚下载完就跨零点」把当天的作品漏在窗口外。"""
WINDOW_MARGIN_DAYS = 1

"""文件名尾部类型标记：_video / _image_1 / _live_2（f2 命名模板决定）。

两种命名都要认（同一作品从不同入口下载时，作品键必须一致才能判重）：
- **发布模式**（post）：`{create}_{desc}_image_1.webp`——作者来自所在目录名
- **点赞/收藏模式**（like）：f2 把喜欢的作品**统统下在「我的昵称」目录下**，
  原作者只存在于文件名里，故命名模板带 `{nickname}`，形如
  `不养羊_2026-09-14 10-31-14_下一站再见吧#jk_#jk_image_1.webp`
  （前缀为可选；作者名自身含下划线也能正确回溯，如 `美羊羊桑__2026-…`）
"""
KIND_RE = re.compile(
    r"^(?:(?P<author>.+?)_)?"
    r"(?P<created>\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2})_"
    r"(?P<body>.+?)_(?P<kind>video|image|live|music|cover|lyric)"
    r"(?:_(?P<index>\d+))?$",
    re.I,
)

"""带真实作品 ID 的命名（新模板，见 :data:`POST_NAMING_TEMPLATE` / `LIKE_NAMING_TEMPLATE`）。

作品 ID 是**固定 19 位数字**且紧跟类型标记，于是锚定 `_{19位数字}_{类型}` 就能
与正文里的数字区分开。

⚠ 尝试顺序必须是「先新后旧」：旧模板正则会把 `_1234567890123456789` 当成正文的
一部分，同一作品会算出两个作品键 → 重复入库。

⚠ 正文用 `.*?`（允许为空）而不是 `.+?`：作品的 `{desc}` 可能为空，此时文件名是
`{create}__{aweme_id}_{kind}`（连续两个下划线）——要求正文非空会让这类产物落回旧
正则，被当成「正文里含作品 ID 的旧文件」，**静默丢掉作品 ID 与原帖链接**（实测）。
放宽只多匹配「正文为空」这一种情形：ID 前面仍必须有分隔下划线，因此正文以数字收尾
（无分隔）的旧文件依旧走旧口径。
"""
KIND_RE_WITH_ID = re.compile(
    r"^(?:(?P<author>.+?)_)?"
    r"(?P<created>\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2})_"
    r"(?P<body>.*?)_(?P<aweme_id>\d{19})"
    r"_(?P<kind>video|image|live|music|cover|lyric)"
    r"(?:_(?P<index>\d+))?$",
    re.I,
)

"""f2 下载未完成时留下的临时文件扩展名（含 0 字节残file，必须跳过）。"""
TMP_EXT = ".tmp"

"""作品类型 → 素材库 media_type。"""
KIND_TO_MEDIA = {
    "image": "image",
    "video": "video",
    "live": "video",
    "cover": "image",
    "music": None,  # 原声不入素材库
    "lyric": None,
}

"""正文里的 #话题（f2 用下划线分隔话题，故以 _ 或空白作边界）。"""
HASHTAG_RE = re.compile(r"#([^\s#_，,。！？.!?]{1,30})")

"""实测打标平均耗时（秒/张），取自 ai_analysis_log；可用 --sec-per-tag 覆盖。"""
DEFAULT_SEC_PER_TAG = 12.5

"""图集「逐图打标」还是「按作品打一次」的分档阈值（张）。"""
BIG_GALLERY_THRESHOLD = 10


# ═══════════════════════════════════════════════════════════════
#  文件名解析（纯函数，便于单测）
# ═══════════════════════════════════════════════════════════════


@dataclass
class ParsedFile:
    """一个已解析的 f2 产物文件。"""

    path: Path
    author_dir: str  # 作者目录名（原始，可能带 √ / 数字后缀）
    author_key: str  # 归一化作者名（用于匹配库内博主、合并被拆分的目录）
    work_key: str  # 作品键（作者 + 创建时间 + 正文）——同一作品的多个文件共享
    created: str  # 创建时间（YYYY-MM-DD HH-MM-SS）
    body: str  # 正文原文（含 #话题，f2 用 _ 替代非法字符）
    kind: str  # image / video / live / ...
    index: int  # 图集序号（无序号为 0）
    media_type: str  # 入库用 media_type（image / video）；空串表示不入库
    size: int = 0
    aweme_id: str = ""  # 真实作品 ID（新命名才有；旧命名的历史文件为空串）

    @property
    def caption(self) -> str:
        """正文转成 caption：下划线还原为空格，压缩连续空白。"""
        return re.sub(r"\s+", " ", self.body.replace("_", " ")).strip()

    @property
    def hashtags(self) -> list[str]:
        """正文里的 #话题列表（去重保序）。"""
        seen: list[str] = []
        for tag in HASHTAG_RE.findall(self.body):
            tag = tag.strip()
            if tag and tag not in seen:
                seen.append(tag)
        return seen


def normalize_author(name: str) -> str:
    """归一化作者目录名，用于跨目录合并与库内博主匹配。

    f2 遇到同名作者会拆出 `里香1√` / `里香2√` 这样的目录，并在下载完成的
    账号后加 `√`；库里的博主名则可能是 `美羊羊桑_`，而清单/昵称里写的是
    `美羊羊桑～`。规则：去掉尾部的 `√`、数字、下划线、点、空白与**波浪号**
    （半角 `~` / 全角 `～` / 波线 `〜`，抖音昵称常用它做装饰），其余保留。

    实测踩点：只剥 `√`/数字时会漏掉 `美羊羊桑～`，导致回填博主时把同一人
    当成新博主建出重复记录。

    Args:
        name: 作者目录名（或库内博主名 / 昵称）。

    Returns:
        归一化后的名字（可能为空串）。
    """
    return re.sub(r"[√\s_\.0-9~～〜]+$", "", (name or "").strip())


def parse_media_filename(path: Path, author_dir: str = "") -> ParsedFile | None:
    """解析 f2 产物文件名；不符合命名规则（残file等）返回 None。

    作者归属：文件名带作者前缀时（点赞模式）以**文件名里的原作者**为准，
    否则回落到所在目录名（发布模式的 `post/{作者}/`）。

    命名兼容两种模板（**先新后旧**，顺序不可颠倒）：

    - 新（带作品 ID）：`{create}_{desc}_{aweme_id}_{kind}_{index}`
    - 旧（历史文件）：`{create}_{desc}_{kind}_{index}`

    旧文件仍要能解析——`Download/` 目录里新旧混放，且重跑历史批次不能因为
    模板变更而失效。

    Args:
        path: 文件路径。
        author_dir: 所属作者目录名（缺省取 path 的父目录名）。

    Returns:
        ParsedFile；文件名不含 f2 类型标记时返回 None。
    """
    if path.suffix.lower() == TMP_EXT:
        return None
    match = KIND_RE_WITH_ID.match(path.stem)
    if not match:
        match = KIND_RE.match(path.stem)
    if not match:
        return None
    kind = match.group("kind").lower()
    media_type = KIND_TO_MEDIA.get(kind)
    if media_type is None:
        return None  # 原声/歌词等不属素材
    author = match.group("author") or author_dir or path.parent.name
    created = match.group("created")
    body = match.group("body")
    return ParsedFile(
        path=path,
        author_dir=author,
        author_key=normalize_author(author),
        work_key=f"{author}||{created}||{body}",
        created=created,
        body=body,
        kind=kind,
        index=int(match.group("index") or 0),
        media_type=media_type,
        size=path.stat().st_size if path.exists() else 0,
        aweme_id=match.groupdict().get("aweme_id") or "",
    )


def scan_directory(root: Path) -> list[ParsedFile]:
    """递归扫描 f2 下载目录，返回可入库的已解析文件列表。

    Args:
        root: f2 的 post 目录（…/Download/douyin/post）。

    Returns:
        解析成功的文件列表（残file、非媒体文件、无法识别命名的文件被跳过）。
    """
    parsed: list[ParsedFile] = []
    if not root.exists():
        return parsed
    for author_path in sorted(p for p in root.iterdir() if p.is_dir()):
        for file_path in sorted(author_path.iterdir()):
            if not file_path.is_file():
                continue
            item = parse_media_filename(file_path, author_path.name)
            if item is not None:
                parsed.append(item)
    return parsed


def download_tree_stats(root: Path) -> dict:
    """统计下载目录里的文件数与总字节数（供「我的喜欢」下载阶段展示实时进度）。

    「我的喜欢」要全量翻页才知道有多少个赞，下载期间进度条没有真分母；能作为
    「在干活」证据的是**已经落盘的文件**——f2 每下完一个作品就写文件，这个函数
    就是那个真值（比解析文件名的 :func:`scan_directory` 便宜得多，故可高频调用）。

    Args:
        root: 下载目录（发布模式给 post 根，点赞模式给 like 根）。

    Returns:
        {"files": 文件数, "bytes": 总字节数}；目录不存在时全 0。
    """
    files = 0
    total = 0
    if not root.exists():
        return {"files": 0, "bytes": 0}
    stack = [root]
    while stack:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue  # 目录被 f2 挪动/清理（Windows 上偶发）：本轮跳过，下轮再统计
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    files += 1
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue  # 文件正在写入/已被删除：不计入，避免打断统计
    return {"files": files, "bytes": total}


def group_works(files: list[ParsedFile]) -> dict[str, list[ParsedFile]]:
    """按作品键分组（同一作品的多图/多段视频归到一组）。

    Args:
        files: 已解析文件列表。

    Returns:
        作品键 → 该作品的文件列表（组内按类型/序号排序，保证首图稳定）。
    """
    works: dict[str, list[ParsedFile]] = defaultdict(list)
    for item in files:
        works[item.work_key].append(item)
    for items in works.values():
        items.sort(key=lambda f: (f.kind != "image", f.index, f.path.name))
    return dict(works)
