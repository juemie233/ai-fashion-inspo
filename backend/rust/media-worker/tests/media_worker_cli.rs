//! media-worker CLI 黑盒测试。
//!
//! 覆盖 `hash` / `thumbnail` / `archive` / `batch` 四个子命令对 Python 调用方
//! 暴露的 JSON 契约与错误行为：成功时 stdout 恰好一行 JSON，失败时退出码 1、
//! stdout 为空、stderr 带「media-worker 失败」前缀与中文原因。
//!
//! 所有用例只用 std 临时目录（结束即清理），不触网、不依赖外部工具；
//! 二进制路径通过 `CARGO_BIN_EXE_media-worker` 解析。

use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

const BIN: &str = env!("CARGO_BIN_EXE_media-worker");

/// b"abc" 的 SHA-256（公开已知向量）。
const SHA256_ABC: &str = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
/// 空文件的 SHA-256。
const SHA256_EMPTY: &str = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
/// 4096 字节（0x00..=0xFF 循环 16 次）的 SHA-256，由 Python hashlib 独立计算。
const SHA256_PATTERN_4096: &str =
    "c8f5d0341d54d951a71b136e6e2afcb14d11ed8489a7ae126a8fee0df6ecf193";
/// 2.5 MiB 确定性字节流（跨 1 MiB 读缓冲）的 SHA-256，由 Python hashlib 独立计算。
const SHA256_PATTERN_MULTI: &str =
    "02f083610a2cb6bfe9c1489524dc47d9b2421abeb0bbee2b68a80b684b388842";

// ---------------------------------------------------------------------------
// 通用辅助
// ---------------------------------------------------------------------------

/// 每个用例独立的临时目录：`<系统临时目录>/media-worker-cli-<tag>-<纳秒时间戳>/`。
fn temp_dir(tag: &str) -> PathBuf {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("系统时间应晚于 UNIX 纪元")
        .as_nanos();
    let dir = std::env::temp_dir().join(format!("media-worker-cli-{tag}-{stamp}"));
    std::fs::create_dir_all(&dir).expect("应能创建临时目录");
    dir
}

fn path_str(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

fn cleanup(dir: &Path) {
    let _ = std::fs::remove_dir_all(dir);
}

fn file_len(path: &Path) -> u64 {
    std::fs::metadata(path)
        .unwrap_or_else(|err| panic!("无法读取文件元数据 {}：{err}", path.display()))
        .len()
}

/// 运行 media-worker（stdin 置空，避免误读父进程输入）。
fn run(args: &[&str]) -> Output {
    Command::new(BIN)
        .args(args)
        .stdin(Stdio::null())
        .output()
        .expect("media-worker 应可启动")
}

/// 运行 media-worker 并向 stdin 写入 `stdin_text` 后等待 EOF 结束（archive / batch 用）。
fn run_with_stdin(args: &[&str], stdin_text: &str) -> Output {
    let mut child = Command::new(BIN)
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("media-worker 应可启动");
    child
        .stdin
        .take()
        .expect("stdin 管道应存在")
        .write_all(stdin_text.as_bytes())
        .expect("应能写入子进程 stdin");
    child
        .wait_with_output()
        .expect("应能等待 media-worker 结束")
}

/// 解析单次命令的成功 stdout：必须换行结尾、恰好一行合法 JSON。
fn stdout_json(output: &Output) -> Value {
    let text = String::from_utf8(output.stdout.clone()).expect("stdout 必须是 UTF-8");
    assert!(
        text.ends_with('\n'),
        "成功路径 stdout 应以换行结尾：{text:?}"
    );
    let mut lines = text.lines();
    let line = lines.next().expect("成功路径 stdout 不应为空");
    assert!(
        lines.next().is_none(),
        "成功路径 stdout 应恰好一行 JSON：{text:?}"
    );
    serde_json::from_str(line).unwrap_or_else(|err| panic!("stdout 不是合法 JSON（{err}）：{line}"))
}

/// 解析 batch 的输出：每个非空行一条 JSON，顺序保持不变。
fn stdout_json_lines(output: &Output) -> Vec<Value> {
    let text = String::from_utf8(output.stdout.clone()).expect("stdout 必须是 UTF-8");
    text.lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| {
            serde_json::from_str(line)
                .unwrap_or_else(|err| panic!("batch 输出行不是合法 JSON（{err}）：{line}"))
        })
        .collect()
}

