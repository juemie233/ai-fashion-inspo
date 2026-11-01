"""批量反推姿态图库全部姿态（含双人）的提示词，写入 `pose_prompts` 表。

用途：
- 对图库 1,439 条姿态（4 体系，含 `双人` / `pair` / `double`）逐张读取线稿，
  用 **thinking 开启** 的 DeepSeek 反推出图生图提示词；
- 结果幂等 upsert 到当前库 `pose_prompts` 表；支持断点续跑（默认跳过已有）、
  失败重试、并发与进度/ETA 输出。

前置：
1. 先执行迁移建表：`cd backend && python -m alembic upgrade head`；
2. 设置环境变量 `DEEPSEEK_API_KEY`（不要把 key 写进任何文件）。

用法（在仓库根目录）：
    # 预览：只打印待处理姿态，不调用接口
    python scripts/data/backfill_pose_prompts.py --dry-run

    # 小批量试跑（写入 3 条）
    python scripts/data/backfill_pose_prompts.py --limit 3 --workers 2

    # 全量（默认跳过已存在的 pose_id；中断后直接重跑即可续）
    python scripts/data/backfill_pose_prompts.py --workers 4

    # 强制重刷全部
    python scripts/data/backfill_pose_prompts.py --force --workers 4

退出码：0=全部成功；1=有失败（已成功的仍会写库，失败列表见结尾输出）。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import settings
from app.services import pose_gallery
from app.utils.time import utcnow

URL = "https://api.deepseek.com/anthropic/v1/messages"
MODEL = "deepseek-flash[1m]"
SYSTEM = (
    "你是资深的 AI 绘画提示词工程师，擅长从姿态参考图反推出可复现该姿势的图生图正向提示词。"
    "只输出一段中文提示词正文，不要解释、不要 Markdown、不要编号。"
    "要求：80～150 字；写清人物朝向、身体重心、四肢位置、手部动作、头部朝向、镜头视角与景别；"
    "只描述便于复现姿势的客观信息，不要描述线稿风格、关节球、灰色背景等参考图自身特征，"
    "不要编造服装、场景、光线等图中没有的内容。"
)
USER_TEXT = "请反推这张姿态图的提示词。"


def _db_path(raw: str) -> Path:
    """解析数据库路径：默认取 app 配置的 database_url。"""
    if not raw:
        raw = settings.database_url
    if ":///" not in raw:
        raise SystemExit(f"只支持 SQLite 文件库：{raw}")
    path = Path(raw.split(":///", 1)[1])
    return path if path.is_absolute() else (BACKEND / path).resolve()


def _open_db(db: Path) -> sqlite3.Connection:
    """读写连接：本函数负责 INSERT / UPDATE `pose_prompts`（非只读用途）。"""
    con = sqlite3.connect(db, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    return con


def _has_table(con: sqlite3.Connection) -> bool:
    row = con.execute(
        "select name from sqlite_master where type='table' and name='pose_prompts'"
    ).fetchone()
    return row is not None


def _existing_ids(con: sqlite3.Connection) -> set[str]:
    return {row[0] for row in con.execute("select pose_id from pose_prompts")}


def _upsert(con: sqlite3.Connection, rec: dict) -> None:
    now = utcnow().isoformat(sep=" ")
    con.execute(
        """
        insert into pose_prompts
          (pose_id, name, angle, system, category, prompt, thinking, model,
           thinking_enabled, input_tokens, output_tokens, elapsed_s, image_bytes,
           created_at, updated_at)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(pose_id) do update set
          name=excluded.name, angle=excluded.angle, system=excluded.system,
          category=excluded.category, prompt=excluded.prompt,
          thinking=excluded.thinking, model=excluded.model,
          thinking_enabled=excluded.thinking_enabled,
          input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens,
          elapsed_s=excluded.elapsed_s, image_bytes=excluded.image_bytes,
          updated_at=excluded.updated_at
        """,
        (
            rec["pose_id"],
            rec["name"],
            rec["angle"],
            rec["system"],
            rec["category"],
            rec["prompt"],
            rec["thinking"],
            rec["model"],
            1 if rec["thinking_enabled"] else 0,
            rec["input_tokens"],
            rec["output_tokens"],
            rec["elapsed_s"],
            rec["image_bytes"],
            now,
            now,
        ),
    )
    con.commit()


def _parse_response(data: dict) -> tuple[str, str, dict]:
    thinking: list[str] = []
    text: list[str] = []
    for block in data.get("content") or []:
        if block.get("type") == "thinking":
            thinking.append(str(block.get("thinking") or ""))
        elif block.get("type") == "text":
            text.append(str(block.get("text") or ""))
    return "\n".join(thinking).strip(), "\n".join(text).strip(), data.get("usage") or {}


def _call_api(
    key: str,
    image_b64: str,
    *,
    budget_tokens: int,
    max_tokens: int,
    timeout: float,
    retries: int = 3,
) -> tuple[str, str, dict, float]:
    """阻塞调用（放在线程里执行），带 429/5xx 重试。"""
    body = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "thinking": {"type": "enabled", "budget_tokens": budget_tokens},
        "system": SYSTEM,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
                    {"type": "text", "text": USER_TEXT},
                ],
            }
        ],
    }
    last_error = ""
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            URL,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
            elapsed = round(time.monotonic() - started, 2)
            thinking, prompt, usage = _parse_response(data)
            return thinking, prompt, usage, elapsed
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 * attempt)
                continue
            break
        except Exception as exc:  # noqa: BLE001 —— 网络超时/断连统一重试
            last_error = str(exc)[:200]
            if attempt < retries:
                time.sleep(2 * attempt)
                continue
            break
    raise RuntimeError(last_error or "未知错误")


async def _process_pose(
    key: str,
    pose: dict,
    con: sqlite3.Connection,
    sem: asyncio.Semaphore,
    args: argparse.Namespace,
) -> dict:
    """单条：下载线稿 → 调接口 → upsert；返回摘要（失败时 error 字段）。"""
    async with sem:
        pose_id = pose["id"]
        try:
            image_path = await pose_gallery.ensure_pose_image(pose_id)
            raw = await asyncio.to_thread(image_path.read_bytes)
            thinking, prompt, usage, elapsed = await asyncio.to_thread(
                _call_api,
                key,
                base64.b64encode(raw).decode(),
                budget_tokens=args.budget_tokens,
                max_tokens=args.max_tokens,
                timeout=args.timeout,
            )
            prompt = (prompt or "").strip()
            if not prompt:
                raise RuntimeError("接口返回空提示词")
            rec = {
                "pose_id": pose_id,
                "name": str(pose.get("name") or ""),
                "angle": str(pose.get("angle") or ""),
                "system": str(pose.get("system") or ""),
                "category": str(pose.get("category") or ""),
                "prompt": prompt,
                "thinking": thinking or None,
                "model": MODEL,
                "thinking_enabled": True,
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
                "elapsed_s": elapsed,
                "image_bytes": len(raw),
            }
            # sqlite 连接必须在创建它的线程里用，这里直接写（单行 upsert，开销很小）
            _upsert(con, rec)
            return {"pose_id": pose_id, "ok": True, **rec, "thinking_chars": len(thinking)}
        except Exception as exc:  # noqa: BLE001 —— 单条失败不能中断全量
            return {"pose_id": pose_id, "ok": False, "error": str(exc)[:300]}


async def run(args: argparse.Namespace) -> int:
    key = (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DS_KEY") or "").strip()
    if not key and not args.dry_run:
        raise SystemExit("缺少 DEEPSEEK_API_KEY 环境变量")
    db = _db_path(args.db)
    if not db.exists():
        raise SystemExit(f"数据库不存在：{db}")
    con = _open_db(db)
    if not _has_table(con):
        raise SystemExit("表 pose_prompts 不存在：先 cd backend && python -m alembic upgrade head")

    items = await pose_gallery.load_catalog()
    items = [item for item in items if item.get("id")]
    items.sort(key=lambda item: item["id"])
    existing = set() if args.force else _existing_ids(con)
    todo = [item for item in items if item["id"] not in existing]
    if args.limit > 0:
        todo = todo[: args.limit]
    print(
        f"图库 {len(items)} 条；已有 {len(existing)} 条；本次待反推 {len(todo)} 条；"
        f"workers={args.workers}、thinking budget={args.budget_tokens}、max_tokens={args.max_tokens}",
        flush=True,
    )
    if args.dry_run:
        for item in todo[:10]:
            print(f"  [dry-run] {item['id']} {item.get('name')}（{item.get('angle')}）{item.get('system')}")
        print(f"[dry-run] 共 {len(todo)} 条，未调用接口")
        return 0

    sem = asyncio.Semaphore(max(1, args.workers))
    results: list[dict] = []
    started = time.monotonic()

    async def worker(pose: dict) -> None:
        result = await _process_pose(key, pose, con, sem, args)
        results.append(result)
        done = len(results)
        if result["ok"] and done % args.log_every == 0:
            rate = (time.monotonic() - started) / done
            eta = (len(todo) - done) * rate / 60
            print(
                f"[{done}/{len(todo)}] ok {result['pose_id']} out={result['output_tokens']} "
                f"chars={len(result['prompt'])}（剩余约 {eta:.0f} 分钟）",
                flush=True,
            )
        elif not result["ok"]:
            print(f"[失败] {result['pose_id']}: {result['error']}", flush=True)

    await asyncio.gather(*(worker(pose) for pose in todo))
    ok = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    total_out = sum(int(r.get("output_tokens") or 0) for r in ok)
    total_in = sum(int(r.get("input_tokens") or 0) for r in ok)
    total_seconds = round(time.monotonic() - started, 1)
    print(
        f"完成：成功 {len(ok)} / {len(todo)}，失败 {len(failed)}；"
        f"总耗时 {total_seconds / 60:.1f} 分钟；token 输入 {total_in} + 输出 {total_out}",
        flush=True,
    )
    if failed:
        print("失败列表（可直接重新运行续跑）：", flush=True)
        for r in failed:
            print(f"  - {r['pose_id']}: {r['error']}", flush=True)
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="批量反推姿态图库全量提示词并写入 pose_prompts 表")
    parser.add_argument("--db", default="", help="SQLite 路径（默认取 app 配置 database_url）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少条（0=全部）")
    parser.add_argument("--workers", type=int, default=4, help="并发请求数（默认 4）")
    parser.add_argument("--force", action="store_true", help="已有记录也重刷（默认跳过）")
    parser.add_argument("--dry-run", action="store_true", help="只打印待处理列表，不调用接口")
    parser.add_argument("--budget-tokens", type=int, default=2048, help="thinking 预算 token")
    parser.add_argument("--max-tokens", type=int, default=4096, help="单条最大输出 token")
    parser.add_argument("--timeout", type=float, default=180.0, help="单次 HTTP 超时秒数")
    parser.add_argument("--log-every", type=int, default=20, help="每处理 N 条打印一次进度")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
