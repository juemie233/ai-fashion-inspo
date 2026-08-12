# 待完成功能清单

## 视频分析功能

**状态：** 待开发

**描述：** 支持上传穿搭视频，通过 AI 分析视频中的穿搭造型，提取关键帧并生成标签。

**涉及模块：**

| 层级 | 说明 |
| ------ | ------ |
| 后端 | ffmpeg 关键帧提取（已安装依赖，需编写服务） |
| 后端 | 视频文件上传 / 存储 |
| 后端 | AI 图片分析复用（对提取的关键帧调用现有分析接口） |
| 前端 | 上传页支持视频文件 |
| 前端 | 视频播放器 + 关键帧展示 |
| 前端 | 分析结果展示 |

**关键帧提取策略：**

- 按固定间隔（如每 3 秒）抽取一帧
- 或使用 ffmpeg 场景检测（`select=gt(scene,0.3)`）自动识别画面变化点

**依赖：**

- ffmpeg（系统已安装）
- 后端 `file_service.py` 需新增 `extract_keyframes()` 函数

**关联模块：**

- `backend/storage/videos/`（视频文件目录，已存在但未使用）
- `backend/app/services/ai_service.py`（图片分析，可复用）

---

## 采集引擎架构改进

**状态：** 待开发

### 1. 采集后 AI 质量审核

**描述：** 下载完成后，利用已有 Ollama + MiniCPM-V 对图片做"是否为穿搭照片"二分类，自动过滤广告、商品图、非模特照片。

**方案：**

- 复用 `ai_service.py` 的分析管道
- 增加一个轻量级 prompt：`"这张图片是否展示真人穿着的服装搭配？仅回答 '是' 或 '否'。"`
- 被判定为"否"的图片自动标记或移至回收区
- 可选：设置置信度阈值，低于阈值的人工复核

**涉及模块：**

- `backend/scripts/run_scraper.py`（采集脚本）
- `backend/app/services/ai_service.py`（AI 分析）
- `backend/app/models/inspiration.py`（可能需要新增 `quality_score` 字段）

### 2. 每步漏斗日志持久化

**描述：** 当前采集漏斗日志仅输出到控制台。应持久化到数据库或日志文件，方便事后排查采集效果不佳的原因。

**方案：**

- 在 `scraper_tasks` 表增加 `diagnostics` JSON 字段，记录每步计数
- 或在采集完成后的日志文件中包含完整漏斗数据
- Web 采集管理页展示"漏斗视图"

**涉及模块：**

- `backend/app/models/scraper.py`
- `backend/scripts/run_scraper.py`
- `web/src/views/ScraperView.vue`