/// 断言 CLI 失败契约：退出码 1、stdout 为空、stderr 带统一前缀与指定中文原因。
fn assert_cli_failure(output: &Output, expected_reason: &str) {
    assert!(
        !output.status.success(),
        "预期失败但成功退出：stdout={:?} stderr={:?}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(output.status.code(), Some(1), "sidecar 失败退出码应为 1");
    assert!(output.stdout.is_empty(), "失败路径 stdout 应为空");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("media-worker 失败"),
        "stderr 缺少统一失败前缀：{stderr}"
    );
    assert!(
        stderr.contains(expected_reason),
        "stderr 缺少原因「{expected_reason}」：{stderr}"
    );
}

/// 写入确定性的高熵 RGB PNG（噪声图，便于观测尺寸与 JPEG 质量差异）。
fn write_noise_png(path: &Path, width: u32, height: u32) {
    let mut image = image::RgbImage::new(width, height);
    for (x, y, pixel) in image.enumerate_pixels_mut() {
        let noise = x
            .wrapping_mul(1_103_515_245)
            .wrapping_add(y.wrapping_mul(12_345))
            .wrapping_add(6_789);
        *pixel = image::Rgb([(noise >> 16) as u8, (noise >> 8) as u8, noise as u8]);
    }
    image.save(path).expect("应能写出 PNG 测试图");
}

/// 写入带 alpha 通道的 RGBA PNG（一半像素全透明）。
fn write_alpha_png(path: &Path, width: u32, height: u32) {
    let mut image = image::RgbaImage::new(width, height);
    for (x, y, pixel) in image.enumerate_pixels_mut() {
        let alpha = if x % 2 == 0 { 0 } else { 200 };
        *pixel = image::Rgba([(x % 256) as u8, (y % 256) as u8, 128, alpha]);
    }
    image.save(path).expect("应能写出 RGBA PNG 测试图");
}

fn decode_image(path: &Path) -> image::DynamicImage {
    image::ImageReader::open(path)
        .expect("测试产物应可打开")
        .with_guessed_format()
        .expect("测试产物格式应可识别")
        .decode()
        .expect("测试产物应可解码")
}

/// 断言文件是 JPEG（SOI 标记 0xFF 0xD8 开头）。
fn assert_jpeg_file(path: &Path) {
    let bytes = std::fs::read(path).expect("JPEG 产物应存在");
    assert!(bytes.len() > 2, "JPEG 产物不应为空");
    assert_eq!(bytes[0], 0xFF, "JPEG 应以 SOI 首字节开头");
    assert_eq!(bytes[1], 0xD8, "JPEG 应以 SOI 次字节开头");
}

/// 读回 zip 的全部条目（顺序即写入顺序），返回 (条目名, 原始字节)。
fn read_zip_entries(path: &Path) -> Vec<(String, Vec<u8>)> {
    let file = std::fs::File::open(path).expect("zip 产物应存在");
    let mut archive = zip::ZipArchive::new(file).expect("zip 产物应可解析");
    let mut entries = Vec::new();
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index).expect("zip 条目应可读取");
        let mut bytes = Vec::new();
        entry.read_to_end(&mut bytes).expect("zip 条目应可读完");
        entries.push((entry.name().to_string(), bytes));
    }
    entries
}

/// 把一组 batch 请求拼成「一行一个 JSON」的 stdin 文本。
fn batch_stdin(lines: &[Value]) -> String {
    let mut text = lines
        .iter()
        .map(|line| line.to_string())
        .collect::<Vec<String>>()
        .join("\n");
    text.push('\n');
    text
}

// ---------------------------------------------------------------------------
// hash
// ---------------------------------------------------------------------------

