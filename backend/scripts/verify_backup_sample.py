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


"""与 `scripts/ops/backup_data.sh` 的 robocopy ``//XD`` 保持**完全一致**的排除目录。

为什么必须一致：复制端跳过这些目录（可重建/临时快照），校验端若仍从源端取样，
就会把「刻意没备份」的文件报成「缺失」——2026-10-22 的备份就是这样被判失败的：
源端抽到 3 个 ``_crop_backup/`` 下的文件，而该目录本就不在备份范围内。
新增排除项时，两处必须同步修改（这是本文件与 shell 之间唯一的隐式耦合）。
"""
EXCLUDED_DIRS = frozenset({
    "logs",
    "tmp",
    "_crop_backup",
    "_crop_dups",
    "cookies",
    "debug",
    "faces",
    "_pre_reset_snapshot",
    "_pre_restore_snapshot",
})

"""``lancedb_backup_*`` 是通配形式的排除项（robocopy 里写作 ``lancedb_backup_*``）。"""
EXCLUDED_DIR_PREFIXES = ("lancedb_backup_",)


"""不参与**逐文件哈希抽样**的目录（但它们**确实**会被备份）。

`lancedb` 是活库：备份步骤在写锁内取一致性快照，快照完成后源端仍会继续写入新的
`.manifest` / `.lance` 文件——这些文件在快照里本来就不该有。按文件比对只会把
「正常的新写入」误报成「缺失」（2026-10-22 复验时实测 5 条）。它的完整性由
快照步骤本身 + manifest 里的目录文件数/字节数覆盖，不依赖抽样哈希。
"""
SAMPLE_SKIP_DIRS = frozenset({"lancedb"})


def _is_sample_skipped(rel: Path) -> bool:
    """判断该相对路径是否属于「不参与抽样」的目录（如活库 lancedb）。"""
    return any(part in SAMPLE_SKIP_DIRS for part in rel.parts[:-1])


def _is_excluded(rel: Path) -> bool:
    """判断相对路径**所在目录**是否在排除清单里（robocopy ``//XD`` 作用于目录）。"""
    for part in rel.parts[:-1]:
        if part in EXCLUDED_DIRS or part.startswith(EXCLUDED_DIR_PREFIXES):
            return True
    return False


def collect_files(root: Path, limit: int | None = None) -> list[Path]:
    """列出 root 下所有常规文件（相对路径，升序保证确定性）。"""
    if not root.exists():
        return []
    files = sorted(
        (p.relative_to(root) for p in root.rglob("*") if p.is_file() and not _is_excluded(p.relative_to(root)) and not _is_sample_skipped(p.relative_to(root))),
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
