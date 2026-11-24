//! storage-audit 错误路径契约测试。
//!
//! 覆盖：存储根不存在、manifest 不存在、manifest 非法 JSON、manifest 结构不合法、
//! 报告输出路径不可写。要求全部非零退出、stderr 为中文原因（以「storage-audit 失败：」
//! 开头）、stdout 为空。
//!
//! 全部为黑盒测试：只经 `CARGO_BIN_EXE_storage_audit` 启动二进制。

use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

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

/// 断言错误路径的通用契约：退出码 1、stdout 为空、stderr 含中文原因。
fn assert_failure(output: &Output, reason: &str) {
    assert_eq!(
        output.status.code(),
        Some(1),
        "错误路径应以 1 退出，实际状态：{:?}",
        output.status
    );
    assert!(
        output.stdout.is_empty(),
        "错误路径 stdout 应为空，实际为：{}",
        String::from_utf8_lossy(&output.stdout)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("storage-audit 失败"),
        "stderr 应包含「storage-audit 失败」，实际为：{stderr}"
    );
    assert!(
        stderr.contains(reason),
        "stderr 应包含「{reason}」，实际为：{stderr}"
    );
}

#[test]
fn missing_storage_root_exits_nonzero_with_chinese_error() {
    let dir = TempDir::new("err-root");
    let missing_root = dir.path().join("no-such-storage");
    let manifest = dir.path().join("manifest.json");
    std::fs::write(&manifest, r#"{"expected":[]}"#).unwrap();

    let output = run_audit(&missing_root, &manifest, &[]);

    assert_failure(&output, "存储根不存在");
}

#[test]
fn missing_manifest_exits_nonzero_with_chinese_error() {
    let dir = TempDir::new("err-manifest");
    let storage = dir.path().join("storage");
    std::fs::create_dir_all(&storage).unwrap();
    let missing_manifest = dir.path().join("no-such-manifest.json");

    let output = run_audit(&storage, &missing_manifest, &[]);

    assert_failure(&output, "读取 manifest 失败");
}

#[test]
fn invalid_json_manifest_exits_nonzero_with_chinese_error() {
    let dir = TempDir::new("err-json");
    let storage = dir.path().join("storage");
    std::fs::create_dir_all(&storage).unwrap();
    let manifest = dir.path().join("manifest.json");
    std::fs::write(&manifest, "{ 这不是合法 JSON").unwrap();

    let output = run_audit(&storage, &manifest, &[]);

    assert_failure(&output, "解析 manifest 失败");
}

#[test]
fn manifest_with_invalid_shape_exits_nonzero_with_chinese_error() {
    let dir = TempDir::new("err-shape");
    let storage = dir.path().join("storage");
    std::fs::create_dir_all(&storage).unwrap();
    let manifest = dir.path().join("manifest.json");
    // expected 必须是数组，这里给数字应触发同一解析错误分支。
    std::fs::write(&manifest, r#"{"expected": 42}"#).unwrap();

    let output = run_audit(&storage, &manifest, &[]);

    assert_failure(&output, "解析 manifest 失败");
}

#[test]
fn unwritable_output_path_exits_nonzero_with_chinese_error() {
    let dir = TempDir::new("err-output");
    let storage = dir.path().join("storage");
    std::fs::create_dir_all(&storage).unwrap();
    let manifest = dir.path().join("manifest.json");
    std::fs::write(&manifest, r#"{"expected":[]}"#).unwrap();
    // 父目录不存在 → 写报告失败（扫描本身是成功的）。
    let report = dir.path().join("no-such-sub/report.json");

    let output = run_audit(&storage, &manifest, &["--output", report.to_str().unwrap()]);

    assert_failure(&output, "写报告失败");
}