#[test]
fn hash_known_vector_returns_json_contract() {
    let dir = temp_dir("hash-abc");
    let file = dir.join("abc.txt");
    std::fs::write(&file, b"abc").expect("应能写入测试文件");

    let output = run(&["hash", path_str(&file).as_str()]);
    assert!(
        output.status.success(),
        "hash 应成功：stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(output.stderr.is_empty(), "成功路径 stderr 应为空");

    let value = stdout_json(&output);
    assert_eq!(value["path"], path_str(&file).as_str());
    assert_eq!(value["sha256"], SHA256_ABC);
    assert_eq!(value["bytes"].as_u64(), Some(3));
    cleanup(&dir);
}

#[test]
fn hash_empty_file() {
    let dir = temp_dir("hash-empty");
    let file = dir.join("empty.bin");
    std::fs::write(&file, b"").expect("应能写入空文件");

    let output = run(&["hash", path_str(&file).as_str()]);
    assert!(
        output.status.success(),
        "hash 空文件应成功：stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let value = stdout_json(&output);
    assert_eq!(value["sha256"], SHA256_EMPTY);
    assert_eq!(value["bytes"].as_u64(), Some(0));
    cleanup(&dir);
}

#[test]
fn hash_binary_with_nul_bytes() {
    // 含 0x00 与非 UTF-8 字节，验证二进制按原始字节参与哈希（不做换行/编码转换）。
    let dir = temp_dir("hash-binary");
    let file = dir.join("pattern.bin");
    let bytes: Vec<u8> = (0..4096_usize).map(|index| (index % 256) as u8).collect();
    std::fs::write(&file, &bytes).expect("应能写入二进制测试文件");

    let output = run(&["hash", path_str(&file).as_str()]);
    assert!(output.status.success(), "hash 二进制应成功");

    let value = stdout_json(&output);
    assert_eq!(value["sha256"], SHA256_PATTERN_4096);
    assert_eq!(value["bytes"].as_u64(), Some(4096));
    cleanup(&dir);
}

#[test]
fn hash_multi_megabyte_file_crosses_read_buffer() {
    // 2.5 MiB：超过内部 1 MiB 的读取缓冲，验证流式分块哈希与总字节数。
    const MULTI_LEN: usize = 2_621_440;
    let dir = temp_dir("hash-multi");
    let file = dir.join("large.bin");
    let bytes: Vec<u8> = (0..MULTI_LEN)
        .map(|index| ((index * 37 + 11) % 256) as u8)
        .collect();
    std::fs::write(&file, &bytes).expect("应能写入大文件");

    let output = run(&["hash", path_str(&file).as_str()]);
    assert!(output.status.success(), "hash 大文件应成功");

    let value = stdout_json(&output);
    assert_eq!(value["sha256"], SHA256_PATTERN_MULTI);
    assert_eq!(value["bytes"].as_u64(), Some(MULTI_LEN as u64));
    cleanup(&dir);
}

#[test]
fn hash_missing_file_reports_chinese_error() {
    let dir = temp_dir("hash-missing");
    let missing = dir.join("does-not-exist.bin");

    let output = run(&["hash", path_str(&missing).as_str()]);
    assert_cli_failure(&output, "无法打开文件");
    assert!(
        String::from_utf8_lossy(&output.stderr).contains(&path_str(&missing)),
        "stderr 应包含缺失文件路径"
    );
    cleanup(&dir);
}

// ---------------------------------------------------------------------------
// thumbnail
// ---------------------------------------------------------------------------

#[test]
fn thumbnail_downscales_into_max_box() {
    let dir = temp_dir("thumb-box");
    let input = dir.join("portrait.png");
    let output = dir.join("thumb.jpg");
    write_noise_png(&input, 1200, 1800);

    let cli = run(&[
        "thumbnail",
        path_str(&input).as_str(),
        path_str(&output).as_str(),
        "--max-width",
        "400",
        "--max-height",
        "600",
    ]);
    assert!(
        cli.status.success(),
        "thumbnail 应成功：stderr={}",
        String::from_utf8_lossy(&cli.stderr)
    );
    assert!(cli.stderr.is_empty(), "成功路径 stderr 应为空");

    let value = stdout_json(&cli);
    assert_eq!(value["output"], path_str(&output).as_str());
    assert_eq!(value["source_width"].as_u64(), Some(1200));
    assert_eq!(value["source_height"].as_u64(), Some(1800));
    assert_eq!(value["width"].as_u64(), Some(400));
    assert_eq!(value["height"].as_u64(), Some(600));

    assert_jpeg_file(&output);
    let decoded = decode_image(&output);
    assert_eq!((decoded.width(), decoded.height()), (400, 600));
    let source_ratio = 1200.0_f64 / 1800.0_f64;
    let output_ratio = f64::from(decoded.width()) / f64::from(decoded.height());
    assert!(
        (source_ratio - output_ratio).abs() < 1e-6,
        "等比缩放不应改变宽高比：源 {source_ratio}，产物 {output_ratio}"
    );
    cleanup(&dir);
}

#[test]
fn thumbnail_keeps_aspect_ratio_for_portrait() {
    let dir = temp_dir("thumb-portrait");
    let input = dir.join("tall.png");
    let output = dir.join("tall.jpg");
    write_noise_png(&input, 1000, 2000);

    let cli = run(&[
        "thumbnail",
        path_str(&input).as_str(),
        path_str(&output).as_str(),
        "--max-width",
        "400",
        "--max-height",
        "600",
    ]);
    assert!(cli.status.success(), "thumbnail 应成功");

    let value = stdout_json(&cli);
    assert_eq!(value["source_width"].as_u64(), Some(1000));
    assert_eq!(value["source_height"].as_u64(), Some(2000));
    assert_eq!(value["width"].as_u64(), Some(300));
    assert_eq!(value["height"].as_u64(), Some(600));
    let decoded = decode_image(&output);
    assert_eq!((decoded.width(), decoded.height()), (300, 600));
    cleanup(&dir);
}

#[test]
fn thumbnail_limits_landscape_by_width() {
    let dir = temp_dir("thumb-landscape");
    let input = dir.join("wide.png");
    let output = dir.join("wide.jpg");
    write_noise_png(&input, 2000, 500);

    let cli = run(&[
        "thumbnail",
        path_str(&input).as_str(),
        path_str(&output).as_str(),
        "--max-width",
        "400",
        "--max-height",
        "600",
    ]);
    assert!(cli.status.success(), "thumbnail 应成功");

    let value = stdout_json(&cli);
    assert_eq!(value["width"].as_u64(), Some(400));
    assert_eq!(value["height"].as_u64(), Some(100));
    let decoded = decode_image(&output);
    assert_eq!((decoded.width(), decoded.height()), (400, 100));
    cleanup(&dir);
}

#[test]
fn thumbnail_does_not_upscale_small_image() {
    let dir = temp_dir("thumb-small");
    let input = dir.join("small.png");
    let output = dir.join("small.jpg");
    write_noise_png(&input, 100, 80);

    let cli = run(&[
        "thumbnail",
        path_str(&input).as_str(),
        path_str(&output).as_str(),
        "--max-width",
        "400",
        "--max-height",
        "600",
    ]);
    assert!(cli.status.success(), "thumbnail 应成功");

    let value = stdout_json(&cli);
    assert_eq!(value["source_width"].as_u64(), Some(100));
    assert_eq!(value["source_height"].as_u64(), Some(80));
    assert_eq!(value["width"].as_u64(), Some(100));
    assert_eq!(value["height"].as_u64(), Some(80));
    let decoded = decode_image(&output);
    assert_eq!((decoded.width(), decoded.height()), (100, 80));
    cleanup(&dir);
}

#[test]
fn thumbnail_converts_rgba_png_to_jpeg() {
    let dir = temp_dir("thumb-alpha");
    let input = dir.join("alpha.png");
    let output = dir.join("alpha.jpg");
    write_alpha_png(&input, 300, 200);

    let cli = run(&[
        "thumbnail",
        path_str(&input).as_str(),
        path_str(&output).as_str(),
    ]);
    assert!(cli.status.success(), "thumbnail 应成功");

    let value = stdout_json(&cli);
    assert_eq!(value["source_width"].as_u64(), Some(300));
    assert_eq!(value["source_height"].as_u64(), Some(200));
    assert_eq!(value["width"].as_u64(), Some(300));
    assert_eq!(value["height"].as_u64(), Some(200));

    assert_jpeg_file(&output);
    let decoded = decode_image(&output);
    assert_eq!((decoded.width(), decoded.height()), (300, 200));
    assert_eq!(
        decoded.color(),
        image::ColorType::Rgb8,
        "JPEG 产物应为无 alpha 的 RGB"
    );
    cleanup(&dir);
}

#[test]
fn thumbnail_creates_missing_output_parent_directory() {
    let dir = temp_dir("thumb-parent");
    let input = dir.join("input.png");
    let output = dir.join("nested").join("deeper").join("thumb.jpg");
    write_noise_png(&input, 120, 90);
    assert!(!output.exists(), "输出父目录初始不应存在");

    let cli = run(&[
        "thumbnail",
        path_str(&input).as_str(),
        path_str(&output).as_str(),
    ]);
    assert!(
        cli.status.success(),
        "thumbnail 应自动创建输出父目录：stderr={}",
        String::from_utf8_lossy(&cli.stderr)
    );
    assert!(output.exists(), "输出文件应存在");
    let decoded = decode_image(&output);
    assert_eq!((decoded.width(), decoded.height()), (120, 90));
    cleanup(&dir);
}

#[test]
fn thumbnail_quality_changes_output_bytes() {
    let dir = temp_dir("thumb-quality");
    let input = dir.join("noise.png");
    let low = dir.join("low.jpg");
    let high = dir.join("high.jpg");
    write_noise_png(&input, 800, 800);
    let input_s = path_str(&input);

    let low_output = run(&[
        "thumbnail",
        input_s.as_str(),
        path_str(&low).as_str(),
        "--max-width",
        "400",
        "--max-height",
        "600",
        "--quality",
        "1",
    ]);
    assert!(low_output.status.success(), "quality=1 应成功");
    let high_output = run(&[
        "thumbnail",
        input_s.as_str(),
        path_str(&high).as_str(),
        "--max-width",
        "400",
        "--max-height",
        "600",
        "--quality",
        "95",
    ]);
    assert!(high_output.status.success(), "quality=95 应成功");

    let low_value = stdout_json(&low_output);
    let high_value = stdout_json(&high_output);
    assert_eq!(low_value["width"].as_u64(), Some(400));
    assert_eq!(low_value["height"].as_u64(), Some(400));
    assert_eq!(high_value["width"].as_u64(), Some(400));
    assert_eq!(high_value["height"].as_u64(), Some(400));

    let low_len = file_len(&low);
    let high_len = file_len(&high);
    assert!(
        high_len > low_len,
        "quality=95 产物体积应大于 quality=1：{high_len} vs {low_len}"
    );
    assert_ne!(
        std::fs::read(&low).expect("应能读取低质量产物"),
        std::fs::read(&high).expect("应能读取高质量产物"),
        "不同 quality 的产物字节应不同"
    );
    cleanup(&dir);
}

#[test]
fn thumbnail_rejects_zero_max_width() {
    let dir = temp_dir("thumb-zero-width");
    let input = dir.join("input.png");
    let output = dir.join("out.jpg");
    write_noise_png(&input, 100, 100);

    let cli = run(&[
        "thumbnail",
        path_str(&input).as_str(),
        path_str(&output).as_str(),
        "--max-width",
        "0",
    ]);
    assert_cli_failure(&cli, "max_width / max_height 必须大于 0");
    assert!(!output.exists(), "参数校验失败时不应创建输出文件");
    cleanup(&dir);
}

#[test]
fn thumbnail_rejects_zero_max_height() {
    let dir = temp_dir("thumb-zero-height");
    let input = dir.join("input.png");
    let output = dir.join("out.jpg");
    write_noise_png(&input, 100, 100);

    let cli = run(&[
        "thumbnail",
        path_str(&input).as_str(),
        path_str(&output).as_str(),
        "--max-height",
        "0",
    ]);
    assert_cli_failure(&cli, "max_width / max_height 必须大于 0");
    assert!(!output.exists(), "参数校验失败时不应创建输出文件");
    cleanup(&dir);
}

#[test]
fn thumbnail_rejects_out_of_range_quality() {
    let dir = temp_dir("thumb-bad-quality");
    let input = dir.join("input.png");
    let output = dir.join("out.jpg");
    write_noise_png(&input, 100, 100);

    for quality in ["0", "101"] {
        let cli = run(&[
            "thumbnail",
            path_str(&input).as_str(),
            path_str(&output).as_str(),
            "--quality",
            quality,
        ]);
        assert_cli_failure(&cli, "JPEG quality 必须在 1~100");
    }
    assert!(!output.exists(), "参数校验失败时不应创建输出文件");
    cleanup(&dir);
}

#[test]
fn thumbnail_rejects_non_image_input() {
    let dir = temp_dir("thumb-not-image");
    let input = dir.join("not-an-image.png");
    let output = dir.join("out.jpg");
    std::fs::write(&input, b"definitely not an image").expect("应能写入坏图");

    let cli = run(&[
        "thumbnail",
        path_str(&input).as_str(),
        path_str(&output).as_str(),
    ]);
    assert_cli_failure(&cli, "无法解码图片");
    assert!(!output.exists(), "解码失败时不应创建输出文件");
    cleanup(&dir);
}

// ---------------------------------------------------------------------------
// archive
// ---------------------------------------------------------------------------

#[test]
fn archive_packs_real_files_and_embedded_content() {
    let dir = temp_dir("archive-pack");
    let source = dir.join("first.txt");
    let missing = dir.join("missing.txt");
    let zip_path = dir.join("out.zip");
    std::fs::write(&source, b"hello").expect("应能写入源文件");
    let manifest = json!({
        "entries": [
            {"path": path_str(&source), "archive": "0001_first.txt"},
            {"path": path_str(&missing), "archive": "0002_missing.txt"},
            {"content": "内嵌内容", "archive": "manifest.csv"},
            {"archive": "0004_neither.txt"}
        ]
    })
    .to_string();

    let cli = run_with_stdin(&["archive", path_str(&zip_path).as_str()], &manifest);
    assert!(
        cli.status.success(),
        "archive 应成功：stderr={}",
        String::from_utf8_lossy(&cli.stderr)
    );
    assert!(cli.stderr.is_empty(), "成功路径 stderr 应为空");

    let value = stdout_json(&cli);
    assert_eq!(value["output"], path_str(&zip_path).as_str());
    assert_eq!(value["entries"].as_u64(), Some(2));
    assert_eq!(value["skipped"].as_u64(), Some(2));
    assert_eq!(
        value["bytes"].as_u64(),
        Some(file_len(&zip_path)),
        "stdout 的 bytes 应与磁盘 zip 大小一致"
    );

    let entries = read_zip_entries(&zip_path);
    assert_eq!(entries.len(), 2, "zip 条目数应与 entries 字段一致");
    assert_eq!(entries[0].0, "0001_first.txt");
    assert_eq!(entries[0].1.as_slice(), b"hello".as_slice());
    assert_eq!(entries[1].0, "manifest.csv");
    assert_eq!(entries[1].1, "内嵌内容".as_bytes().to_vec());
    cleanup(&dir);
}

#[test]
fn archive_sanitizes_slashes_in_entry_names() {
    let dir = temp_dir("archive-sanitize");
    let zip_path = dir.join("out.zip");
    let manifest = json!({
        "entries": [
            {"content": "a", "archive": "dir/sub/file.txt"},
            {"content": "b", "archive": "a\\b.txt"},
            {"content": "c", "archive": "plain.txt"}
        ]
    })
    .to_string();

    let cli = run_with_stdin(&["archive", path_str(&zip_path).as_str()], &manifest);
    assert!(
        cli.status.success(),
        "archive 应成功：stderr={}",
        String::from_utf8_lossy(&cli.stderr)
    );

    let value = stdout_json(&cli);
    assert_eq!(value["entries"].as_u64(), Some(3));
    assert_eq!(value["skipped"].as_u64(), Some(0));

    let names: Vec<String> = read_zip_entries(&zip_path)
        .into_iter()
        .map(|(name, _)| name)
        .collect();
    assert_eq!(
        names,
        vec!["dir_sub_file.txt", "a_b.txt", "plain.txt"],
        "archive 名中的 / 与 \\ 应替换为 _"
    );
    cleanup(&dir);
}

#[test]
fn archive_rejects_empty_entries() {
    let dir = temp_dir("archive-empty");
    let zip_path = dir.join("out.zip");
    let manifest = json!({"entries": []}).to_string();

    let cli = run_with_stdin(&["archive", path_str(&zip_path).as_str()], &manifest);
    assert_cli_failure(&cli, "没有 entries");
    assert!(!zip_path.exists(), "空 entries 失败时不应创建 zip");
    cleanup(&dir);
}

#[test]
fn archive_creates_missing_output_parent_directory() {
    let dir = temp_dir("archive-parent");
    let zip_path = dir.join("a").join("b").join("c").join("out.zip");
    let manifest = json!({
        "entries": [{"content": "readme", "archive": "readme.md"}]
    })
    .to_string();

    let cli = run_with_stdin(&["archive", path_str(&zip_path).as_str()], &manifest);
    assert!(
        cli.status.success(),
        "archive 应自动创建输出父目录：stderr={}",
        String::from_utf8_lossy(&cli.stderr)
    );
    assert!(zip_path.exists(), "输出 zip 应存在");

    let value = stdout_json(&cli);
    assert_eq!(value["entries"].as_u64(), Some(1));
    let entries = read_zip_entries(&zip_path);
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0].0, "readme.md");
    assert_eq!(entries[0].1, b"readme".to_vec());
    cleanup(&dir);
}

