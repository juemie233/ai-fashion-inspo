"""生成 API Key 并引导启用破坏性接口认证。

用法:
    python scripts/generate_api_key.py

说明:
    - 生成随机密钥并输出配置指引（写入 backend/.env 的 API_KEY= 后重启后端生效）
    - 已配置 API_KEY 时仅打印当前密钥状态，不会覆盖
"""

import secrets
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
ENV_FILE = BACKEND_DIR / ".env"


def read_env() -> dict[str, str]:
    """读取 .env 文件为键值对（忽略注释与空行）。"""
    if not ENV_FILE.exists():
        return {}
    result: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def main() -> None:
    env = read_env()
    current = env.get("API_KEY", "").strip()

    if current:
        print("已检测到 API_KEY 配置（为避免误覆盖，未生成新密钥）。")
        print(f"  当前密钥: {current}")
        print("  如需更换，请手动修改 backend/.env 中的 API_KEY 后重启后端。")
        return

    # 生成随机密钥（32 字节 URL 安全编码，约 43 字符）
    key = secrets.token_urlsafe(32)
    print("=" * 60)
    print("已生成新的 API Key:")
    print()
    print(f"  {key}")
    print()
    print("启用步骤：")
    print(f"  1. 将下面一行追加到 {ENV_FILE}：")
    print(f"     API_KEY={key}")
    print("  2. 重启后端服务（scripts/restart.sh 或手动重启 uvicorn）")
    print()
    print("生效后行为：")
    print("  - 破坏性接口（重置、批量删除、清空垃圾桶、去重删除、删除人物等）")
    print("    请求头必须携带 X-API-Key，否则返回 401/403")
    print("  - 读接口与普通写操作（上传、收藏、移入垃圾桶等）不受影响")
    print()
    print("前端使用：")
    print("  - 浏览器控制台执行：localStorage.setItem('apiKey', '<你的密钥>')")
    print("    前端请求会自动附加 X-API-Key 头")
    print("  - 或构建时配置环境变量 VITE_API_KEY")
    print("=" * 60)
    sys.exit(0)


if __name__ == "__main__":
    main()
