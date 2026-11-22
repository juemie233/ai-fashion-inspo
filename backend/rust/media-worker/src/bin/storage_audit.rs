//! storage-audit：只读的存储完整性扫描 sidecar。
//!
//! 输入一份数据库导出的 `manifest.json`（expected 文件清单），扫描 storage
//! 的 images/thumbnails/videos，输出：
//! - 数据库有、磁盘没有的缺失文件
//! - 磁盘有、数据库没有的孤立文件
//! - 大小不一致
//! - 可选 SHA-256 不一致
//!
//! 不连数据库、不删除/移动任何文件；结果写 JSON，由 Python/管理员决定后续动作。

use std::collections::{BTreeMap, BTreeSet};
use std::fs::File;
use std::io::{BufReader, Read};
use std::path::{Component, Path, PathBuf};
use std::time::Instant;

use anyhow::{Context, Result};
use clap::Parser;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use walkdir::WalkDir;

#[derive(Parser, Debug)]
#[command(name = "storage-audit", version, about = "只读扫描素材存储完整性")]
struct Cli {
    /// 存储根目录（如 G:/fashion-inspo-storage）
    root: PathBuf,
    /// 数据库导出的 expected manifest JSON
    manifest: PathBuf,
    /// 只扫描这些一级目录（逗号分隔；省略时默认 images,thumbnails,videos）
    ///
    /// 显式指定时 manifest 也按同一范围过滤，否则未扫描目录下的素材会被误报为缺失。
    #[arg(long)]
    dirs: Option<String>,
    /// 输出报告 JSON；不填则打印到 stdout
    #[arg(long)]
    output: Option<PathBuf>,
    /// 对 manifest 中带 sha256 的条目做内容校验（否则只比大小）
    #[arg(long)]
    hash: bool,
}

#[derive(Deserialize)]
struct Manifest {
    expected: Vec<ExpectedEntry>,
}

#[derive(Deserialize, Clone)]
struct ExpectedEntry {
    path: String,
    #[serde(default)]
    size: Option<u64>,
    #[serde(default)]
    sha256: Option<String>,
}

#[derive(Serialize)]
struct Report {
    root: String,
    scanned_media_dirs: Vec<String>,
    expected_files: usize,
    actual_files: usize,
    missing: Vec<String>,
    orphans: Vec<OrphanFile>,
    size_mismatches: Vec<SizeMismatch>,
    hash_mismatches: Vec<HashMismatch>,
    elapsed_ms: u128,
}

#[derive(Serialize)]
struct OrphanFile {
    path: String,
    size: u64,
}

#[derive(Serialize)]
struct SizeMismatch {
    path: String,
    expected: u64,
    actual: u64,
}

#[derive(Serialize)]
struct HashMismatch {
    path: String,
    expected: String,
    actual: String,
}

