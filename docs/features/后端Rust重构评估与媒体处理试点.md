# 后端 Rust 重构评估与媒体处理试点

> 日期：2026-11-21
> 状态：v0 已完成并跑基准；结论为**暂不接入生产**（见 §9）
> 范围：只评估“是否/在哪里引入 Rust”，不承诺全量重写后端

## 1. 结论

当前项目 **不建议全量 Rust 重写**。

原因不是 Rust 性能不好，而是本项目的主要复杂度不在 API 层：

- 233 个 Python 文件、约 6.5 万行
- 29 个 router、约 344 个 API 端点
- 151 个 service 文件
- 264 个测试文件、约 3,800 条后端测试
- SQLite + SQLAlchemy async + Alembic
- 独立 worker：暂停/恢复/取消/重试/进度/WebSocket/GPU 互斥
- Python 专属能力密集：Playwright、`xhshow`、Pillow、ReportLab、
  LanceDB、hnswlib、scikit-learn、CLIP、ONNX、ffmpeg

全量重写的风险主要落在爬虫、AI、图像、任务状态机和历史边界行为上，
而 Rust 的收益主要落在 API 吞吐/内存/部署；当前本地单人场景下 API 不是瓶颈。

## 2. 各模块建议

| 模块 | 建议 | 原因 |
| --- | --- | --- |
| 图片处理：缩略图、SHA-256、媒体探测 | **优先试点** | 独立、纯计算、无业务状态、易回退 |
| 存储完整性/备份校验扫描 | 可做 sidecar | 大量文件遍历/哈希，Rust 更快更省内存 |
| ZIP/归档打包 | 可选 | 适合 Rust，但当前不是瓶颈 |
| pHash 感知哈希 | 暂缓 | 必须与现有多通道 dHash + Pillow 缩放逐位兼容，风险高 |
| PDF Lookbook | 暂缓 | ReportLab 中文排版已可用，重写成本高于收益 |
| API / Router | 不建议 | 契约、校验、序列化、错误文案回归成本极高 |
| 任务队列 / worker | 不建议 | 状态机和边界条件复杂，重写风险最高 |
| Playwright / 采集 | 不建议 | Rust 浏览器自动化生态与反检测能力弱 |
| AI / 向量 / 人脸 | 不建议 | LanceDB 已是 Rust 内核；CLIP/sklearn/人脸仍依赖 Python |
| ffmpeg 抽帧 | 不建议 | 真正耗时在外部 ffmpeg 进程，换壳无收益 |

## 3. 试点功能：media-worker

位置：

```text
backend/rust/media-worker/
```

定位：

- 独立 CLI，不直接连数据库
- 由 Python 调用（后续接入），Rust 只负责媒体计算
- 失败可回退 Python，不影响 API/worker 主链路
- **本阶段不接入生产调用**，先完成实现、测试和基准对比

### v0 命令

```bash
media-worker hash <input>
media-worker thumbnail <input> <output> --max-width 400 --max-height 600 --quality 85
```

输出 JSON，例如：

```json
{"sha256":"..."}
{"output":"...","width":400,"height":600}
```

### v0 范围

- 流式 SHA-256
- 图片缩略图：等比缩放、不放大、JPEG 输出
- 支持 jpg/jpeg/png/webp/gif 等常见格式
- 错误返回非零退出码，stderr 给中文说明

### 明确不做（本阶段）

- pHash：现有 Python 是“R/G/B 三通道 dHash + Pillow LANCZOS”，
  逐位对齐需要精确复刻 Pillow 缩放，先不纳入 v0
- 视频缩略图：仍走 Python/ffmpeg
- 数据库/路径解析/业务去重：仍由 Python 负责
- 生产接入：先双跑对比，达标后再灰度

## 4. 非 C 盘开发环境

Rust 工具链、cargo 缓存和构建产物全部放 D 盘：

```text
D:\tools\rustup\          RUSTUP_HOME
D:\tools\cargo\           CARGO_HOME（crates 缓存、cargo bin）
D:\tools\cargo-target\     CARGO_TARGET_DIR（编译产物）
D:\tools\downloads\       安装包
```

MSVC 链接器使用本机已有：

