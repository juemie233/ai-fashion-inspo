# 后端 Rust 重构评估与媒体处理试点

> 日期：2026-11-21
> 状态：缩略图否决；流式 ZIP（archive）与存储审计（storage-audit）均已**可选接入**（默认关闭/按阈值启用，失败自动回退 Python）；已合并回 master（2026-11-24）
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

## 8. 分支与合并状态

```text
feat/rust-media-worker -> master（已合并，2026-11-24）
```

该分支包含评估文档、media-worker 试点代码，以及 archive（合集导出）与
storage-audit（完整性巡检 / 备份前预检）的**可选接入**——两者均默认关闭，
失败/超时/二进制缺失时自动回退 Python，不影响主链路。

合并后开发统一在 `master` 进行，不再向旧分支提交。

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
实验代码已随 `feat/rust-media-worker` 合并到 `master`，保留作可复现实验。
如果未来仍要尝试 Rust 媒体处理，需要先换成更快的解码/缩放栈
（如 `zune-image` / `fast_image_resize`）并重新基准，不能直接复用本版结论。

## 10. 第二轮尝试：流式 ZIP 归档

### 假设

合集 ZIP 导出当前在 Python 中先把整个 ZIP 写进 `BytesIO`，再返回字节：

- 500 张素材时会同时占用约 100~200MB 内存；
- ZIP 压缩在单线程完成；
- 素材越多，内存峰值越接近“文件总大小”。

Rust 的 `zip` crate 可以边读文件边写输出，理论上同时改善**内存峰值**和吞吐。
这比缩略图更符合 Rust 的优势：无解码/像素算法，只有文件流和压缩。

### 已实现

`media-worker archive <output.zip>`：

- stdin 读取 `{entries:[{path, archive}]}`；
- 逐条打开文件、写 ZIP，**不把整个 ZIP 放内存**；
- 缺失文件跳过并计入 `skipped`；
- 输出 `{output, entries, skipped, bytes}`；
- 新增 5 项 cargo 测试（其中 1 项验证流式 ZIP / 缺失文件 / 可读性）。

### 基准（真实图片，`ZIP_DEFLATED`，已缓存）

> 52 张为真实素材；200/500 行复用同一批文件扩展到导出口径上限，用于压测内存趋势，
> 实际素材尺寸分布不同，时间会略有差异。

| 素材数 | Python 内存 ZIP | Rust 流式 ZIP | 速度 | Python 内存增量 |
| --- | ---: | ---: | ---: | ---: |
| 52 | 0.585s | 0.556s | **1.05×** | 18.6MB |
| 200 | 2.158s | 1.760s | **1.23×** | 70.6MB |
| 500 | 5.375s | 4.277s | **1.26×** | 176.4MB |

ZIP 输出大小与条目名完全一致。

### 判断与接入

- 速度提升不大（约 1.05~1.26×），**不是压倒性优势**；
- 真正的收益是 **Rust 流式写入，内存不随 ZIP 文件大小线性增长**；
- 因此把 `archive` 接入导出链路，但**默认关闭、按阈值启用、失败自动回退**：
  - 新增 `MEDIA_WORKER_BIN`：不配置则完全走 Python；
  - `MEDIA_WORKER_ARCHIVE_THRESHOLD`：素材数达到阈值才尝试 Rust（默认 100）；
  - `MEDIA_WORKER_ARCHIVE_TIMEOUT_SECONDS`：sidecar 超时（默认 300s）；
  - Rust 输出 `.zip` 到既有导出目录，Python 继续写 `meta.json`、返回下载 token；
  - Rust 失败/超时/二进制缺失：删除半成品，自动回退 Python `_zip_bytes`；
  - Python 仍是唯一数据库/业务状态写入方，Rust 不连数据库。
- 已用真实合集 4（52 张）手动验证：Rust 路径成功，`manifest.csv`/`README.md`/52 个媒体条目均完整。
- 已合并到 `master`；生产启用只需在 `.env` 配置 `MEDIA_WORKER_BIN`。

## 11. 第三轮尝试：存储完整性扫描

### 假设

