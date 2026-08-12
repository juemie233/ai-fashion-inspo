"""应用配置：通过 Pydantic Settings 管理所有配置项。"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用设置类，自动从环境变量和 .env 文件加载。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 应用
    app_name: str = "Fashion Inspo"
    app_version: str = "0.1.0"
    debug: bool = True

    # 服务器
    host: str = "0.0.0.0"
    port: int = 8000

    # 数据库
    database_url: str = "sqlite+aiosqlite:///./fashion_inspo.db"

    # 文件存储
    storage_root: Path = Path(__file__).parent.parent / "storage"
    images_dir: Path = storage_root / "images"
    thumbnails_dir: Path = storage_root / "thumbnails"
    videos_dir: Path = storage_root / "videos"

    # 缩略图
    thumbnail_size: tuple[int, int] = (400, 600)
    thumbnail_quality: int = 85

    # AI / Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_vision_model: str = "minicpm-v:8b"
    ollama_embedding_model: str = "all-minilm"
    ai_analysis_timeout: int = 60  # 秒
    ai_low_confidence_threshold: float = 0.6
    ai_temperature: float = 0.7
    ai_top_p: float = 0.9
    ai_top_k: int = 40
    ai_num_predict: int = 2048

    # 采集引擎
    scraper_request_delay: float = 2.0  # 请求间隔（秒）
    scraper_max_concurrent: int = 3
    scraper_browser_headless: bool = True
    chrome_executable: str = (
        "C:/Program Files/Google/Chrome/Application/chrome.exe"
    )  # Chrome 浏览器路径
    chrome_user_data_dir: str = (
        "C:/Users/Administrator/Desktop/chrome-scraper-profile"
    )  # 采集专用 Chrome 用户数据目录
    chrome_debug_port: int = 9222  # Chrome 调试端口

    # 安全
    api_key: str = ""  # API 密钥，为空则跳过认证（开发模式）
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

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