#[test]
fn archive_rejects_invalid_manifest_json() {
    let dir = temp_dir("archive-bad-json");
    let zip_path = dir.join("out.zip");

    let cli = run_with_stdin(
        &["archive", path_str(&zip_path).as_str()],
        "{\"entries\": [oops}",
    );
    assert_cli_failure(&cli, "解析 archive manifest 失败");
    assert!(!zip_path.exists(), "manifest 解析失败时不应创建 zip");
    cleanup(&dir);
}

// ---------------------------------------------------------------------------
// batch
// ---------------------------------------------------------------------------

#[test]
fn batch_mixed_hash_and_thumbnail_preserves_order() {
    let dir = temp_dir("batch-mixed");
    let text_file = dir.join("abc.txt");
    let png = dir.join("photo.png");
    let jpg = dir.join("thumb.jpg");
    std::fs::write(&text_file, b"abc").expect("应能写入文本文件");
    write_noise_png(&png, 1200, 1800);
    let stdin_text = batch_stdin(&[
        json!({"id": "h1", "op": "hash", "input": path_str(&text_file)}),
        json!({
            "id": "t1",
            "op": "thumbnail",
            "input": path_str(&png),
            "output": path_str(&jpg),
            "max_width": 200,
            "max_height": 300
        }),
    ]);

    let cli = run_with_stdin(&["batch"], &stdin_text);
    assert!(
        cli.status.success(),
        "batch 应成功：stderr={}",
        String::from_utf8_lossy(&cli.stderr)
    );
    assert!(cli.stderr.is_empty(), "逐行业务失败不应写 stderr");

    let responses = stdout_json_lines(&cli);
    assert_eq!(responses.len(), 2, "每个请求应恰有一行响应");
    assert_eq!(responses[0]["id"], "h1");
    assert_eq!(responses[0]["ok"].as_bool(), Some(true));
    assert_eq!(responses[0]["sha256"], SHA256_ABC);
    assert_eq!(responses[0]["bytes"].as_u64(), Some(3));

    assert_eq!(responses[1]["id"], "t1");
    assert_eq!(responses[1]["ok"].as_bool(), Some(true));
    assert_eq!(responses[1]["output"], path_str(&jpg).as_str());
    assert_eq!(responses[1]["width"].as_u64(), Some(200));
    assert_eq!(responses[1]["height"].as_u64(), Some(300));
    assert_eq!(responses[1]["source_width"].as_u64(), Some(1200));
    assert_eq!(responses[1]["source_height"].as_u64(), Some(1800));

    let decoded = decode_image(&jpg);
    assert_eq!((decoded.width(), decoded.height()), (200, 300));
    cleanup(&dir);
}

