"""分段下载 hf-mirror 上的大文件（应对 CDN 429 限流 + 不支持全量 GET 的情况）。

为什么要这个脚本：该镜像对**全量 GET** 返回 "Entry not found"，只有 Range 请求可用；
而且连续请求会 429。所以按 16 MB 分段 + 间隔 + 指数退避，支持断点续传。
"""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CHUNK = 16 * 1024 * 1024
PACE = 1.6  # 每块之间的间隔（秒），太快会被 429
BACKOFF = (5, 15, 45, 90, 180)


def download(url: str, out: Path, expect: int) -> int:
    """按 Range 分块下载到 ``out``（已存在则续传），返回最终字节数。"""
    start = out.stat().st_size if out.exists() else 0
    if start >= expect:
        print(f"已完整: {out} ({start} bytes)")
        return start

    pos = start
    t0 = time.time()
    with out.open("ab") as f:
        while pos < expect:
            end = min(pos + CHUNK - 1, expect - 1)
            data = None
            for delay in (0, *BACKOFF):
                if delay:
                    print(f"    退避 {delay}s（429/限流）", flush=True)
                    time.sleep(delay)
                try:
                    req = urllib.request.Request(url, headers={"Range": f"bytes={pos}-{end}"})
                    with urllib.request.urlopen(req, timeout=180) as resp:
                        data = resp.read()
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code in (429, 503):
                        continue
                    raise
                except (urllib.error.URLError, TimeoutError) as exc:
                    print(f"    网络异常: {exc}", flush=True)
                    continue
            if data is None:
                raise SystemExit(f"块 {pos // CHUNK} 重试耗尽（已下 {pos} bytes，可重跑续传）")
            if len(data) != end - pos + 1:
                raise SystemExit(f"块长度不符: got {len(data)} want {end - pos + 1}")
            f.write(data)
            f.flush()
            pos += len(data)
            speed = pos / max(0.1, time.time() - t0) / 2**20
            print(
                f"  {pos / expect * 100:5.1f}%  {pos / 2**30:5.2f}/{expect / 2**30:.2f} GiB  "
                f"{speed:5.1f} MiB/s",
                flush=True,
            )
            time.sleep(PACE)
    print(f"完成: {out} ({pos} bytes)")
    return pos


if __name__ == "__main__":
    url, path, size = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
    got = download(url, path, size)
    sys.exit(0 if got == size else 1)
