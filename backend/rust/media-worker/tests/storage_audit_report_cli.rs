//! storage-audit 报告结构、排序、输出方式与路径归一化契约测试。
//!
//! 覆盖：报告字段集合与默认值、missing/orphans 排序与字段、空清单空存储、
//! stdout vs `--output`、反斜杠归一化、绝对路径/`..` 忽略（但 expected_files
//! 仍记录原始条目数）、以及 `./` 前缀的当前实际行为。
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

/// 从报告数组中提取 path 列表（保持报告顺序）。
fn paths_of(report: &Value, field: &str) -> Vec<String> {
    report[field]
        .as_array()
        .unwrap_or_else(|| panic!("{field} 应为数组"))
        .iter()
        .map(|item| item["path"].as_str().expect("path 应为字符串").to_string())
        .collect()
}

#[test]
fn report_has_expected_schema_and_default_scanned_dirs() {
    let dir = TempDir::new("report-schema");
    let storage = dir.path().join("storage");
    write_file(&storage.join("images/keep.jpg"), b"abc");
    let manifest = write_manifest(
        dir.path(),
        &json!([{ "path": "images/keep.jpg", "size": 3 }]),
    );

    let report = report_from_stdout(&run_audit(&storage, &manifest, &[]));

    let object = report.as_object().expect("报告应为 JSON 对象");
    let mut keys: Vec<&str> = object.keys().map(String::as_str).collect();
    keys.sort_unstable();
    assert_eq!(
        keys,
        [
            "actual_files",
            "elapsed_ms",
            "expected_files",
            "hash_mismatches",
            "missing",
            "orphans",
            "root",
            "scanned_media_dirs",
            "size_mismatches",
        ]
    );

    // 报告里的 root 是 canonicalize 后的绝对路径。
    let canonical = storage.canonicalize().unwrap();
    assert_eq!(report["root"].as_str(), canonical.to_str());
    // 未传 --dirs 时默认扫描三个媒体目录。
    assert_eq!(
        report["scanned_media_dirs"],
        json!(["images", "thumbnails", "videos"])
    );
    assert_eq!(report["expected_files"], 1);
    assert_eq!(report["actual_files"], 1);
    assert!(report["elapsed_ms"].as_u64().is_some());
    for field in ["missing", "orphans", "size_mismatches", "hash_mismatches"] {
        assert!(report[field].is_array(), "{field} 应为数组");
    }
}

#[test]
fn missing_paths_are_sorted_alphabetically() {
    let dir = TempDir::new("report-missing-sort");
    let storage = dir.path().join("storage");
    std::fs::create_dir_all(&storage).unwrap();
    let manifest = write_manifest(
        dir.path(),
        &json!([
            { "path": "images/b.jpg" },
            { "path": "images/a.jpg" },
            { "path": "images/c.jpg" }
        ]),
    );

    let report = report_from_stdout(&run_audit(&storage, &manifest, &[]));

    assert_eq!(report["expected_files"], 3);
    assert_eq!(
        report["missing"],
        json!(["images/a.jpg", "images/b.jpg", "images/c.jpg"])
    );
}

#[test]
fn orphans_include_path_and_size_and_are_sorted() {
    let dir = TempDir::new("report-orphans");
    let storage = dir.path().join("storage");
    write_file(&storage.join("images/big.jpg"), b"hello");
    write_file(&storage.join("images/small.jpg"), b"z");
    let manifest = write_manifest(dir.path(), &json!([]));

    let report = report_from_stdout(&run_audit(&storage, &manifest, &[]));

    assert_eq!(report["actual_files"], 2);
    // orphans 按 path 字母序，含 path 与磁盘真实 size。
    assert_eq!(
        paths_of(&report, "orphans"),
        ["images/big.jpg", "images/small.jpg"]
    );
    let orphans = report["orphans"].as_array().unwrap();
    assert_eq!(orphans[0]["size"], 5);
    assert_eq!(orphans[1]["size"], 1);
}

#[test]
fn empty_manifest_and_empty_storage_report_zero_counts() {
    let dir = TempDir::new("report-empty");
    let storage = dir.path().join("storage");
    std::fs::create_dir_all(&storage).unwrap();
    let manifest = write_manifest(dir.path(), &json!([]));

    let report = report_from_stdout(&run_audit(&storage, &manifest, &[]));

    assert_eq!(report["expected_files"], 0);
    assert_eq!(report["actual_files"], 0);
    assert_eq!(report["missing"], json!([]));
    assert_eq!(report["orphans"], json!([]));
    assert_eq!(report["size_mismatches"], json!([]));
    assert_eq!(report["hash_mismatches"], json!([]));
}

