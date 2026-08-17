#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档翻译脚本：把中文源文档翻译为英文文档（.en.md）。

设计原则：
- 中文文档是源（README.md、docs/*.md），英文文档是「手动执行本脚本后生成的产物」。
- 翻译引擎：本地 Ollama 语言模型（无需 API key、数据不出本机）。

用法（在项目根目录执行）：
    python scripts/translate_docs.py                    # 翻译全部中文源文档
    python scripts/translate_docs.py --file README.md   # 只翻译指定文件
    python scripts/translate_docs.py --force            # 忽略已存在英文版，强制重译
    python scripts/translate_docs.py --model qwen2.5:14b  # 指定模型（默认 qwen2.5:7b）

依赖：
- Ollama 已启动（默认 http://localhost:11434）
- 已拉取语言模型：ollama pull qwen2.5:7b
- 仅使用 Python 标准库（urllib），无需安装额外依赖
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# 默认配置
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b"
ROOT = Path(__file__).resolve().parents[1]

# 待翻译的中文源文档（相对项目根目录）；新增文档时在此追加
# 注意：中文源文件名以仓库实际文件名为准（docs/ 下为中文名）
SOURCE_DOCS = [
    "README.md",
    "docs/CLIP编码说明.md",
    "docs/代码审计报告.md",
    "docs/系统评估报告-开源组件引入.md",
    "docs/项目情况说明.md",
]

# 每段翻译的最大字符数（控制上下文长度，避免超出模型窗口）
CHUNK_SIZE = 2000

SYSTEM_PROMPT = """You are a professional technical documentation translator.
Translate the given Chinese markdown document into English, following these rules strictly:
1. Faithfully translate every paragraph; do not add, omit, or change technical facts.
2. Keep unchanged: code blocks, commands, file/directory paths, URLs, table/column/field names,
   variable names, enum values (e.g. manual_upload, xiaohongshu, browser_extension), API endpoints,
   model names, numbers, and configuration values.
3. Preserve the markdown structure exactly: headings, lists, tables (same number of columns),
   blockquotes, code fences, and link paths (only translate the link display text, never the path).
4. Keep Chinese data values (e.g. tag names like JK制服, trash reasons like 质量差) as-is,
   because they are real data stored in the database.
5. Use standard English technical terms (soft delete, trash, inspiration, scraping, etc.).
6. Output ONLY the translated markdown, no explanations, no code fences around it."""


def ollama_request(url: str, payload: dict) -> dict:
    """发送请求到 Ollama API，返回 JSON 响应。"""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_ollama(base_url: str, model: str) -> None:
    """检查 Ollama 可用性与模型是否存在。"""
    try:
        tags = ollama_request(f"{base_url}/api/tags", {})
    except (urllib.error.URLError, OSError) as e:
        sys.exit(f"[错误] 无法连接 Ollama（{base_url}）：{e}\n请先启动 Ollama：ollama serve")

    models = {m.get("name", "") for m in tags.get("models", [])}
    if model not in models:
        print(f"[提示] 未找到模型 {model}，可用模型：{sorted(models) or '无'}")
        sys.exit(f"请先拉取语言模型：ollama pull {model}")


def translate_chunk(base_url: str, model: str, text: str) -> str:
    """翻译单段文本。"""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "options": {"temperature": 0.1},
    }
    data = ollama_request(f"{base_url}/api/chat", payload)
    return data.get("message", {}).get("content", "").strip()


def split_chunks(text: str, size: int) -> list[str]:
    """按字符数切分文本，优先在空行处断开，避免切断代码块。"""
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = (current + "\n\n" + block).strip() if current else block
        if len(candidate) > size and current:
            chunks.append(current.strip())
            current = block
        else:
            current = candidate
    if current.strip():
        chunks.append(current.strip())
    return chunks


def translate_file(base_url: str, model: str, src: Path, force: bool) -> None:
    """翻译单个中文文档，输出 .en.md。"""
    dst = src.with_suffix(".en.md")
    if dst.exists() and not force:
        print(f"[跳过] {src.name} → {dst.name}（已存在，--force 强制重译）")
        return

    print(f"[翻译] {src.name} → {dst.name} ...")
    source_text = src.read_text(encoding="utf-8")
    chunks = split_chunks(source_text, CHUNK_SIZE)
    parts: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        print(f"  - 段落 {i}/{len(chunks)} ...")
        translated = translate_chunk(base_url, model, chunk)
        if not translated:
            print(f"  [警告] 段落 {i} 翻译结果为空，保留原文")
            translated = chunk
        parts.append(translated)
    dst.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    print(f"[完成] {dst.name}（{len(parts)} 段）")


def main() -> int:
    parser = argparse.ArgumentParser(description="把中文源文档翻译为英文（.en.md）")
    parser.add_argument("--file", help="只翻译指定文件（相对项目根目录，如 README.md）")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama 模型（默认 {DEFAULT_MODEL}）")
    parser.add_argument("--base-url", default=DEFAULT_OLLAMA_URL, help=f"Ollama 地址（默认 {DEFAULT_OLLAMA_URL}）")
    parser.add_argument("--force", action="store_true", help="英文版已存在时强制重译")
    args = parser.parse_args()

    check_ollama(args.base_url, args.model)

    files = [Path(args.file)] if args.file else [ROOT / p for p in SOURCE_DOCS]
    if args.file:
        files = [ROOT / args.file]

    missing = [f for f in files if not f.exists()]
    if missing:
        sys.exit(f"[错误] 源文档不存在：{', '.join(str(m) for m in missing)}")

    for src in files:
        translate_file(args.base_url, args.model, src, args.force)

    print("\n全部完成。英文文档已生成（.en.md），检查无误后可提交推送。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
