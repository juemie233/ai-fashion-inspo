# media-worker

Python 后端的媒体处理 sidecar 试点（Rust）。

当前只提供：

- `hash`：流式 SHA-256
- `thumbnail`：等比缩略图，不放大、不拉伸，输出 JPEG

## 非 C 盘工具链

本机 Rust 工具链和构建缓存必须放在 D 盘：

```bash
export RUSTUP_HOME='D:\tools\rustup'
export CARGO_HOME='D:\tools\cargo'
export CARGO_TARGET_DIR='D:\tools\cargo-target\fashion-inspo'
export PATH="/d/tools/cargo/bin:$PATH"
```

## 构建与测试

```bash
cd backend/rust/media-worker
cargo test
cargo build --release
```

## 命令

```bash
media-worker hash <input>
media-worker thumbnail <input> <output> --max-width 400 --max-height 600 --quality 85
```

流式 ZIP（从 stdin 读 manifest，不把整个 ZIP 放内存）：

```bash
echo '{"entries":[{"path":"G:/.../a.webp","archive":"0001_a.webp"}]}' | media-worker archive D:/exports/out.zip
```

常驻批量模式（Python 调用方保持进程存活，按行 stdin/stdout 收发 JSON）：

```text
{"id":"1","op":"hash","input":"G:/.../a.webp"}
{"id":"2","op":"thumbnail","input":"G:/.../b.webp","output":"D:/.../b.jpg","max_width":400,"max_height":600,"quality":85}
```

输出：

```json
{"id":"1","ok":true,"sha256":"...","bytes":123}
{"id":"2","ok":true,"output":"D:/.../b.jpg","width":337,"height":600,"source_width":2160,"source_height":3840}
```

## 边界

- 不连数据库、不读业务配置
- 不处理视频、不计算 pHash
- 不接入生产 API；先做双跑一致性和性能对比
