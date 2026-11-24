//! storage-audit `--hash` 契约测试。
//!
//! 覆盖：sha256 正确、sha256 错误、manifest 用大写 hex、条目缺 sha256、
//! 以及 size_mismatches 与 hash_mismatches 同时命中时的分类。
//!
//! 全部为黑盒测试：只经 `CARGO_BIN_EXE_storage_audit` 启动二进制，不 import 二进制内部模块。

use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

/// 已知 sha256 向量（可脱离 Rust 实现用 `printf 'abc' | sha256sum` 复核）。
const SHA256_ABC: &str = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
const SHA256_HELLO: &str = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";

static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

/// 独立临时目录，`Drop` 时递归清理（断言失败触发 panic 时同样会清理）。
struct TempDir(PathBuf);

impl TempDir {
    fn new(prefix: &str) -> Self {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let counter = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!("storage-audit-{prefix}-{stamp}-{counter}"));
        std::fs::create_dir_all(&path).unwrap();
        Self(path)
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// 运行 storage-audit 二进制，返回完整输出。
fn run_audit(root: &Path, manifest: &Path, extra: &[&str]) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_storage_audit"));
    command.arg(root).arg(manifest).args(extra);
    command.output().expect("无法启动 storage-audit 二进制")
}

/// 断言退出码为 0、stderr 为空，并把 stdout 解析为报告 JSON。
fn report_from_stdout(output: &Output) -> Value {
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(output.status.success(), "storage-audit 失败：{stderr}");
    assert!(stderr.is_empty(), "成功路径不应写 stderr：{stderr}");
    serde_json::from_slice(&output.stdout).expect("stdout 应为合法 JSON 报告")
}

/// 写入文件并自动创建父目录。
fn write_file(path: &Path, bytes: &[u8]) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, bytes).unwrap();
}

/// 把 expected 数组写成 `<dir>/manifest.json`，返回 manifest 路径。
fn write_manifest(dir: &Path, expected: &Value) -> PathBuf {
    let path = dir.join("manifest.json");
    std::fs::write(&path, json!({ "expected": expected }).to_string()).unwrap();
    path
}

/// 从报告中提取 hash_mismatches 的 path 列表（保持报告顺序）。
fn hash_mismatch_paths(report: &Value) -> Vec<String> {
    report["hash_mismatches"]
        .as_array()
        .expect("hash_mismatches 应为数组")
        .iter()
        .map(|item| item["path"].as_str().expect("path 应为字符串").to_string())
        .collect()
}

#[test]
fn hash_flag_accepts_matching_sha256() {
    let dir = TempDir::new("hash-ok");
    let storage = dir.path().join("storage");
    write_file(&storage.join("images/a.jpg"), b"abc");
    let manifest = write_manifest(
        dir.path(),
        &json!([{ "path": "images/a.jpg", "size": 3, "sha256": SHA256_ABC }]),
    );

    let report = report_from_stdout(&run_audit(&storage, &manifest, &["--hash"]));

    assert_eq!(report["hash_mismatches"], json!([]));
    assert_eq!(report["size_mismatches"], json!([]));
    assert_eq!(report["missing"], json!([]));
    assert_eq!(report["actual_files"], 1);
}

#[test]
fn hash_flag_reports_wrong_sha256_with_expected_and_actual() {
    let wrong = "0".repeat(64);
    let dir = TempDir::new("hash-wrong");
    let storage = dir.path().join("storage");
    write_file(&storage.join("images/a.jpg"), b"abc");
    let manifest = write_manifest(
        dir.path(),
        &json!([{ "path": "images/a.jpg", "size": 3, "sha256": wrong }]),
    );

    let report = report_from_stdout(&run_audit(&storage, &manifest, &["--hash"]));
    let mismatches = report["hash_mismatches"]
        .as_array()
        .expect("hash_mismatches 应为数组");
    assert_eq!(mismatches.len(), 1);
    assert_eq!(mismatches[0]["path"], "images/a.jpg");
    // expected 回显 manifest 原始值，actual 是磁盘内容的小写 hex。
    assert_eq!(mismatches[0]["expected"], wrong.as_str());
    assert_eq!(mismatches[0]["actual"], SHA256_ABC);
    // 内容不匹配不影响 size_mismatches。
    assert_eq!(report["size_mismatches"], json!([]));

    // 不传 --hash 时不做内容校验。
    let report = report_from_stdout(&run_audit(&storage, &manifest, &[]));
    assert_eq!(report["hash_mismatches"], json!([]));
}

