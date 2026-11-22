//! storage-audit 边角契约：重复 manifest 条目与空 `--dirs`。
//!
//! 这两类输入不会出现在正常导出里（Python 侧按数据库主键导出，dirs 固定），
//! 但 sidecar 是独立 CLI，契约要写死避免以后重构时行为漂移：
//! - 同一路径在 manifest 里出现两次：expected_files 按原始条数计，实际校验只保留
//!   最后一条（BTreeMap 覆盖），不应产生 missing/orphan 误报；
//! - `--dirs ""`：不扫描任何目录，actual_files=0，manifest 条目全部判 missing。

use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

fn temp_dir() -> PathBuf {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let path = std::env::temp_dir().join(format!("storage-audit-edges-{stamp}"));
    std::fs::create_dir_all(&path).unwrap();
    path
}

fn run(storage: &Path, manifest: &Path, extra: &[&str]) -> std::process::Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_storage_audit"));
    command.arg(storage).arg(manifest).args(extra);
    command.output().unwrap()
}

fn write_manifest(dir: &Path, entries: Value) -> PathBuf {
    let path = dir.join("manifest.json");
    let body = serde_json::json!({"expected": entries});
    std::fs::write(&path, body.to_string()).unwrap();
    path
}

#[test]
fn duplicate_manifest_paths_count_raw_but_validate_last_entry() {
    let dir = temp_dir();
    let storage = dir.join("storage");
    std::fs::create_dir_all(storage.join("images")).unwrap();
    std::fs::write(storage.join("images/a.jpg"), b"abcd").unwrap();
    // 同一路径两条：第一条尺寸故意不对，第二条正确 —— 后者覆盖前者，不应报 size mismatch
    let manifest = write_manifest(
        &dir,
        serde_json::json!([
            {"path": "images/a.jpg", "size": 999},
            {"path": "images/a.jpg", "size": 4}
        ]),
    );

    let output = run(
        &storage,
        &manifest,
        &["--output", dir.join("report.json").to_str().unwrap()],
    );
    assert!(
        output.status.success(),
        "storage-audit failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let report: Value =
        serde_json::from_slice(&std::fs::read(dir.join("report.json")).unwrap()).unwrap();
    assert_eq!(
        report["expected_files"].as_u64(),
        Some(1),
        "按去重后的比对条数计（同路径后一条覆盖前一条）"
    );
    assert_eq!(report["actual_files"].as_u64(), Some(1));
    assert_eq!(report["missing"].as_array().unwrap().len(), 0);
    assert_eq!(report["orphans"].as_array().unwrap().len(), 0);
    assert_eq!(
        report["size_mismatches"].as_array().unwrap().len(),
        0,
        "后一条正确尺寸覆盖前一条，不应误报"
    );

    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn empty_dirs_scans_nothing_and_reports_all_missing() {
    let dir = temp_dir();
    let storage = dir.join("storage");
    std::fs::create_dir_all(storage.join("images")).unwrap();
    std::fs::write(storage.join("images/a.jpg"), b"abcd").unwrap();
    let manifest = write_manifest(&dir, serde_json::json!([{"path": "images/a.jpg"}]));

    let output = run(
        &storage,
        &manifest,
        &[
            "--dirs",
            "",
            "--output",
            dir.join("report.json").to_str().unwrap(),
        ],
    );
    assert!(output.status.success(), "--dirs 为空不应报错");
    let report: Value =
        serde_json::from_slice(&std::fs::read(dir.join("report.json")).unwrap()).unwrap();
    assert_eq!(report["actual_files"].as_u64(), Some(0), "不扫描任何目录");
    assert_eq!(report["scanned_media_dirs"].as_array().unwrap().len(), 0);
    assert_eq!(report["orphans"].as_array().unwrap().len(), 0);
    assert_eq!(
        report["missing"].as_array().unwrap().len(),
        1,
        "未扫描目录时 manifest 条目全部计 missing"
    );

    let _ = std::fs::remove_dir_all(dir);
}