```text
D:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools
```

安装/使用命令必须显式设置环境变量，禁止落回 `C:\Users\...\.cargo` / `.rustup`：

```bash
export RUSTUP_HOME='D:\tools\rustup'
export CARGO_HOME='D:\tools\cargo'
export CARGO_TARGET_DIR='D:\tools\cargo-target\fashion-inspo'
export PATH="/d/tools/cargo/bin:$PATH"
```

验收时检查：

- `rustc --version` / `cargo --version` 可用
- 构建后 `C:\Users\...\.cargo`、`.rustup` 没有新增缓存
- 目标目录位于 `D:\tools\cargo-target`
- 仓库内只保留源码和 `Cargo.lock`，不提交 `target/`

## 5. 验收标准

### 功能

- 同一文件 Rust SHA-256 与 Python `file_sha256` / `sha256sum` 完全一致
- 缩略图：
  - 不超过 400×600
  - 等比缩放，不拉伸
  - 不放大原图
  - 输出可被 Pillow 正常读取
  - 视觉质量可接受
- 错误文件/不存在路径/不支持格式返回非零退出码，不 panic

### 工程

- `cargo test` 通过
- 构建产物不写入 C 盘
- 文档记录命令、依赖、已知限制
- 不修改现有 Python 行为，不接入生产 API

### 性能（达到才继续）

用 1,000 张真实图片对比 Python/Pillow 与 Rust：

- 总耗时
- CPU 使用
- 峰值内存
- 单张异常率

没有明显收益就停止，不继续扩大 Rust 范围。

## 6. 后续路线

1. v0：hash + thumbnail，独立测试/基准
2. 双跑对比：输出一致性、性能
3. Python 侧增加可选调用与回退
4. 新素材灰度；失败自动回退
5. 再评估 pHash / ZIP / 存储扫描
6. **API、爬虫、AI、人脸、任务队列仍保持 Python**

## 7. 风险

- 缩略图视觉/编码与 Pillow 不完全一致 → 必须双跑对比
- Windows 路径/中文路径/EXIF 方向 → 单独测试
- sidecar 进程生命周期、超时、僵尸进程 → Python 调用方负责超时和清理
- Rust 编译时间长、工具链体积大 → 只用于独立工具，不影响主后端
- 不要形成“Rust 负责一半、Python 负责一半且各自写数据库”的双写结构

## 8. 当前分支

```text
feat/rust-media-worker
```

该分支只包含评估文档与 media-worker 试点代码；不合并回 `master`。

## 9. v0 实现与基准结果（2026-11-21）

### 已实现

- Rust 1.100.0，工具链位于 `D:\tools\rustup` / `D:\tools\cargo`
- 源码：`backend/rust/media-worker/`
- 构建产物：`D:\tools\cargo-target\fashion-inspo`
- 命令：`hash`、`thumbnail`、`batch`（stdin/stdout NDJSON）
- `cargo test`：4 passed
- SHA-256 与 Python `hashlib.sha256` 逐字节一致
- 缩略图尺寸已复刻 Pillow 的 `round_aspect` 规则，抽测 20 张尺寸全部一致

### 基准（20 张真实素材，已缓存）

| 项目 | Python/Pillow | Rust batch | 结论 |
| --- | ---: | ---: | --- |
| SHA-256 | 0.009s | 0.010s | 基本持平 |
| 缩略图 400×600 | 2.188s | 4.695s | **Rust 慢约 2.1×** |
| JPEG 输出体积 | 1.03MB | 1.20MB | Rust 编码器更大 |

Rust 慢的原因不是语言本身，而是 `image` crate 的解码/缩放路径与
Pillow 的 libjpeg-turbo / 优化 C 实现相比没有优势；进程常驻已经排除了
启动开销，结论仍然成立。

### 结论

**停止在缩略图这条路径上扩大 Rust 投入，不接入生产。**  
保留 `feat/rust-media-worker` 分支作为可复现实验，不合并。  
如果未来仍要尝试 Rust 媒体处理，需要先换成更快的解码/缩放栈
（如 `zune-image` / `fast_image_resize`）并重新基准，不能直接复用本版结论。
