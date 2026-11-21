//! media-worker v0：给 Python 后端调用的媒体计算 sidecar。
//!
//! 本阶段只提供两个无业务状态的能力：
//! - `hash`：流式 SHA-256
//! - `thumbnail`：等比缩略图（不放大、不拉伸、输出 JPEG）
//!
//! 刻意不接数据库、不读业务配置、不处理 pHash/视频。失败时返回非零退出码，
//! 由 Python 调用方决定是否回退到 Pillow 实现。

use std::fs::File;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};
use image::imageops::FilterType;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

#[derive(Parser, Debug)]
#[command(
    name = "media-worker",
    version,
    about = "媒体处理 sidecar（SHA-256 / 缩略图）"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand, Debug)]
enum Command {
    /// 流式计算文件 SHA-256（小写十六进制）
    Hash {
        /// 输入文件路径
        input: PathBuf,
    },
    /// 生成等比缩略图（不放大、不拉伸）并输出 JPEG
    Thumbnail {
        /// 输入图片路径
        input: PathBuf,
        /// 输出 JPEG 路径
        output: PathBuf,
        /// 最大宽度
        #[arg(long, default_value_t = 400)]
        max_width: u32,
        /// 最大高度
        #[arg(long, default_value_t = 600)]
        max_height: u32,
        /// JPEG 质量（1~100）
        #[arg(long, default_value_t = 85)]
        quality: u8,
    },
    /// 常驻批量模式：stdin 逐行读 JSON，stdout 逐行回 JSON（避免每张图启动进程）
    Batch,
    /// 流式 ZIP：从 stdin 读 {output, entries:[{path,archive}]}，不把整个 ZIP 放内存
    Archive {
        /// 输出 zip 路径（同时覆盖 manifest 内的 output）
        output: PathBuf,
    },
}

#[derive(Serialize)]
struct HashOutput {
    path: String,
    sha256: String,
    bytes: u64,
}

#[derive(Serialize)]
struct ThumbnailOutput {
    output: String,
    width: u32,
    height: u32,
    source_width: u32,
    source_height: u32,
}

fn main() {
    if let Err(err) = run() {
        eprintln!("media-worker 失败：{err:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Command::Hash { input } => {
            let (sha256, bytes) = sha256_file(&input)?;
            print_json(&HashOutput {
                path: input.to_string_lossy().into_owned(),
                sha256,
                bytes,
            })
        }
        Command::Thumbnail {
            input,
            output,
            max_width,
            max_height,
            quality,
        } => {
            let output = make_thumbnail(&input, &output, max_width, max_height, quality)?;
            print_json(&output)
        }
        Command::Batch => run_batch(),
        Command::Archive { output } => {
            let result = write_archive(&output)?;
            print_json(&result)
        }
    }
}

#[derive(Deserialize)]
struct ArchiveEntry {
    #[serde(default)]
    path: Option<PathBuf>,
    #[serde(default)]
    content: Option<String>,
    archive: String,
}

#[derive(Deserialize)]
struct ArchiveManifest {
    entries: Vec<ArchiveEntry>,
}

#[derive(Serialize)]
struct ArchiveOutput {
    output: String,
    entries: usize,
    skipped: usize,
    bytes: u64,
}

fn write_archive(output: &Path) -> Result<ArchiveOutput> {
    let manifest: ArchiveManifest =
        serde_json::from_reader(std::io::stdin().lock()).context("解析 archive manifest 失败")?;
    write_archive_entries(output, &manifest.entries)
}

