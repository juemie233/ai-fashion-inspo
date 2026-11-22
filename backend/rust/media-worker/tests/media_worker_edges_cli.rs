//! media-worker 边界补充：多格式输入、非 ASCII 路径、batch 缺 id、archive 内容优先级。
//!
//! 与 `media_worker_cli.rs` 分工：那边覆盖主契约（hash/thumbnail/archive/batch 主路径与
//! 错误），这里补 Python 调用方可能遇到、但主契约用例没锁的边角：
//! - 用户素材常是 GIF/BMP/灰度图与中文文件名，Thumbnail 必须都能转 JPEG；
//! - batch 请求省略 id 时响应形状（Python 端按 id 关联，需知道缺省值）；
//! - archive 条目同时给 path 与 content 时以 content 为准（调用方约定）；
//! - 中文路径在 Windows argv/stdout 中文编码下不炸。

use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use image::{ImageBuffer, Rgb, RgbImage};
use serde_json::Value;
use sha2::{Digest, Sha256};

fn temp_dir(prefix: &str) -> PathBuf {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let path = std::env::temp_dir().join(format!("{prefix}-{stamp}"));
    std::fs::create_dir_all(&path).unwrap();
    path
}

fn run(args: &[&str], stdin_text: Option<&str>) -> std::process::Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_media-worker"));
    command.args(args);
    if let Some(text) = stdin_text {
        use std::io::Write;
        command
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());
        let mut child = command.spawn().unwrap();
        child
            .stdin
            .as_mut()
            .unwrap()
            .write_all(text.as_bytes())
            .unwrap();
        return child.wait_with_output().unwrap();
    }
    command.output().unwrap()
}

fn parse_json(output: &std::process::Output) -> Value {
    assert!(
        output.status.success(),
        "media-worker failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).unwrap()
}

fn write_image(path: &Path, width: u32, height: u32) {
    let image: RgbImage = ImageBuffer::from_fn(width, height, |x, y| {
        Rgb([(x % 256) as u8, (y % 256) as u8, 128])
    });
    image.save(path).unwrap();
}

#[test]
fn thumbnail_accepts_gif_bmp_and_grayscale_inputs() {
    let dir = temp_dir("media-worker-formats");
    let sources = [
        ("src.gif", image::ImageFormat::Gif),
        ("src.bmp", image::ImageFormat::Bmp),
    ];
    for (name, _format) in sources {
        let source = dir.join(name);
        write_image(&source, 120, 60);
        let output = dir.join(format!("{name}.jpg"));
        let body = parse_json(&run(
            &[
                "thumbnail",
                source.to_str().unwrap(),
                output.to_str().unwrap(),
            ],
            None,
        ));
        assert!(output.is_file());
        let bytes = std::fs::read(&output).unwrap();
        assert_eq!(&bytes[..2], &[0xFF, 0xD8], "输出必须是 JPEG：{name}");
        assert_eq!(
            (body["width"].as_u64(), body["height"].as_u64()),
            (Some(120), Some(60))
        );
    }

    // 灰度 PNG：也要能正常转 RGB JPEG
    let gray = image::GrayImage::from_fn(80, 40, |x, y| image::Luma([((x + y) % 256) as u8]));
    let gray_path = dir.join("gray.png");
    gray.save(&gray_path).unwrap();
    let out = dir.join("gray.jpg");
    let body = parse_json(&run(
        &[
            "thumbnail",
            gray_path.to_str().unwrap(),
            out.to_str().unwrap(),
        ],
        None,
    ));
    assert_eq!(
        (body["width"].as_u64(), body["height"].as_u64()),
        (Some(80), Some(40))
    );

    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn hash_and_thumbnail_support_non_ascii_paths() {
    let dir = temp_dir("media-worker-cjk");
    let source = dir.join("穿搭素材_001.png");
    write_image(&source, 32, 16);

    let hash = parse_json(&run(&["hash", source.to_str().unwrap()], None));
    let payload = std::fs::read(&source).unwrap();
    let expected = format!("{:x}", Sha256::digest(&payload));
    assert_eq!(hash["sha256"].as_str(), Some(expected.as_str()));
    assert_eq!(hash["bytes"].as_u64(), Some(payload.len() as u64));

    let out = dir.join("缩略图_001.jpg");
    let thumb = parse_json(&run(
        &["thumbnail", source.to_str().unwrap(), out.to_str().unwrap()],
        None,
    ));
    assert!(out.is_file());
    assert_eq!(thumb["width"].as_u64(), Some(32));

    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn batch_request_without_id_returns_null_id() {
    let dir = temp_dir("media-worker-batch-noid");
    let source = dir.join("a.bin");
    std::fs::write(&source, b"abc").unwrap();
    let request = serde_json::json!({"op": "hash", "input": source});
    let output = run(&["batch"], Some(&format!("{request}\n")));

    assert!(output.status.success());
    let line: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(line["ok"], true);
    assert!(line["id"].is_null(), "缺省 id 应为 null：{line}");

    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn archive_prefers_embedded_content_over_path() {
    let dir = temp_dir("media-worker-archive-content");
    let source = dir.join("from-disk.txt");
    std::fs::write(&source, b"disk-bytes").unwrap();
    let output = dir.join("out.zip");
    let manifest = serde_json::json!({
        "entries": [{
            "path": source.to_str().unwrap(),
            "content": "inline-bytes",
            "archive": "both.txt"
        }]
    });
    let body = parse_json(&run(
        &["archive", output.to_str().unwrap()],
        Some(&manifest.to_string()),
    ));
    assert_eq!(body["entries"].as_u64(), Some(1));
    assert_eq!(body["skipped"].as_u64(), Some(0));

    let file = std::fs::File::open(&output).unwrap();
    let mut zip = zip::ZipArchive::new(file).unwrap();
    assert_eq!(zip.len(), 1, "同时给 path/content 只应写入一个条目");
    let mut entry = zip.by_index(0).unwrap();
    let mut written = Vec::new();
    std::io::Read::read_to_end(&mut entry, &mut written).unwrap();
    assert_eq!(written, b"inline-bytes", "content 优先于 path");

    let _ = std::fs::remove_dir_all(dir);
}
