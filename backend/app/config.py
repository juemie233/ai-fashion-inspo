"""应用配置：通过 Pydantic Settings 管理所有配置项。"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# 数据库文件绝对路径（不依赖进程 CWD，避免从不同目录启动时产生双库）
_DB_PATH = Path(__file__).resolve().parent.parent / "fashion_inspo.db"


class Settings(BaseSettings):
    """应用设置类，自动从环境变量和 .env 文件加载。"""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 应用
    app_name: str = "Fashion Inspo"
    app_version: str = "0.1.0"
    debug: bool = True

    # 服务器（默认仅本机可访问；确需局域网/真机访问时再显式改绑 0.0.0.0）
    host: str = "127.0.0.1"
    port: int = 18888

    # 数据库
    database_url: str = f"sqlite+aiosqlite:///{_DB_PATH.as_posix()}"

    # 文件存储
    storage_root: Path = Path(__file__).parent.parent / "storage"
    images_dir: Path = storage_root / "images"
    thumbnails_dir: Path = storage_root / "thumbnails"
    videos_dir: Path = storage_root / "videos"
    trash_dir: Path = storage_root / "trash"  # 垃圾桶（软删除文件移入此目录）
    # 视频关键帧：子目录按素材 ID 命名（storage/keyframes/{inspiration_id}/frame_001.jpg），
    # 不入库，由 /api/files/keyframes/{id} 按需列目录返回
    keyframes_dir: Path = storage_root / "keyframes"
    # 人物照片（模特写真）：与素材库 images/ 分离，避免被完整性检查误判为孤立文件
    person_photos_dir: Path = storage_root / "person_photos"
    person_thumbnails_dir: Path = storage_root / "person_thumbnails"

    # 上传大小限制（MB）：防止误传超大文件导致内存与磁盘暴涨
    max_image_upload_mb: int = 20  # 图片/缩略图
    max_video_upload_mb: int = 500  # 视频

    # 向量检索（LanceDB 嵌入式向量库）
    lancedb_dir: Path = storage_root / "lancedb"  # LanceDB 数据目录（文件落盘，可随项目迁移）
    lancedb_text_table: str = "text_vectors"  # 文本向量表
    lancedb_image_table: str = "image_vectors"  # 图像向量表
    lancedb_text_dim: int = 384  # 文本向量维度（Ollama all-minilm 输出 384 维）
    lancedb_image_dim: int = 512  # 图像向量维度（CLIP ViT-B/32 输出 512 维）
    clip_model_name: str = "clip-ViT-B-32"  # 图像向量模型（sentence-transformers / open_clip 均可加载）
    vector_top_k_default: int = 20  # 向量搜索默认 TopK
    vector_similarity_weight: float = 0.6  # 混合排序：视觉相似度权重
    vector_tag_weight: float = 0.4  # 混合排序：标签匹配权重

    # 缩略图
    thumbnail_size: tuple[int, int] = (400, 600)
    thumbnail_quality: int = 85

    # 视频关键帧提取（ffmpeg）
    keyframe_interval_seconds: float = 3.0  # 固定间隔抽帧间隔（秒）
    keyframe_scene_threshold: float = 0.0  # 场景检测阈值（0=禁用；如 0.3 表示画面变化 >30% 时抽帧）
    keyframe_max_frames: int = 60  # 单视频关键帧数量上限（防长视频刷爆磁盘）
    face_scan_video_max_frames: int = 3  # 人脸扫描每个视频取前 N 帧

    # AI / Ollama
    ollama_base_url: str = "http://localhost:11434"
    # 默认视觉模型与 README 推荐一致（Qwen3-VL:8B-Instruct）；
    # 可在 .env 用 OLLAMA_VISION_MODEL 覆盖，或在「AI 模型管理」页切换
    ollama_vision_model: str = "qwen3-vl:8b-instruct"
    ollama_embedding_model: str = "all-minilm"
    ai_analysis_timeout: int = 300  # 秒（思考型模型推理耗时更长）
    ai_low_confidence_threshold: float = 0.6
    ai_temperature: float = 0.7
    ai_top_p: float = 0.9
    ai_top_k: int = 40
    ai_num_predict: int = 4096  # 思考型模型需为推理预留 token
    # Ollama 推理上下文窗口：视觉模型对高分辨率图片编码消耗大量 token
    # （实测一张 1.8MB 图片占约 4000 token），Ollama 默认 4096 会把 JSON 输出硬性截断，
    # 必须显式传入更大的 num_ctx
    ai_num_ctx: int = 16384

    # 质量审核
    manual_upload_auto_approve: bool = True  # 手动上传默认免审核（直接标记为已通过）
    ai_generated_confidence_threshold: float = 0.8  # AI 生成检测置信度阈值，仅 ≥ 此值才标记「疑似 AI」

    # 标签命名质量
    # 标签名超过该字数判定为「低质命名（过长）」：标签健康度扫描与 AI 打标提取共用同一阈值，
    # 可在 .env 用 TAG_NAME_MAX_LENGTH 覆盖
    tag_name_max_length: int = 12

    # 人脸识别子服务（face-service：独立 Python 3.10 环境运行 insightface）
    # 主后端 3.12 不兼容 insightface，人脸能力通过 HTTP 调用子服务；
    # 留空表示未部署子服务，人脸相关功能自动降级/不可用
    face_service_url: str = "http://127.0.0.1:18889"
    face_service_timeout: float = 30.0  # 子服务调用超时（秒）
    face_match_threshold: float = 0.5  # 模特人脸匹配余弦相似度阈值（建议 0.45~0.55，按样本调整）

    # 垃圾桶（软删除）
    # 0 表示禁用自动回收：垃圾桶素材永不自动清理，仅可手动恢复或彻底删除。
    # 如需恢复「到期自动清理」，在 .env 设置 TRASH_RETENTION_DAYS=30 等正整数。
    trash_retention_days: int = 0

    # 任务队列并发（worker 为独立进程 python -m app.worker）
    # worker 同时执行的任务数：值越大整体吞吐越高，但 Ollama 显存压力与
    # SQLite 写锁竞争也随之增大（多任务同时写进度/心跳易触发 database is locked），
    # 建议保持 1~2；仅多卡/强机器再尝试更大值。可在 .env 用 WORKER_CONCURRENCY 覆盖
    worker_concurrency: int = 1
    # 批内分析并发度：批量分析/组合分析/质量审核任务内部同时分析的素材数
    # （原写死常量 _ANALYZE_CONCURRENCY=1）。注意 worker 与 API 进程
    # （routers/ai_shared.py 的独立信号量=2）互不感知，最坏并发出路为
    # worker_concurrency × 本项 + API 侧 2 路；显存吃紧时优先调小本项，建议 1~2。
    # 可在 .env 用 ANALYZE_CONCURRENCY 覆盖
    analyze_concurrency: int = 1

    # 负样本初筛器（阶段 2：CLIP 向量 + sklearn 轻量分类器）
    quality_classifier_threshold: float = 0.9  # 自动拒绝的置信度阈值（宁缺毋滥，低置信度仍走 VLM 复审）

    # AI 分析 Prompt（运行时可变，前端可编辑）
    ai_analysis_prompt: str = (
        "你是一个专业的时尚穿搭分析助手。请分析这张穿搭图片，提取以下维度的标签：\n\n"
        "1. 风格体系：JK制服/汉服/Lolita/Y2K/CleanFit/法式/日系/韩系/学院风/街头/新中式/复古/极简/美式复古/英伦风/波西米亚/运动风/甜美风/暗黑风\n"
        "   （可以输出多个风格标签，如果没有明显风格可以不输出）\n\n"
        "2. 单品识别：识别图中每一件主要服饰单品，包括类型+颜色+特征。\n"
        '   格式：{"type": "单品类型", "color": "颜色", "features": ["特征1", "特征2"]}\n\n'
        "3. 版型：宽松/修身/Oversized/直筒/紧身/A字/H型/喇叭/锥形/阔腿\n\n"
        "4. 穿着方式/身体部位关系：过膝/露腰/高腰/V领/圆领/高领/一字肩/七分袖/长袖/短袖/无袖/拖地/迷你/中长款/长款/短款\n\n"
        "5. 图片属性：露脸/不露脸/全身/半身/坐姿/站姿/对镜自拍/他拍/叠穿/单穿/街拍/棚拍\n\n"
        "6. 主色调提取：提取2-3个主要颜色（返回hex值）\n\n"
        "请以JSON格式输出，不要包含任何其他文字：\n"
        '{\n  "style": [],\n  "items": [{"type": "", "color": "", "features": []}],\n'
        '  "fit": [],\n  "wear_style": [],\n'
        '  "attributes": [],\n  "dominant_colors": []\n}'
    )

    # 采集引擎
    scraper_request_delay: float = 2.0  # 请求间隔（秒）
    scraper_max_concurrent: int = 3
    scraper_default_max_count: int = 20  # 每次采集默认数量（降低单次规模以规避风控）
    scraper_browser_headless: bool = True
    # Chrome 路径（Windows 用户级安装默认位置）；留空时自动探测常见安装路径
    chrome_executable: str = ""
    chrome_user_data_dir: str = ""  # 采集专用 Chrome 用户数据目录；留空使用默认目录
    chrome_debug_port: int = 9222  # Chrome 调试端口
    chrome_auto_restart_limit: int = 3  # Chrome 崩溃自动重启次数上限
    chrome_idle_timeout: int = 600  # 无活动采集任务时的空闲自动关闭秒数（0=禁用）
    chrome_startup_timeout: int = 20  # 启动就绪轮询超时（秒）
    scraper_task_auto_retry: int = 2  # 采集任务崩溃自动续采次数上限

    # 安全
    api_key: str = ""  # API 密钥，为空则跳过认证（开发模式）
    cors_origins: list[str] = [
        "http://localhost:17777",
        "http://127.0.0.1:17777",
    ]

    # 启动后自动补备（与每日 03:00 的 schtasks 双通道，backup.lock 互斥）
    backup_on_startup: bool = True  # .env 设 BACKUP_ON_STARTUP=false 关闭
    backup_target_path: str = "E:/fashion-inspo-backups"  # 备份目标根目录
    backup_startup_delay_minutes: int = 10  # 启动后延迟多久再检查（避开迁移/初始化竞争）
    backup_min_interval_hours: int = 20  # 距上次成功备份小于此时长则跳过
    backup_tick_hours: int = 6  # 常驻循环的检查周期

    @property
    def storage_dirs(self) -> dict[str, Path]:
        """返回所有存储目录的映射。"""
        return {
            "images": self.images_dir,
            "thumbnails": self.thumbnails_dir,
            "videos": self.videos_dir,
            "keyframes": self.keyframes_dir,
            "trash": self.trash_dir,
            "person_photos": self.person_photos_dir,
            "person_thumbnails": self.person_thumbnails_dir,
        }
    
    @property
    def config_constants(self) -> "ConfigConstants":
        """返回常量配置实例。

        注解使用字符串前向引用：ConfigConstants 定义在本类之后，
        且全局实例在模块尾部才创建，属性体在首次访问时才会求值。
        """
        return config_constants


# 全局单例配置
settings = Settings()


class ConfigConstants:
    """常量配置类，从配置文件中提取硬编码值。"""
    
    def __init__(self, settings_obj):
        self.settings = settings_obj
    
    # AI 相关常量
    @property
    def ai_temperature(self):
        return getattr(self.settings, 'ai_temperature', 0.7)
    
    @property
    def ai_top_p(self):
        return getattr(self.settings, 'ai_top_p', 0.9)
    
    @property
    def ai_top_k(self):
        return getattr(self.settings, 'ai_top_k', 40)
    
    @property
    def ai_num_predict(self):
        return getattr(self.settings, 'ai_num_predict', 4096)
    
    @property
    def ai_num_ctx(self):
        return getattr(self.settings, 'ai_num_ctx', 16384)
    
    @property
    def ai_low_confidence_threshold(self):
        return getattr(self.settings, 'ai_low_confidence_threshold', 0.6)
    
    @property
    def ai_generated_confidence_threshold(self):
        return getattr(self.settings, 'ai_generated_confidence_threshold', 0.8)
    
    @property
    def ai_analysis_prompt(self):
        # 如果没有从配置中加载，使用默认值
        return getattr(self.settings, 'ai_analysis_prompt', (
            "你是一个专业的时尚穿搭分析助手。请分析这张穿搭图片，提取以下维度的标签：\n\n"
            "1. 风格体系：JK制服/汉服/Lolita/Y2K/CleanFit/法式/日系/韩系/学院风/街头/新中式/复古/极简/美式复古/英伦风/波西米亚/运动风/甜美风/暗黑风\n"
            "   （可以输出多个风格标签，如果没有明显风格可以不输出）\n\n"
            "2. 单品识别：识别图中每一件主要服饰单品，包括类型+颜色+特征。\n"
            '   格式：{"type": "单品类型", "color": "颜色", "features": ["特征1", "特征2"]}\n\n'
            "3. 版型：宽松/修身/Oversized/直筒/紧身/A字/H型/喇叭/锥形/阔腿\n\n"
            "4. 穿着方式/身体部位关系：过膝/露腰/高腰/V领/圆领/高领/一字肩/七分袖/长袖/短袖/无袖/拖地/迷你/中长款/长款/短款\n\n"
            "5. 图片属性：露脸/不露脸/全身/半身/坐姿/站姿/对镜自拍/他拍/叠穿/单穿/街拍/棚拍\n\n"
            "6. 主色调提取：提取2-3个主要颜色（返回hex值）\n\n"
            "请以JSON格式输出，不要包含任何其他文字：\n"
            '{\n  "style": [],\n  "items": [{"type": "", "color": "", "features": []}],\n'
            '  "fit": [],\n  "wear_style": [],\n'
            '  "attributes": [],\n  "dominant_colors": []\n}'
        ))
    
    # 向量检索常量
    @property
    def vector_top_k_default(self):
        return getattr(self.settings, 'vector_top_k_default', 20)
    
    @property
    def vector_similarity_weight(self):
        return getattr(self.settings, 'vector_similarity_weight', 0.6)
    
    @property
    def vector_tag_weight(self):
        return getattr(self.settings, 'vector_tag_weight', 0.4)
    
    @property
    def text_vector_dim(self):
        return getattr(self.settings, 'lancedb_text_dim', 384)
    
    @property
    def image_vector_dim(self):
        return getattr(self.settings, 'lancedb_image_dim', 512)
    
    # 图片处理常量
    @property
    def thumbnail_size(self):
        return getattr(self.settings, 'thumbnail_size', (400, 600))
    
    @property
    def thumbnail_quality(self):
        return getattr(self.settings, 'thumbnail_quality', 85)
    
    @property
    def max_image_upload_mb(self):
        return getattr(self.settings, 'max_image_upload_mb', 20)
    
    @property
    def max_video_upload_mb(self):
        return getattr(self.settings, 'max_video_upload_mb', 500)
    
    # 标签常量
    @property
    def tag_name_max_length(self):
        return getattr(self.settings, 'tag_name_max_length', 12)
    
    @property
    def seed_tags(self):
        return getattr(self.settings, 'seed_tags', [
            "JK制服", "汉服", "Lolita", "Y2K", "CleanFit", "法式", "日系", "韩系", 
            "学院风", "街头", "新中式", "复古", "极简", "美式复古", "英伦风", 
            "波西米亚", "运动风", "甜美风", "暗黑风"
        ])
    
    # 质量审核常量
    @property
    def quality_classifier_threshold(self):
        return getattr(self.settings, 'quality_classifier_threshold', 0.9)
    
    @property
    def manual_upload_auto_approve(self):
        return getattr(self.settings, 'manual_upload_auto_approve', True)
    
    # 人脸识别常量
    @property
    def face_service_timeout(self):
        return getattr(self.settings, 'face_service_timeout', 30.0)
    
    @property
    def face_match_threshold(self):
        return getattr(self.settings, 'face_match_threshold', 0.5)
    
    # 任务队列常量
    @property
    def poll_interval(self):
        return getattr(self.settings, 'poll_interval', 1.0)
    
    @property
    def heartbeat_interval(self):
        return getattr(self.settings, 'heartbeat_interval', 10.0)
    
    @property
    def stale_heartbeat_threshold(self):
        return getattr(self.settings, 'stale_heartbeat_threshold', 90.0)
    
    # 爬虫常量
    @property
    def scraper_request_delay(self):
        return getattr(self.settings, 'scraper_request_delay', 2.0)
    
    @property
    def scraper_max_concurrent(self):
        return getattr(self.settings, 'scraper_max_concurrent', 3)
    
    @property
    def scraper_default_max_count(self):
        return getattr(self.settings, 'scraper_default_max_count', 20)
    
    @property
    def scraper_browser_headless(self):
        return getattr(self.settings, 'scraper_browser_headless', True)
    
    @property
    def chrome_debug_port(self):
        return getattr(self.settings, 'chrome_debug_port', 9222)
    
    @property
    def chrome_auto_restart_limit(self):
        return getattr(self.settings, 'chrome_auto_restart_limit', 3)
    
    @property
    def chrome_idle_timeout(self):
        return getattr(self.settings, 'chrome_idle_timeout', 600)
    
    @property
    def chrome_startup_timeout(self):
        return getattr(self.settings, 'chrome_startup_timeout', 20)
    
    @property
    def task_auto_retry(self):
        return getattr(self.settings, 'task_auto_retry', 2)


# 创建配置常量实例
config_constants = ConfigConstants(settings)

# ── Chrome 路径自动探测 ──
# 配置留空时，按常见安装位置探测 Chrome 可执行文件；用户数据目录使用默认位置。
def _detect_chrome_executable() -> str:
    """探测常见安装位置的 Chrome 可执行文件，未找到返回空字符串。"""
    import os

    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ""


if not settings.chrome_executable:
    detected = _detect_chrome_executable()
    if detected:
        settings.chrome_executable = detected

if not settings.chrome_user_data_dir:
    import os

    default_dir = os.path.expandvars(r"%LOCALAPPDATA%\chrome-scraper-profile")
    if not default_dir.startswith(r"%"):
        settings.chrome_user_data_dir = default_dir
    else:
        # 非 Windows 环境：使用用户主目录
        settings.chrome_user_data_dir = str(Path.home() / "chrome-scraper-profile")

# 尝试从 prompt.txt 加载已持久化的 prompt
_prompt_file = Path(__file__).parent.parent / "prompt.txt"
if _prompt_file.exists():
    try:
        saved_prompt = _prompt_file.read_text(encoding="utf-8").strip()
        if saved_prompt:
            settings.ai_analysis_prompt = saved_prompt
    except Exception:
        pass  # 加载失败时使用默认值