fn write_archive_entries(output: &Path, entries: &[ArchiveEntry]) -> Result<ArchiveOutput> {
    if entries.is_empty() {
        bail!("archive manifest 没有 entries");
    }
    if let Some(parent) = output.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("创建输出目录失败：{}", parent.display()))?;
        }
    }
    let file =
        File::create(output).with_context(|| format!("无法创建输出文件：{}", output.display()))?;
    let mut zip = zip::ZipWriter::new(file);
    let options = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated);
    let mut written = 0_usize;
    let mut skipped = 0_usize;
    for entry in entries {
        let name = entry.archive.replace(['/', '\\'], "_");
        if let Some(content) = &entry.content {
            zip.start_file(name, options)
                .with_context(|| format!("ZIP 写入条目失败：{}", entry.archive))?;
            zip.write_all(content.as_bytes())
                .with_context(|| format!("ZIP 写入内嵌内容失败：{}", entry.archive))?;
            written += 1;
            continue;
        }
        let Some(path) = &entry.path else {
            skipped += 1;
            continue;
        };
        let source = match File::open(path) {
            Ok(file) => file,
            Err(_) => {
                skipped += 1;
                continue;
            }
        };
        zip.start_file(name, options)
            .with_context(|| format!("ZIP 写入条目失败：{}", entry.archive))?;
        std::io::copy(&mut BufReader::new(source), &mut zip)
            .with_context(|| format!("ZIP 复制文件失败：{}", path.display()))?;
        written += 1;
    }
    zip.finish().context("完成 ZIP 失败")?;
    let bytes = std::fs::metadata(output)
        .with_context(|| format!("读取输出文件失败：{}", output.display()))?
        .len();
    Ok(ArchiveOutput {
        output: output.to_string_lossy().into_owned(),
        entries: written,
        skipped,
        bytes,
    })
}

#[derive(Deserialize)]
struct BatchRequest {
    #[serde(default)]
    id: Option<String>,
    op: String,
    #[serde(default)]
    input: Option<PathBuf>,
    #[serde(default)]
    output: Option<PathBuf>,
    #[serde(default)]
    max_width: Option<u32>,
    #[serde(default)]
    max_height: Option<u32>,
    #[serde(default)]
    quality: Option<u8>,
}

fn run_batch() -> Result<()> {
    let stdin = std::io::stdin();
    let stdout = std::io::stdout();
    let mut writer = stdout.lock();
    for line in stdin.lock().lines() {
        let line = line.context("读取 batch stdin 失败")?;
        if line.trim().is_empty() {
            continue;
        }
        let response = handle_batch_line(&line);
        serde_json::to_writer(&mut writer, &response).context("输出 batch JSON 失败")?;
        writer.write_all(b"\n").context("输出 batch 换行失败")?;
        writer.flush().context("刷新 batch stdout 失败")?;
    }
    Ok(())
}

fn handle_batch_line(line: &str) -> serde_json::Value {
    let request: BatchRequest = match serde_json::from_str(line) {
        Ok(request) => request,
        Err(err) => {
            return serde_json::json!({"ok": false, "error": format!("JSON 解析失败：{err}")})
        }
    };
    let id = request.id.clone();
    let result = (|| -> Result<serde_json::Value> {
        match request.op.as_str() {
            "hash" => {
                let input = request.input.as_deref().context("hash 缺少 input")?;
                let (sha256, bytes) = sha256_file(input)?;
                Ok(serde_json::json!({
                    "id": id,
                    "ok": true,
                    "sha256": sha256,
                    "bytes": bytes,
                }))
            }
            "thumbnail" => {
                let input = request.input.as_deref().context("thumbnail 缺少 input")?;
                let output = request.output.as_deref().context("thumbnail 缺少 output")?;
                let thumb = make_thumbnail(
                    input,
                    output,
                    request.max_width.unwrap_or(400),
                    request.max_height.unwrap_or(600),
                    request.quality.unwrap_or(85),
                )?;
                Ok(serde_json::json!({
                    "id": id,
                    "ok": true,
                    "output": thumb.output,
                    "width": thumb.width,
                    "height": thumb.height,
                    "source_width": thumb.source_width,
                    "source_height": thumb.source_height,
                }))
            }
            other => bail!("未知 op：{other}"),
        }
    })();
    match result {
        Ok(value) => value,
        Err(err) => serde_json::json!({"id": id, "ok": false, "error": format!("{err:#}")}),
    }
}