#[test]
fn hash_flag_accepts_uppercase_expected_sha256() {
    let uppercase = SHA256_ABC.to_ascii_uppercase();
    let dir = TempDir::new("hash-uppercase");
    let storage = dir.path().join("storage");
    write_file(&storage.join("images/a.jpg"), b"abc");
    let manifest = write_manifest(
        dir.path(),
        &json!([{ "path": "images/a.jpg", "size": 3, "sha256": uppercase }]),
    );

    let report = report_from_stdout(&run_audit(&storage, &manifest, &["--hash"]));

    // 源码按 to_ascii_lowercase() 比较，大写 hex 不应误报。
    assert_eq!(report["hash_mismatches"], json!([]));
}

#[test]
fn entries_without_sha256_are_skipped_by_hash_flag() {
    let dir = TempDir::new("hash-no-sha");
    let storage = dir.path().join("storage");
    write_file(&storage.join("images/a.jpg"), b"abc");
    write_file(&storage.join("images/b.jpg"), b"hello");
    let manifest = write_manifest(
        dir.path(),
        &json!([
            { "path": "images/a.jpg", "size": 3 },
            { "path": "images/b.jpg", "size": 999 }
        ]),
    );

    // 带 --hash：缺 sha256 的条目被跳过，不产生 hash_mismatches。
    let report = report_from_stdout(&run_audit(&storage, &manifest, &["--hash"]));
    assert_eq!(report["hash_mismatches"], json!([]));
    assert_eq!(report["size_mismatches"].as_array().unwrap().len(), 1);

    // 不带 --hash：同样不报。
    let report = report_from_stdout(&run_audit(&storage, &manifest, &[]));
    assert_eq!(report["hash_mismatches"], json!([]));
}

#[test]
fn size_and_hash_mismatches_are_reported_separately() {
    let wrong = "0".repeat(64);
    let dir = TempDir::new("hash-mixed");
    let storage = dir.path().join("storage");
    write_file(&storage.join("images/size-only.jpg"), b"abc");
    write_file(&storage.join("images/hash-only.jpg"), b"hello");
    write_file(&storage.join("images/both.jpg"), b"xy");
    let manifest = write_manifest(
        dir.path(),
        &json!([
            // 内容 sha256 正确、manifest size 错误 → 只进 size_mismatches
            { "path": "images/size-only.jpg", "size": 999, "sha256": SHA256_ABC },
            // size 正确、sha256 错误 → 只进 hash_mismatches
            { "path": "images/hash-only.jpg", "size": 5, "sha256": wrong },
            // size 与 sha256 都错误 → 两个列表各出现一次
            { "path": "images/both.jpg", "size": 1, "sha256": wrong }
        ]),
    );

    let report = report_from_stdout(&run_audit(&storage, &manifest, &["--hash"]));

    let size_paths: Vec<&str> = report["size_mismatches"]
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item["path"].as_str().unwrap())
        .collect();
    // BTreeMap 顺序 → 按 path 字母序。
    assert_eq!(size_paths, ["images/both.jpg", "images/size-only.jpg"]);
    assert_eq!(
        hash_mismatch_paths(&report),
        ["images/both.jpg", "images/hash-only.jpg"]
    );

    let hash_only = report["hash_mismatches"]
        .as_array()
        .unwrap()
        .iter()
        .find(|item| item["path"] == "images/hash-only.jpg")
        .expect("应包含 hash-only 条目的 hash mismatch");
    assert_eq!(hash_only["actual"], SHA256_HELLO);

    let both = report["size_mismatches"]
        .as_array()
        .unwrap()
        .iter()
        .find(|item| item["path"] == "images/both.jpg")
        .expect("应包含 both 条目的 size mismatch");
    assert_eq!(both["expected"], 1);
    assert_eq!(both["actual"], 2);
}
