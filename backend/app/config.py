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

    # 服务器
    host: str = "0.0.0.0"
    port: int = 18888

    # 数据库
    database_url: str = f"sqlite+aiosqlite:///{_DB_PATH.as_posix()}"

    # 文件存储
    storage_root: Path = Path(__file__).parent.parent / "storage"
    images_dir: Path = storage_root / "images"
    thumbnails_dir: Path = storage_root / "thumbnails"
    videos_dir: Path = storage_root / "videos"

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

    # AI / Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_vision_model: str = "minicpm-v:8b"
    ollama_embedding_model: str = "all-minilm"
    ai_analysis_timeout: int = 300  # 秒（思考型模型推理耗时更长）
    ai_low_confidence_threshold: float = 0.6
    ai_temperature: float = 0.7
    ai_top_p: float = 0.9
    ai_top_k: int = 40
    ai_num_predict: int = 4096  # 思考型模型需为推理预留 token

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
    chrome_executable: str = (
        "C:/Users/Administrator/AppData/Local/Google/Chrome/Application/chrome.exe"
    )  # Chrome 浏览器路径（Windows 用户级安装默认位置）
    chrome_user_data_dir: str = (
        "C:/Users/Administrator/Desktop/chrome-scraper-profile"
    )  # 采集专用 Chrome 用户数据目录
    chrome_debug_port: int = 9222  # Chrome 调试端口

    # 安全
    api_key: str = ""  # API 密钥，为空则跳过认证（开发模式）
    cors_origins: list[str] = [
        "http://localhost:17777",
        "http://127.0.0.1:17777",
    ]

    @property
    def storage_dirs(self) -> dict[str, Path]:
        """返回所有存储目录的映射。"""
        return {
            "images": self.images_dir,
            "thumbnails": self.thumbnails_dir,
            "videos": self.videos_dir,
        }


# 全局单例配置
settings = Settings()

# 尝试从 prompt.txt 加载已持久化的 prompt
_prompt_file = Path(__file__).parent.parent / "prompt.txt"
if _prompt_file.exists():
    try:
        saved_prompt = _prompt_file.read_text(encoding="utf-8").strip()
        if saved_prompt:
            settings.ai_analysis_prompt = saved_prompt
    except Exception:
        pass  # 加载失败时使用默认值
