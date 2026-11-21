//! storage-audit CLI 黑盒测试：验证缺失/孤立/大小不一致报告。

use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

fn temp_dir() -> PathBuf {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let path = std::env::temp_dir().join(format!("storage-audit-cli-{stamp}"));
    std::fs::create_dir_all(&path).unwrap();
    path
}

fn write_manifest(root: &Path) -> PathBuf {
    let manifest = serde_json::json!({
        "expected": [
            {"path": "images/keep.jpg", "size": 4},
            {"path": "images/wrong-size.jpg", "size": 999},
            {"path": "images/missing.jpg"}
        ]
    });
    let path = root.join("manifest.json");
    std::fs::write(&path, manifest.to_string()).unwrap();
    path
}

#[test]
fn reports_missing_orphan_and_size_mismatch() {
    let dir = temp_dir();
    let storage = dir.join("storage");
    std::fs::create_dir_all(storage.join("images")).unwrap();
    std::fs::write(storage.join("images/keep.jpg"), b"keep").unwrap();
    std::fs::write(storage.join("images/wrong-size.jpg"), b"x").unwrap();
    std::fs::write(storage.join("images/orphan.jpg"), b"orphan").unwrap();
    let manifest = write_manifest(&dir);
    let report = dir.join("report.json");

    let output = Command::new(env!("CARGO_BIN_EXE_storage_audit"))
        .arg(&storage)
        .arg(&manifest)
        .arg("--output")
        .arg(&report)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "storage-audit failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );

    let data: Value = serde_json::from_slice(&std::fs::read(&report).unwrap()).unwrap();
    assert_eq!(data["expected_files"], 3);
    assert_eq!(data["actual_files"], 3);
    assert!(data["missing"]
        .as_array()
        .unwrap()
        .iter()
        .any(|v| v == "images/missing.jpg"));
    assert!(data["orphans"]
        .as_array()
        .unwrap()
        .iter()
        .any(|v| v == "images/orphan.jpg"));
    assert_eq!(data["size_mismatches"].as_array().unwrap().len(), 1);
    assert_eq!(data["hash_mismatches"].as_array().unwrap().len(), 0);

    let _ = std::fs::remove_dir_all(dir);
}
