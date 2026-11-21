//! storage-audit `--dirs` 与目录扫描契约测试。
//!
//! 覆盖：只扫指定目录、多目录去空白、目录不存在静默跳过、manifest 条目落在
//! 未扫描目录时的行为、子目录递归、扫描目录之外的文件被忽略。
//!
//! 全部为黑盒测试：只经 `CARGO_BIN_EXE_storage_audit` 启动二进制。

use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

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

#[test]
fn dirs_limits_actual_scan_to_requested_dirs() {
    let dir = TempDir::new("dirs-filter");
    let storage = dir.path().join("storage");
    write_file(&storage.join("images/a.jpg"), b"a");
    write_file(&storage.join("thumbnails/t.jpg"), b"t");
    let manifest = write_manifest(dir.path(), &json!([{ "path": "images/a.jpg", "size": 1 }]));

    // 默认扫描 images,thumbnails,videos：thumbnails 下无 manifest 记录的文件是 orphan。
    let report = report_from_stdout(&run_audit(&storage, &manifest, &[]));
    assert_eq!(report["actual_files"], 2);
    assert_eq!(report["orphans"].as_array().unwrap().len(), 1);
    assert_eq!(report["orphans"][0]["path"], "thumbnails/t.jpg");

    // 只扫 images：thumbnails 下的文件不进入 actual_files / orphans。
    let report = report_from_stdout(&run_audit(&storage, &manifest, &["--dirs", "images"]));
    assert_eq!(report["scanned_media_dirs"], json!(["images"]));
    assert_eq!(report["actual_files"], 1);
    assert_eq!(report["orphans"], json!([]));
    assert_eq!(report["missing"], json!([]));
}

#[test]
fn dirs_trims_spaces_and_keeps_requested_order() {
    let dir = TempDir::new("dirs-trim");
    let storage = dir.path().join("storage");
    write_file(&storage.join("images/a.jpg"), b"a");
    write_file(&storage.join("thumbnails/t.jpg"), b"t");
    let manifest = write_manifest(
        dir.path(),
        &json!([
            { "path": "images/a.jpg", "size": 1 },
            { "path": "thumbnails/t.jpg", "size": 1 }
        ]),
    );

    let report = report_from_stdout(&run_audit(
        &storage,
        &manifest,
        &["--dirs", " images , thumbnails "],
    ));

    // scanned_media_dirs 回显的是去空白后的请求列表，保持输入顺序。
    assert_eq!(
        report["scanned_media_dirs"],
        json!(["images", "thumbnails"])
    );
    assert_eq!(report["actual_files"], 2);
    assert_eq!(report["missing"], json!([]));
    assert_eq!(report["orphans"], json!([]));
}

#[test]
fn dirs_nonexistent_directory_is_silently_skipped() {
    let dir = TempDir::new("dirs-missing");
    let storage = dir.path().join("storage");
    write_file(&storage.join("images/a.jpg"), b"a");
    let manifest = write_manifest(dir.path(), &json!([{ "path": "images/a.jpg", "size": 1 }]));

    // 不存在的目录静默跳过：退出码 0、stderr 为空、其余目录照常扫描。
    let report = report_from_stdout(&run_audit(
        &storage,
        &manifest,
        &["--dirs", "images,no-such-dir"],
    ));

    assert_eq!(report["actual_files"], 1);
    assert_eq!(report["missing"], json!([]));
    assert_eq!(report["orphans"], json!([]));
    // 当前契约：scanned_media_dirs 记录的是请求列表，不校验目录是否存在。
    assert_eq!(
        report["scanned_media_dirs"],
        json!(["images", "no-such-dir"])
    );
}

#[test]
fn manifest_entries_outside_scanned_dirs_are_reported_missing() {
    let dir = TempDir::new("dirs-subset");
    let storage = dir.path().join("storage");
    write_file(&storage.join("images/a.jpg"), b"a");
    write_file(&storage.join("thumbnails/t.jpg"), b"t");
    let manifest = write_manifest(
        dir.path(),
        &json!([
            { "path": "images/a.jpg", "size": 1 },
            { "path": "thumbnails/t.jpg", "size": 1 }
        ]),
    );

    let report = report_from_stdout(&run_audit(&storage, &manifest, &["--dirs", "images"]));

    // 当前契约（缺陷，见 task-6 报告）：--dirs 只限制磁盘扫描范围，不过滤 manifest。
    // thumbnails/t.jpg 明明在盘上，但因目录未被扫描而被判为 missing。
    assert_eq!(report["actual_files"], 1);
    assert_eq!(report["missing"], json!(["thumbnails/t.jpg"]));
    assert_eq!(report["orphans"], json!([]));
}

#[test]
fn scans_nested_subdirectories_recursively() {
    let dir = TempDir::new("dirs-recursive");
    let storage = dir.path().join("storage");
    write_file(&storage.join("images/2026-09/a.jpg"), b"a");
    write_file(&storage.join("images/top.jpg"), b"top");
    let manifest = write_manifest(
        dir.path(),
        &json!([
            { "path": "images/2026-09/a.jpg", "size": 1 },
            { "path": "images/top.jpg", "size": 3 }
        ]),
    );

    let report = report_from_stdout(&run_audit(&storage, &manifest, &[]));

    assert_eq!(report["actual_files"], 2);
    assert_eq!(report["missing"], json!([]));
    assert_eq!(report["orphans"], json!([]));
    assert_eq!(report["size_mismatches"], json!([]));
}

#[test]
fn files_outside_scanned_media_dirs_are_ignored() {
    let dir = TempDir::new("dirs-outside");
    let storage = dir.path().join("storage");
    std::fs::create_dir_all(&storage).unwrap();
    write_file(&storage.join("lancedb/index.bin"), b"db");
    write_file(&storage.join("trash/old.jpg"), b"old");
    write_file(&storage.join("root-note.txt"), b"note");
    let manifest = write_manifest(dir.path(), &json!([]));

    // 只扫描一级媒体目录，根下其它文件/目录既不算 actual_files 也不算 orphan。
    let report = report_from_stdout(&run_audit(&storage, &manifest, &[]));

    assert_eq!(report["actual_files"], 0);
    assert_eq!(report["orphans"], json!([]));
    assert_eq!(report["missing"], json!([]));
}