#[test]
fn output_flag_writes_file_and_silences_stdout() {
    let dir = TempDir::new("report-output");
    let storage = dir.path().join("storage");
    write_file(&storage.join("images/a.jpg"), b"abc");
    let manifest = write_manifest(dir.path(), &json!([{ "path": "images/a.jpg", "size": 3 }]));
    let report_path = dir.path().join("report.json");

    let output = run_audit(
        &storage,
        &manifest,
        &["--output", report_path.to_str().unwrap()],
    );

    assert!(
        output.status.success(),
        "storage-audit 失败：{}",
        String::from_utf8_lossy(&output.stderr)
    );
    // 传 --output 时报告只写文件，stdout 不应再有 JSON。
    assert!(
        output.stdout.is_empty(),
        "传 --output 时 stdout 应为空，实际为：{}",
        String::from_utf8_lossy(&output.stdout)
    );
    assert!(output.stderr.is_empty());

    let report: Value = serde_json::from_slice(&std::fs::read(&report_path).unwrap())
        .expect("--output 文件应为合法 JSON");
    assert_eq!(report["expected_files"], 1);
    assert_eq!(report["actual_files"], 1);
    assert_eq!(report["missing"], json!([]));
}

#[test]
fn backslash_manifest_paths_are_normalized_to_forward_slashes() {
    let dir = TempDir::new("report-backslash");
    let storage = dir.path().join("storage");
    write_file(&storage.join("images/a.jpg"), b"abc");
    // manifest 里写 Windows 反斜杠路径，期望归一化为正斜杠后命中磁盘文件。
    let manifest = write_manifest(dir.path(), &json!([{ "path": "images\\a.jpg", "size": 3 }]));

    let report = report_from_stdout(&run_audit(&storage, &manifest, &[]));

    assert_eq!(report["actual_files"], 1);
    assert_eq!(report["missing"], json!([]));
    assert_eq!(report["orphans"], json!([]));
}

#[test]
fn absolute_and_parent_entries_are_ignored_but_expected_count_is_raw() {
    let dir = TempDir::new("report-ignore");
    let storage = dir.path().join("storage");
    write_file(&storage.join("images/keep.jpg"), b"abc");
    let absolute = if cfg!(windows) {
        "C:/abs.jpg"
    } else {
        "/abs.jpg"
    };
    let manifest = write_manifest(
        dir.path(),
        &json!([
            { "path": "images/keep.jpg", "size": 3 },
            // 绝对路径：被忽略
            { "path": absolute, "size": 1 },
            // Windows 上是 RootDir 组件、Unix 上是绝对路径：同样被忽略
            { "path": "/rooted.jpg", "size": 1 },
            // 父目录逃逸：被忽略
            { "path": "../escape.jpg", "size": 1 }
        ]),
    );

    let report = report_from_stdout(&run_audit(&storage, &manifest, &[]));

    // 当前契约：expected_files 是 manifest 原始条目数（含被忽略的 3 条）。
    assert_eq!(report["expected_files"], 4);
    assert_eq!(report["actual_files"], 1);
    assert_eq!(report["missing"], json!([]));
    assert_eq!(report["orphans"], json!([]));
}

#[test]
fn dot_prefixed_manifest_path_is_currently_reported_missing() {
    let dir = TempDir::new("report-dot");
    let storage = dir.path().join("storage");
    write_file(&storage.join("images/a.jpg"), b"abc");
    let manifest = write_manifest(
        dir.path(),
        &json!([{ "path": "./images/a.jpg", "size": 3 }]),
    );

    let report = report_from_stdout(&run_audit(&storage, &manifest, &[]));

    // 当前契约（缺陷，见 task-6 报告）：normalize_relative 用 Path::to_string_lossy()
    // 输出，保留 "./" 前缀而没有按组件重拼，于是 "./images/a.jpg" 与磁盘侧
    // "images/a.jpg" 不相等：同一个文件既进 missing（带 ./ 前缀）又进 orphans。
    // 若后续修复为剥离 CurDir，本用例应同步改为断言 missing/orphans 均为空。
    assert_eq!(report["expected_files"], 1);
    assert_eq!(report["actual_files"], 1);
    assert_eq!(report["missing"], json!(["./images/a.jpg"]));
    assert_eq!(paths_of(&report, "orphans"), ["images/a.jpg"]);
    // 因 key 与 actual 不相等，size/hash 校验也不会覆盖该条目。
    assert_eq!(report["size_mismatches"], json!([]));
    assert_eq!(report["hash_mismatches"], json!([]));
}
