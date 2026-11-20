# 收藏增强 P2：合集导出（Lookbook）与 AI 合集整理预览

> **状态**：现行（2026-11-20 实施）
> **上游**：[收藏增强 P1：合集整理与智能视图](收藏增强-P1-合集整理与智能视图.md)
> **范围**：后端 P2 两项：①合集导出四格式（PDF/ZIP/CSV/Markdown，500 张/24h）；
> ②合集 AI 整理预览（规则分组 + Ollama 起名，只预览不写库）。前端由并行任务实现。

## 1. 合集导出

### 1.1 生成导出

`POST /api/collections/{collection_id}/exports`

```json
{ "format": "pdf" }
```

- `format`：`pdf` / `zip` / `csv` / `markdown`；
- 手动/智能合集均可；只导出**未删除**素材（垃圾桶排除）；素材顺序沿用合集内容顺序
  （手动 = position，智能 = 上传时间倒序）；
- **空合集 400**；**超过 500 条 400**（提示分批：`合集共 N 条素材，超过单次导出上限 500 条，请分批导出`）；
- 每个导出目录带 `meta.json`（格式/文件名/计数/过期时间）；生成新导出前会**懒清理**超过 24h 的旧导出。

返回：

```json
{
  "token": "随机安全 token",
  "filename": "合集名_20261120.pdf",
  "format": "pdf",
  "item_count": 12,
  "skipped_count": 1,
  "size_bytes": 1048576,
  "download_url": "/api/exports/{token}",
  "expires_at": "2026-11-21T10:00:00Z"
}
```

- `item_count`：实际成功导出的数量（CSV/Markdown = 行数；ZIP = 打包成功的文件数；PDF = 嵌入的图片数）；
- `skipped_count`：文件缺失/无法解码/视频等无法按该格式处理而跳过的数量；
- 响应**只暴露 token 与相对地址**，不暴露服务器绝对路径。

### 1.2 下载

`GET /api/exports/{token}` → `FileResponse`

- token 只允许 `[A-Za-z0-9_-]`、长度 16~64，非法/不存在/过期一律 **404**（防路径穿越）；
- `Content-Disposition` 使用安全英文文件名（如 `collection-3-pdf-20261120.pdf`）；
- 下载时发现已过期会顺手删除该导出目录。

### 1.3 各格式内容

| 格式 | 内容 |
| --- | --- |
| CSV | UTF-8 **BOM**；列：序号 / 素材ID / 文件名 / 媒体类型 / 来源 / 作者 / 收藏时间 / 评分 / 标签 / 原链接 / 相对路径；所有单元格过 `sanitize_csv_cell`（防公式注入） |
| Markdown | `# 合集名` + 合集元信息（类型/导出时间/条目数）+ 条目列表（序号/作者/标签/原链接/本地路径） |
| ZIP | 原图/视频按 `0001_<safe>.<ext>` 打包（缺失文件跳过计 skipped）+ `manifest.csv`（同 CSV 列契约）+ `README.md` |
| PDF | ReportLab 矢量生成：A4 封面 + 每页 2×2 网格；封面含合集描述/封面图/本地导出时间/正确素材数，正文图片**等比完整放入**格子（不再 `ImageOps.fit` 裁剪），标签按实际像素宽度换行/省略，页脚 `第 X / Y 页`，作者旁的「原帖」可点击；文字可选中/搜索（不再整页栅格化）；图片最长边 1600px、JPEG 88，线程池生成不阻塞事件循环 |

安全/健壮性：所有素材路径都做 `storage_root` 内解析（越界即跳过）；导出文件落在
`storage_root/exports/<token>/`，不跟随软链到目录外。

## 2. 合集 AI 整理预览

`POST /api/collections/{collection_id}/ai-summary`（**只预览，不写库**）

- 读取合集最多 **80 条**素材（手动/智能合集均可，排除垃圾桶）：标题（caption/文件名）、
  作者、标签、主色（`dominant_colors` JSON 数组）、评分、来源；
- **规则分组**：按素材标签词频取最多 5 个主题标签；每个 tag 的成员为包含该标签的素材
  （允许重叠），每组最多返回 12 个 `item_ids`；没有任何标签时按主色（每张第一个主色）
  或评分兜底 1 组（tag = 主色 hex 或「高评分/未评分」）；
- 调用本地 Ollama `POST {ollama_base_url}/api/chat`：模型 `ollama_vision_model`、
  `stream=false`、`think=false`、`temperature=0.3`，提示词要求**只输出 JSON**；
- AI 生成：`summary`、3 个 `name_suggestions`、`reason`、5~8 个 `theme_tags`、
  每个规则分组的中文主题名；
