# media-worker

Python 后端的媒体处理 sidecar 试点（Rust）。

当前提供两个二进制：

- `media-worker`：`hash`、`thumbnail`、`archive`、`batch`
- `storage-audit`：只读扫描 images/thumbnails/videos，报告缺失、孤立、大小/哈希不一致

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

## storage-audit

从数据库导出 expected JSON：

```json
{"expected":[{"path":"images/2026-09/a.webp","size":123,"sha256":"..."}]}
```

```bash
storage-audit G:/fashion-inspo-storage manifest.json \
  --dirs images,thumbnails,videos --output report.json --hash
```

边界：不连数据库、不删除/移动文件；只读扫描。

## 边界

- 不连数据库、不读业务配置
- 不处理视频、不计算 pHash
- 生产 API 只做**可选**接入：archive（合集导出 ZIP，按素材数阈值启用）与
  storage-audit（完整性巡检 / 备份前预检）；均默认关闭，失败/超时/二进制缺失
  自动回退 Python，详见 `docs/features/后端Rust重构评估与媒体处理试点.md`