fn print_json<T: Serialize>(value: &T) -> Result<()> {
    let stdout = std::io::stdout();
    let mut lock = stdout.lock();
    serde_json::to_writer(&mut lock, value).context("输出 JSON 失败")?;
    lock.write_all(b"\n").context("输出换行失败")?;
    Ok(())
}

fn sha256_file(path: &Path) -> Result<(String, u64)> {
    let file = File::open(path).with_context(|| format!("无法打开文件：{}", path.display()))?;
    let mut reader = BufReader::with_capacity(1 << 20, file);
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; 1 << 20];
    let mut total = 0_u64;
    loop {
        let read = reader
            .read(&mut buffer)
            .with_context(|| format!("读取文件失败：{}", path.display()))?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
        total += read as u64;
    }
    Ok((format!("{:x}", hasher.finalize()), total))
}

fn make_thumbnail(
    input: &Path,
    output: &Path,
    max_width: u32,
    max_height: u32,
    quality: u8,
) -> Result<ThumbnailOutput> {
    if max_width == 0 || max_height == 0 {
        bail!("max_width / max_height 必须大于 0");
    }
    if !(1..=100).contains(&quality) {
        bail!("JPEG quality 必须在 1~100");
    }

    let image = image::ImageReader::open(input)
        .with_context(|| format!("无法打开图片：{}", input.display()))?
        .with_guessed_format()
        .context("无法判断图片格式")?
        .decode()
        .with_context(|| format!("无法解码图片：{}", input.display()))?;

    let (source_width, source_height) = (image.width(), image.height());
    if source_width == 0 || source_height == 0 {
        bail!("图片尺寸为 0：{}", input.display());
    }

    // 与 Pillow Image.thumbnail 的语义一致：只缩小，不放大；保持宽高比。
    let (target_width, target_height) =
        contain_size(source_width, source_height, max_width, max_height);
    let thumb = if target_width == source_width && target_height == source_height {
        image
    } else {
        image.resize_exact(target_width, target_height, FilterType::Lanczos3)
    };

    if let Some(parent) = output.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("创建输出目录失败：{}", parent.display()))?;
        }
    }

    let rgb = thumb.to_rgb8();
    let mut encoder = image::codecs::jpeg::JpegEncoder::new_with_quality(
        File::create(output).with_context(|| format!("无法创建输出文件：{}", output.display()))?,
        quality,
    );
    encoder
        .encode(
            rgb.as_raw(),
            rgb.width(),
            rgb.height(),
            image::ExtendedColorType::Rgb8,
        )
        .with_context(|| format!("写入 JPEG 失败：{}", output.display()))?;

    Ok(ThumbnailOutput {
        output: output.to_string_lossy().into_owned(),
        width: rgb.width(),
        height: rgb.height(),
        source_width,
        source_height,
    })
}

/// 计算放进 `(max_w, max_h)` 后的等比尺寸，绝不放大。
///
/// 复刻 Pillow `Image.thumbnail` 的 `preserve_aspect_ratio`：
/// 先判断最大宽高比落在哪一侧，再在 floor/ceil 两个候选里选更接近原宽高比的。
/// 这样常见的 2160×3840 → 337×600、1982×3522 → 338×600 不会出现 1px 偏色。
fn contain_size(width: u32, height: u32, max_w: u32, max_h: u32) -> (u32, u32) {
    if max_w >= width && max_h >= height {
        return (width, height);
    }
    let aspect = width as f64 / height as f64;
    let box_aspect = max_w as f64 / max_h as f64;
    if box_aspect >= aspect {
        let target = max_h as f64 * aspect;
        let chosen = choose_round(target, |n| (aspect - n / max_h as f64).abs());
        (chosen.max(1), max_h)
    } else {
        let target = max_w as f64 / aspect;
        let chosen = choose_round(target, |n| (aspect - max_w as f64 / n).abs());
        (max_w, chosen.max(1))
    }
}

