# 待完成功能清单

## API 版本握手

**状态：** 待开发

**描述：** 后端 schema 变更后若未重启，前端会静默降级（如漏斗按钮消失），用户无从察觉。通过版本握手让前后端不一致时明确提示。

**方案：**

- `/api/health` 返回 `schema_version` 字段，每次修改数据库 schema 时递增
- 前端本地维护一个"期望版本号"常量（或从构建配置注入）
- 前端启动时请求 health，版本不匹配则明确提示"后端 API 版本不匹配，请重启后端"
- 可选：后端启动时从 `db_migrations.py` 的字段清单计算版本号，避免手动维护

**涉及模块：**

- `backend/app/main.py`（health 端点）
- `backend/app/db_migrations.py`（schema_version 常量）
- `web/src/api/client.ts`（启动时版本检查）
- `web/src/App.vue` 或全局布局（不匹配提示 UI）

---

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