#[test]
fn batch_unknown_op_returns_error_and_keeps_id() {
    let dir = temp_dir("batch-unknown-op");
    let stdin_text = batch_stdin(&[json!({"id": "x9", "op": "resize", "input": "whatever"})]);

    let cli = run_with_stdin(&["batch"], &stdin_text);
    assert!(cli.status.success(), "未知 op 是行级错误，进程仍应 0 退出");
    assert!(cli.stderr.is_empty(), "逐行业务失败不应写 stderr");

    let responses = stdout_json_lines(&cli);
    assert_eq!(responses.len(), 1);
    assert_eq!(responses[0]["id"], "x9");
    assert_eq!(responses[0]["ok"].as_bool(), Some(false));
    let error = responses[0]["error"].as_str().unwrap_or_default();
    assert!(
        error.contains("未知 op：resize"),
        "error 应说明未知 op：{error}"
    );
    cleanup(&dir);
}

#[test]
fn batch_bad_json_line_does_not_crash() {
    let dir = temp_dir("batch-bad-json");
    let text_file = dir.join("abc.txt");
    std::fs::write(&text_file, b"abc").expect("应能写入文本文件");
    let valid = json!({"id": "h1", "op": "hash", "input": path_str(&text_file)}).to_string();
    let stdin_text = format!("{{oops}}\n{valid}\n");

    let cli = run_with_stdin(&["batch"], &stdin_text);
    assert!(cli.status.success(), "坏 JSON 行不应让进程崩溃或非零退出");
    assert!(
        cli.stderr.is_empty(),
        "坏 JSON 行应以 JSON 错误返回，不写 stderr"
    );

    let responses = stdout_json_lines(&cli);
    assert_eq!(responses.len(), 2, "坏行之后的有效请求仍应被处理");
    assert_eq!(responses[0]["ok"].as_bool(), Some(false));
    assert!(responses[0]["error"]
        .as_str()
        .unwrap_or_default()
        .contains("JSON 解析失败"));
    assert!(
        responses[0].get("id").is_none(),
        "JSON 解析失败时无法回传 id"
    );
    assert_eq!(responses[1]["id"], "h1");
    assert_eq!(responses[1]["ok"].as_bool(), Some(true));
    assert_eq!(responses[1]["sha256"], SHA256_ABC);
    cleanup(&dir);
}

