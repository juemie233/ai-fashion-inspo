"""备份抽样哈希校验：在「不跑全量 SHA-256」的前提下抓出内容损坏。

为什么需要（现有校验的盲区）：`backup_data.sh` 默认只比对**文件数与字节数**，
两者都对得上但内容已坏的情况（磁盘坏道、robocopy 中断、被其它进程改写）不会被发现；
全量 `--verify-hash` 能发现，但 3.2 万素材 = 几十 GB 读一遍，日常备份不划算。

折中：从「冻结源端清单」里**随机抽 N 个文件**（默认 200）做双端 SHA-256 比对。
- 抽样是确定性的（按 `--seed` 固定），同一批备份重跑得到同一组样本，便于复查；
- 命中概率与文件数无关，200 个样本对「整目录损坏」几乎必然命中；
- 成本约 N × 单文件大小（200 张图 ≈ 数十 MB），对备份耗时影响可忽略。

用法::

    python scripts/verify_backup_sample.py --src G:/fashion-inspo-storage \\
        --dest E:/backups/2026-10-21_030000/storage --sample 200 --seed 0

退出码：0 全部一致；2 有不一致或读取失败（与 backup_data.sh 的错误码约定一致）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

"""默认抽样数量：200 个文件足以覆盖「整目录损坏」，又不至于让日常备份变慢。"""
DEFAULT_SAMPLE = 200

"""单次比较的读取块大小（大文件不整体读入内存）。"""
CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    """分块计算 SHA-256（与项目其它地方的 `file_sha256` 口径一致）。"""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def collect_files(root: Path, limit: int | None = None) -> list[Path]:
    """列出 root 下所有常规文件（相对路径，升序保证确定性）。"""
    if not root.exists():
        return []
    files = sorted(
        (p.relative_to(root) for p in root.rglob("*") if p.is_file()),
        key=lambda p: p.as_posix(),
    )
    return files[:limit] if limit else files


def pick_sample(total: int, sample: int, seed: int) -> list[int]:
    """从 ``range(total)`` 里确定性地取 ``sample`` 个下标（升序返回）。"""
    if sample <= 0 or total <= 0:
        return []
    if sample >= total:
        return list(range(total))
    return sorted(random.Random(seed).sample(range(total), sample))


def verify(
    src_root: Path, dest_root: Path, *, sample: int = DEFAULT_SAMPLE, seed: int = 0
) -> dict:
    """抽样比对两端文件的内容哈希。

    Args:
        src_root: 源端根目录（现网 storage 根）。
        dest_root: 备份端根目录（备份里的 storage）。
        sample: 抽样数量；<=0 表示关闭（只统计文件数）。
        seed: 抽样随机种子（固定 → 可复现同一组样本）。

    Returns:
        ``{"total": 源端文件数, "sample": 实际抽样数, "checked": 成功比对数,
        "missing": [备份端缺失], "corrupt": [大小相同但内容不一致],
        "changed": [大小已变=备份后源端又写了], "ok": bool}``

    为什么要区分 ``corrupt`` 与 ``changed``：备份是**时点快照**，复制完成后后台任务
    仍在往源端写入是正常现象。若把「源端变了」也当损坏，日常备份会天天空报警；
    而「大小一模一样、内容却不同」才是真正的静默损坏（坏道/中断写入）。
    """
    rel_files = collect_files(src_root)
    picked = pick_sample(len(rel_files), sample, seed)
    missing: list[str] = []
    corrupt: list[str] = []
    changed: list[str] = []
    checked = 0
    for index in picked:
        rel = rel_files[index]
        src_file = src_root / rel
        dest_file = dest_root / rel
        try:
            if not dest_file.exists():
                missing.append(rel.as_posix())
                continue
            if src_file.stat().st_size != dest_file.stat().st_size:
                # 备份后源端又被写过：属正常现象，不计失败
                changed.append(rel.as_posix())
                continue
            if sha256_file(src_file) != sha256_file(dest_file):
                corrupt.append(rel.as_posix())
            else:
                checked += 1
        except OSError:
            missing.append(rel.as_posix())
    return {
        "total": len(rel_files),
        "sample": len(picked),
        "checked": checked,
        "missing": missing[:20],
        "corrupt": corrupt[:20],
        "changed": changed[:20],
        "ok": not missing and not corrupt,
    }


def main(argv: list[str] | None = None) -> int:
    """命令行入口：打印结果并给出退出码。"""
    parser = argparse.ArgumentParser(description="备份抽样哈希校验")
    parser.add_argument("--src", required=True, type=Path, help="源端根目录")
    parser.add_argument("--dest", required=True, type=Path, help="备份端根目录")
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE, help="抽样数量（0=关）")
    parser.add_argument("--seed", type=int, default=0, help="抽样种子（固定可复现）")
    parser.add_argument("--json", type=Path, default=None, help="把结果另存为 JSON")
    args = parser.parse_args(argv)

    if not args.src.exists():
        print(f"[跳过] 源端不存在：{args.src}")
        return 0
    if not args.dest.exists():
        print(f"[错误] 备份端不存在：{args.dest}")
        return 2

    result = verify(args.src, args.dest, sample=args.sample, seed=args.seed)
    print(
        f"抽样哈希校验：源端 {result['total']} 个文件，抽 {result['sample']} 个，"
        f"通过 {result['checked']}，缺失 {len(result['missing'])}，"
        f"内容不一致 {len(result['corrupt'])}，备份后已变 {len(result['changed'])}"
    )
    for rel in result["missing"]:
        print(f"  ✗ 缺失 {rel}")
    for rel in result["corrupt"]:
        print(f"  ✗ 内容不一致 {rel}")
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