现有 Python 管理端扫描 storage 的 `images/thumbnails/videos`，用 `rglob` 收集
文件集合，再与数据库路径对比。这个任务没有业务状态、没有图片解码，适合 Rust
并行遍历；如果 6 万+ 文件能显著提速，就值得后续接入管理页/备份流程。

### 已实现

`storage-audit <root> <manifest.json>`：

- 输入数据库导出的 expected 清单（路径 + 可选大小/SHA-256）；
- 可选 `--hash` 并行校验内容；
- 输出缺失文件、孤立文件、大小不一致、哈希不一致；
- 只读，不删除/移动文件；不连数据库；
- 拒绝绝对路径和 `..` 越界清单；
- 9 项 cargo 测试（含 CLI 黑盒测试）。

### 基准（真实 storage，只读，已缓存）

| 项目 | Python `rglob` | Rust `storage-audit` |
| --- | ---: | ---: |
| 66,290 个文件 | 4.759s | **0.338s** |
| 缺失/孤立结果 | 0 / 0 | 0 / 0 |
| 报告耗时 | — | 186ms |

约 **14×** 提升。这个任务没有 AI/解码/网络干扰，是当前最干净的 Rust 增益。

### 判断

- 存储扫描是当前收益最明确的功能，适合接入管理端「存储完整性检查」或备份前校验；
- 但接入生产仍需补：Python manifest 生成、API/任务入口、管理员权限、报告展示；
- 已接入现有 `GET /api/admin/integrity-check`：
  - 从 `MEDIA_WORKER_BIN` 同目录自动查找 `storage_audit` 二进制；
  - Rust 返回缺失/孤立文件后，Python 负责映射素材 ID、垃圾桶不变量和响应结构；
  - 任何启动/超时/解析异常都自动回退 Python；
  - 响应新增 `scanner: "rust" | "python"`，前端旧版本可忽略；
  - 真实库实测 `scanner=rust` 时巡检约 **0.6~0.7s**（原 Python 扫描约 4.8s）；
- 当前只读展示，不改变孤立文件清理逻辑；
- 已接入备份前预检：`scripts/ops/backup_data.sh` 在复制前调用
  `backend/scripts/storage_audit_preflight.py`，报告写入
  `.integrity_preflight.json` 与 `manifest.json.integrity_preflight`；
  预检只告警、不阻断备份、不删除文件；
- 管理页完整性卡片显示「Rust 加速 / Python 扫描」来源；
- 下一步不再扩 pHash/PDF；如需继续 Rust，优先把 ZIP 的 100 张阈值按真实规模调优。

已合并到 `master`，后续在 `master` 维护。

## 12. 代码统计中的 Rust

统计脚本已同步支持 Rust：

- `scripts/ci/count_lines.sh`：新增「Rust sidecar」分节（`backend/rust/**/*.rs`，排除 target）
- `scripts/ci/count_lines.ps1`：同一口径；文件保持 UTF-8 BOM
- `scripts/ci/project_stats.py`：`SOURCE_EXTS` 加 `.rs`，新增 `backend/rust` 分块
- `scripts/ci/code_report.py`：新增 `rust_sidecar` 桶与 Rust 语言组，按语言输出不再只取 Top 6

当前统计（2026-11-21）：

| 口径 | 文件 | 总行 | 代码 |
| --- | ---: | ---: | ---: |
| count_lines / project_stats（仅 `.rs`） | 3 | 902 | 790 |
| code_report Rust 语言（仅 `.rs`） | 3 | 902 | — |
| code_report `Rust sidecar` 桶（含 Cargo.toml/README 等） | 5 | 1,007 | — |

全仓代码总量约 **34 万行**；Rust 目前约 **0.3%**。

## 13. 近重复分组的再评估（本轮不开发）

- 现有 Python 分带倒排索引在 22,347 张已缓存素材上约 **1.7s**（历史实测）；
- 当前库规模约 2.6 万素材，管理页近重复扫描不是用户可感知瓶颈；
- 纯算法移植到 Rust 可以更快，但收益要等到 **10 万级素材**或高频全库扫描才明显；
- 因此本轮不实现 Rust near-duplicate，保留 Python 版本；
- 触发条件：素材量到 10 万、或近重复扫描进入日常高频操作，再按同一套“独立 CLI + 基准 + 不接生产”的流程评估。