- JSON 解析失败不报错：分组名回退规则 tag，其余字段规则兜底；**Ollama 超时/不可用/空响应 → 503**
  （`AI 服务暂不可用，请稍后重试`）；
- **空合集 400**；不新增表/迁移，不持久化 AI 结果。

返回：

```json
{
  "summary": "…",
  "name_suggestions": ["…", "…", "…"],
  "reason": "…",
  "theme_tags": ["…", "…", "…", "…", "…"],
  "groups": [
    {
      "tag": "法式",
      "name": "法式优雅",
      "count": 8,
      "item_ids": ["uuid1", "…"],
      "items": [{"id": "uuid1", "thumbnail_path": "…", "source_author": "…"}]
    }
  ],
  "model": "qwen3-vl:8b-instruct",
  "generated_at": "2026-11-20T10:00:00Z"
}
```

前端用现有 `PATCH /api/collections/{id}` 应用名称/描述；主题标签与理由只供复制，不自动写库。

## 3. 涉及模块

| 模块 | 职责 |
| --- | --- |
| `backend/app/services/collection_export_service.py` | 四格式生成、token/meta/过期清理、安全路径解析、下载校验 |
| `backend/app/routers/collections.py` | `POST /api/collections/{id}/exports`（生成导出，依赖 DB 会话） |
| `backend/app/services/collection_ai_service.py` | 80 条采样、规则分组、提示词、Ollama 调用、JSON 解析与回退 |
| `backend/app/routers/exports.py` | `GET /api/exports/{token}`（token 下载；不直连 DB，避免 router-DB 耦合棘轮） |
| `backend/app/routers/collections.py` | `POST /api/collections/{id}/ai-summary` |
| `backend/app/schemas/collection.py` | `CollectionExportRequest` |
| `backend/tests/test_collection_export.py` | 四格式/上限/过期/路径穿越/缺失/智能合集/垃圾桶/空合集 |
| `backend/tests/test_collection_ai_summary.py` | 正常/markdown 包裹/解析失败/503/空合集/80 采样/规则分组 |

**数据模型**：本 P2 不新增表/迁移，`docs/overview/数据模型.md` 无变化（导出使用 `storage_root/exports/` 临时目录 + meta.json，AI 结果不落库）。

## 4. 验证

```bash
cd backend
pytest tests/test_collection_export.py -q
pytest tests/test_collection_ai_summary.py -q
```

## 5. 已知取舍

- **PDF 只嵌入图片**：视频素材计入 `skipped_count`（PDF 不嵌视频；ZIP 格式保留原视频）。
- **PDF 中文字体**：优先 Windows `simhei`/`msyh`、Linux `wqy`/`Noto CJK`，都找不到时回退
  ReportLab 内置 `STSong-Light` CID 字体；不依赖 Pillow 默认字体。
- **PDF 体积**：为保证清晰度，图片最长边限制 1600px、JPEG 88；52 张约 24MB，ZIP 仍保留原图。
- **图片不裁剪**：竖图在高格子里会左右留白，这是为了保留完整构图；如需满格可后续提供「裁剪/留白」选项。
- **导出文件 24h 懒清理**：只有「生成新导出」时触发清理；长期不导出则旧目录会多留一段时间，
  不影响下载（下载时按 meta 过期判定 404）。
- **上限 500 是单次快照**：超过请分批导出；导出期间合集变化不会反映到已生成文件。
- **AI 预览可能重叠**：分组按标签而非互斥聚类，同一素材可出现在多个组；`count` 为组内成员数
  （采样后口径，最大 80 条素材范围内）。
- **AI 结果不持久化**：刷新页面需重新生成；Ollama 服务未启动时返回 503，前端应提示可重试。
- **提示词输入有截断**：单条素材字段与总提示词都有长度上限，防超长/注入输入拖垮本地模型。

## 6. 明确不做（2026-11-20 决定）

收藏增强评审里剩下的两条待办**已由用户确认不做**，因此从 `TODO.md` 移除（TODO 只保留未完成项）：

- **收藏备注 / 优先级 / 收藏理由**：布尔心形 + 合集分类已覆盖当前使用方式，再加备注字段和
  用途标签的维护成本（迁移、搜索、移动端只读、垃圾桶一致性）大于收益。
- **浏览器插件直接收藏到指定合集**：插件目前是「先入库再整理」，直接选目标合集需要在
  manifest 里维护合集列表、处理离线排队与幂等，收益有限。

若后续使用方式变化（例如需要按用途筛选或从插件批量归入合集），再单独评估并重新登记到 TODO。