#[test]
fn batch_missing_arguments_report_param_errors() {
    let dir = temp_dir("batch-missing-args");
    let png = dir.join("p.png");
    write_noise_png(&png, 20, 20);
    let lines = vec![
        json!({"id": "m1", "op": "hash"}),
        json!({"id": "m2", "op": "thumbnail", "input": path_str(&png)}),
        json!({"id": "m3", "op": "thumbnail", "output": path_str(&dir.join("x.jpg"))}),
        json!({"id": "m4", "op": "hash", "input": path_str(&dir.join("missing.bin"))}),
    ];
    let stdin_text = batch_stdin(&lines);

    let cli = run_with_stdin(&["batch"], &stdin_text);
    assert!(
        cli.status.success(),
        "行级参数错误不应让 batch 非零退出：stderr={}",
        String::from_utf8_lossy(&cli.stderr)
    );

    let responses = stdout_json_lines(&cli);
    assert_eq!(responses.len(), 4);
    let expected = [
        ("m1", "hash 缺少 input"),
        ("m2", "thumbnail 缺少 output"),
        ("m3", "thumbnail 缺少 input"),
        ("m4", "无法打开文件"),
    ];
    for (index, (id, reason)) in expected.iter().enumerate() {
        let response = &responses[index];
        assert_eq!(response["id"], *id);
        assert_eq!(
            response["ok"].as_bool(),
            Some(false),
            "第 {index} 行应返回 ok=false"
        );
        let error = response["error"].as_str().unwrap_or_default();
        assert!(
            error.contains(*reason),
            "第 {index} 行 error 应包含「{reason}」：{error}"
        );
    }
    cleanup(&dir);
}