/// 在 `floor(number)` / `ceil(number)` 中选 key 更小的一个（Pillow 的 round_aspect）。
fn choose_round<F>(number: f64, key: F) -> u32
where
    F: Fn(f64) -> f64,
{
    let floor = number.floor() as u32;
    let ceil = number.ceil() as u32;
    if key(floor as f64) <= key(ceil as f64) {
        floor
    } else {
        ceil
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::{Rgb, RgbImage};
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_path(prefix: &str, suffix: &str) -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!("{prefix}-{stamp}.{suffix}"))
    }

    #[test]
    fn contain_size_does_not_upscale_and_keeps_ratio() {
        assert_eq!(contain_size(1000, 2000, 400, 600), (300, 600));
        assert_eq!(contain_size(100, 100, 400, 600), (100, 100));
        assert_eq!(contain_size(4000, 3000, 400, 600), (400, 300));
        assert_eq!(contain_size(2160, 3840, 400, 600), (337, 600));
        assert_eq!(contain_size(1982, 3522, 400, 600), (338, 600));
        assert_eq!(contain_size(2160, 2828, 400, 600), (400, 524));
    }

    #[test]
    fn sha256_file_matches_known_vector() {
        let path = temp_path("media-worker-hash", "txt");
        std::fs::write(&path, b"abc").unwrap();
        let (hash, bytes) = sha256_file(&path).unwrap();
        assert_eq!(
            hash,
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(bytes, 3);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn thumbnail_is_bounded_and_readable() {
        let input = temp_path("media-worker-in", "png");
        let output = temp_path("media-worker-out", "jpg");
        let image = RgbImage::from_pixel(1200, 1800, Rgb([30, 60, 90]));
        image.save(&input).unwrap();

        let result = make_thumbnail(&input, &output, 400, 600, 85).unwrap();
        assert_eq!((result.width, result.height), (400, 600));
        assert_eq!((result.source_width, result.source_height), (1200, 1800));

        let decoded = image::open(&output).unwrap();
        assert_eq!((decoded.width(), decoded.height()), (400, 600));

        let _ = std::fs::remove_file(input);
        let _ = std::fs::remove_file(output);
    }

    #[test]
    fn archive_streams_entries_and_skips_missing() {
        let dir = temp_path("media-worker-archive", "dir");
        std::fs::create_dir_all(&dir).unwrap();
        let first = dir.join("first.txt");
        let output = dir.join("out.zip");
        std::fs::write(&first, b"hello").unwrap();
        let entries = vec![
            ArchiveEntry {
                path: Some(first.clone()),
                content: None,
                archive: "0001_first.txt".to_string(),
            },
            ArchiveEntry {
                path: Some(dir.join("missing.txt")),
                content: None,
                archive: "0002_missing.txt".to_string(),
            },
            ArchiveEntry {
                path: None,
                content: Some("manifest".to_string()),
                archive: "manifest.csv".to_string(),
            },
        ];
        let result = write_archive_entries(&output, &entries).unwrap();
        assert_eq!(result.entries, 2);
        assert_eq!(result.skipped, 1);
        assert!(result.bytes > 0);
        let mut archive = zip::ZipArchive::new(File::open(&output).unwrap()).unwrap();
        assert_eq!(archive.len(), 2);
        let mut text = String::new();
        archive
            .by_index(0)
            .unwrap()
            .read_to_string(&mut text)
            .unwrap();
        assert_eq!(text, "hello");
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn rejects_zero_dimension() {
        let input = temp_path("media-worker-zero", "png");
        let output = temp_path("media-worker-zero-out", "jpg");
        RgbImage::from_pixel(10, 10, Rgb([0, 0, 0]))
            .save(&input)
            .unwrap();
        assert!(make_thumbnail(&input, &output, 0, 600, 85).is_err());
        let _ = std::fs::remove_file(input);
        let _ = std::fs::remove_file(output);
    }
}