fn main() {
    if let Err(err) = run() {
        eprintln!("storage-audit 失败：{err:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    let cli = Cli::parse();
    let started = Instant::now();
    let root = cli
        .root
        .canonicalize()
        .with_context(|| format!("存储根不存在：{}", cli.root.display()))?;
    let manifest_text = std::fs::read_to_string(&cli.manifest)
        .with_context(|| format!("读取 manifest 失败：{}", cli.manifest.display()))?;
    let manifest: Manifest = serde_json::from_str(&manifest_text).context("解析 manifest 失败")?;

    let mut normalized = BTreeMap::new();
    for entry in &manifest.expected {
        let Some(rel) = normalize_relative(&entry.path) else {
            continue;
        };
        normalized.insert(rel, entry.clone());
    }

    let scope_explicit = cli.dirs.is_some();
    let dirs: Vec<String> = match &cli.dirs {
        Some(raw) => parse_dirs(raw),
        None => vec!["images".to_owned(), "thumbnails".to_owned(), "videos".to_owned()],
    };
    // 显式限定范围时，manifest 也要按同一组目录过滤：否则没被扫描的目录下的
    // 素材会被报成 missing（文件其实在盘上），missing 语义对子集扫描不成立。
    if scope_explicit && !dirs.is_empty() {
        let prefixes: Vec<String> = dirs.iter().map(|dir| format!("{dir}/")).collect();
        normalized.retain(|path, _| {
            dirs.iter().any(|dir| path == dir) || prefixes.iter().any(|p| path.starts_with(p))
        });
    }

    let mut actual = BTreeSet::new();
    for dir in &dirs {
        let base = root.join(dir);
        if !base.is_dir() {
            continue;
        }
        for entry in WalkDir::new(&base).follow_links(false) {
            let entry = match entry {
                Ok(entry) => entry,
                Err(_) => continue,
            };
            if !entry.file_type().is_file() {
                continue;
            }
            if let Ok(rel) = entry.path().strip_prefix(&root) {
                actual.insert(rel.to_string_lossy().replace('\\', "/"));
            }
        }
    }

    let missing: Vec<String> = normalized
        .keys()
        .filter(|path| !actual.contains(*path))
        .cloned()
        .collect();
    let orphans: Vec<OrphanFile> = actual
        .iter()
        .filter(|path| !normalized.contains_key(*path))
        .map(|path| OrphanFile {
            path: path.clone(),
            size: std::fs::metadata(root.join(path))
                .map(|meta| meta.len())
                .unwrap_or(0),
        })
        .collect();

    // 参与比对的清单条数（已剔除非法路径与 --dirs 之外的条目），比 manifest.expected.len()
    // 更能反映「这次实际校验了多少」
    let expected_files = normalized.len();
    let existing: Vec<(String, ExpectedEntry)> = normalized
        .into_iter()
        .filter(|(path, _)| actual.contains(path))
        .collect();
    let size_mismatches: Vec<SizeMismatch> = existing
        .iter()
        .filter_map(|(path, entry)| {
            let size = entry.size?;
            let actual_size = std::fs::metadata(root.join(path)).ok()?.len();
            (size != actual_size).then(|| SizeMismatch {
                path: path.clone(),
                expected: size,
                actual: actual_size,
            })
        })
        .collect();

    let hash_mismatches: Vec<HashMismatch> = if cli.hash {
        existing
            .par_iter()
            .filter_map(|(path, entry)| {
                let expected = entry.sha256.as_ref()?;
                let actual = sha256_file(&root.join(path)).ok()?;
                (actual != expected.to_ascii_lowercase()).then(|| HashMismatch {
                    path: path.clone(),
                    expected: expected.clone(),
                    actual,
                })
            })
            .collect()
    } else {
        Vec::new()
    };

    let report = Report {
        root: root.to_string_lossy().into_owned(),
        scanned_media_dirs: dirs,
        expected_files,
        actual_files: actual.len(),
        missing,
        orphans,
        size_mismatches,
        hash_mismatches,
        elapsed_ms: started.elapsed().as_millis(),
    };
    let json = serde_json::to_string_pretty(&report).context("序列化报告失败")?;
    if let Some(output) = &cli.output {
        std::fs::write(output, json)
            .with_context(|| format!("写报告失败：{}", output.display()))?;
    } else {
        println!("{json}");
    }
    Ok(())
}

/// 只接受相对路径；绝对路径/`..` 一律忽略，避免扫描逃逸 storage_root。
///
/// 输出按 `Normal` 组件重新拼接（分隔符统一为 `/`）：`./images/a.jpg` 要归一成
/// `images/a.jpg`，否则与磁盘侧路径字符串不相等，同一文件会同时出现在 missing
/// 和 orphans 里，size/hash 校验也会因 key 不匹配被跳过。
fn normalize_relative(raw: &str) -> Option<String> {
    let path = Path::new(raw);
    if path.is_absolute() {
        return None;
    }
    let mut parts: Vec<&str> = Vec::new();
    for component in path.components() {
        match component {
            Component::Normal(part) => parts.push(part.to_str()?),
            Component::CurDir => {}
            _ => return None,
        }
    }
    if parts.is_empty() {
        return None;
    }
    Some(parts.join("/"))
}

/// 逗号分隔的目录参数解析（去空白、丢空项）。
fn parse_dirs(raw: &str) -> Vec<String> {
    raw.split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(ToOwned::to_owned)
        .collect()
}

fn sha256_file(path: &Path) -> Result<String> {
    let file = File::open(path).with_context(|| format!("打开文件失败：{}", path.display()))?;
    let mut reader = BufReader::with_capacity(1 << 20, file);
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; 1 << 20];
    loop {
        let read = reader
            .read(&mut buffer)
            .with_context(|| format!("读取文件失败：{}", path.display()))?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_dir(prefix: &str) -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!("{prefix}-{stamp}"));
        std::fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn rejects_absolute_or_parent_paths() {
        assert_eq!(
            normalize_relative("images/a.jpg"),
            Some("images/a.jpg".into())
        );
        assert_eq!(normalize_relative("../a.jpg"), None);
        assert_eq!(normalize_relative("C:/a.jpg"), None);
        assert_eq!(normalize_relative("/a.jpg"), None);
    }

    #[test]
    fn scans_missing_orphan_and_size_mismatch() {
        let root = temp_dir("storage-audit");
        std::fs::create_dir_all(root.join("images")).unwrap();
        std::fs::write(root.join("images/keep.jpg"), b"keep").unwrap();
        std::fs::write(root.join("images/orphan.jpg"), b"orphan").unwrap();
        let manifest = Manifest {
            expected: vec![
                ExpectedEntry {
                    path: "images/keep.jpg".into(),
                    size: Some(4),
                    sha256: None,
                },
                ExpectedEntry {
                    path: "images/missing.jpg".into(),
                    size: Some(10),
                    sha256: None,
                },
            ],
        };
        let actual = manifest
            .expected
            .iter()
            .filter(|e| root.join(&e.path).is_file())
            .count();
        assert_eq!(actual, 1);
        assert!(!root.join("images/missing.jpg").exists());
        assert!(root.join("images/orphan.jpg").is_file());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn hashes_file() {
        let root = temp_dir("storage-audit-hash");
        std::fs::write(root.join("a.txt"), b"abc").unwrap();
        assert_eq!(
            sha256_file(&root.join("a.txt")).unwrap(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        let _ = std::fs::remove_dir_all(root);
    }
}