#[test]
fn batch_ignores_blank_lines() {
    let dir = temp_dir("batch-blank-lines");
    let file = dir.join("abc.txt");
    std::fs::write(&file, b"abc").expect("应能写入文本文件");
    let first = json!({"id": "a", "op": "hash", "input": path_str(&file)}).to_string();
    let second = json!({"id": "b", "op": "hash", "input": path_str(&file)}).to_string();
    let stdin_text = format!("\n{first}\n   \n\n{second}\n\n");

    let cli = run_with_stdin(&["batch"], &stdin_text);
    assert!(cli.status.success(), "batch 应成功");

    let responses = stdout_json_lines(&cli);
    assert_eq!(responses.len(), 2, "空行不应产生响应");
    assert_eq!(responses[0]["id"], "a");
    assert_eq!(responses[1]["id"], "b");
    cleanup(&dir);
}

#[test]
fn batch_applies_default_thumbnail_options() {
    let dir = temp_dir("batch-defaults");
    let png = dir.join("big.png");
    let batch_jpg = dir.join("batch.jpg");
    let cli_jpg = dir.join("cli.jpg");
    write_noise_png(&png, 1200, 1800);
    let stdin_text = batch_stdin(&[json!({
        "id": "d1",
        "op": "thumbnail",
        "input": path_str(&png),
        "output": path_str(&batch_jpg)
    })]);

    let cli = run_with_stdin(&["batch"], &stdin_text);
    assert!(cli.status.success(), "batch 应成功");
    let responses = stdout_json_lines(&cli);
    assert_eq!(responses.len(), 1);
    assert_eq!(responses[0]["ok"].as_bool(), Some(true));
    assert_eq!(
        responses[0]["width"].as_u64(),
        Some(400),
        "默认 max_width=400"
    );
    assert_eq!(
        responses[0]["height"].as_u64(),
        Some(600),
        "默认 max_height=600"
    );

    // 默认参数应等价于 CLI 显式 --max-width 400 --max-height 600 --quality 85。
    let explicit = run(&[
        "thumbnail",
        path_str(&png).as_str(),
        path_str(&cli_jpg).as_str(),
        "--max-width",
        "400",
        "--max-height",
        "600",
        "--quality",
        "85",
    ]);
    assert!(explicit.status.success(), "显式参数 thumbnail 应成功");
    assert_eq!(
        std::fs::read(&batch_jpg).expect("应能读取 batch 产物"),
        std::fs::read(&cli_jpg).expect("应能读取 CLI 产物"),
        "batch 默认 max_width/max_height/quality 应与 CLI 默认值完全一致"
    );
    cleanup(&dir);
}

#[test]
fn batch_finishes_at_eof_without_trailing_newline() {
    let dir = temp_dir("batch-eof");
    let file = dir.join("abc.txt");
    std::fs::write(&file, b"abc").expect("应能写入文本文件");
    // 故意不带结尾换行：验证 EOF 时仍处理完最后一行并正常 0 退出。
    let stdin_text = json!({"id": "eof1", "op": "hash", "input": path_str(&file)}).to_string();

    let cli = run_with_stdin(&["batch"], &stdin_text);
    assert!(
        cli.status.success(),
        "EOF 无结尾换行时应正常结束：stderr={}",
        String::from_utf8_lossy(&cli.stderr)
    );

    let responses = stdout_json_lines(&cli);
    assert_eq!(responses.len(), 1);
    assert_eq!(responses[0]["id"], "eof1");
    assert_eq!(responses[0]["ok"].as_bool(), Some(true));
    assert_eq!(responses[0]["sha256"], SHA256_ABC);
    cleanup(&dir);
}
