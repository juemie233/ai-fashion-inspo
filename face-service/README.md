# 人脸识别微服务（face-service）

独立 Python 3.10 环境运行的人脸识别子服务，供主后端（Python 3.12）通过 HTTP 调用。
解决 insightface 不支持 Python 3.12 的问题：主后端环境保持不变，人脸能力独立部署。

## 架构

```
主后端 (Python 3.12, FastAPI)  --HTTP-->  人脸识别子服务 (Python 3.10 + InsightFace, 端口 18889)
```

主后端通过 `FaceRecognitionClient`（`FACE_SERVICE_URL` 环境变量，默认 `http://127.0.0.1:18889`）调用。

## 环境要求

- Python 3.10（insightface 官方支持 3.6~3.10；已安装于 `%LOCALAPPDATA%\Programs\Python\Python310`）
- NVIDIA GPU + CUDA 12.8 + cuDNN 9.x（已装于 `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8`；
  无 GPU 时自动回退 CPU）

## 快速开始

```bat
:: 1. 创建虚拟环境（仅首次）
"%LOCALAPPDATA%\Programs\Python\Python310\python.exe" -m venv .venv

:: 2. 安装依赖（仅首次，使用阿里云镜像；无需编译，全部有 wheel）
.venv\Scripts\pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

:: 3. 放置模型（仅首次）
::    下载 buffalo_l 的 det_10g.onnx 与 w600k_r50.onnx 到 models/buffalo_l/
::    国内镜像（immich 重构版，动态输入）：
::      https://hf-mirror.com/immich-app/buffalo_l/resolve/main/detection/model.onnx
::      https://hf-mirror.com/immich-app/buffalo_l/resolve/main/recognition/model.onnx

:: 4. 启动服务（端口 18889）
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 18889
```

> 说明：本服务不依赖 insightface 包（其 Python 3.12 不兼容且源码编译需要
> MSVC Build Tools），而是用 onnxruntime 直接加载 buffalo_l 的 onnx 模型
> （RetinaFace 检测 + ArcFace 识别），推理后端与 insightface 原生一致。

## API

| 方法 | 路径 | 说明 |
| ---- | ---- | ---- |
| GET | /health | 健康检查 + 模型加载状态 |
| POST | /api/face/embed | 上传图片，返回人脸检测框与 512 维特征 |
| POST | /api/face/register | 注册人脸（form: person_id, person_name, file） |
| POST | /api/face/match | 人脸匹配（form: file, top_k=5），返回余弦相似度 top-k |
| GET | /api/face/persons | 已注册人脸列表 |
| DELETE | /api/face/persons/{id} | 删除注册 |

## 数据

- 注册数据存于本地 `face_service.db`（SQLite，特征向量 float32 BLOB），与主后端数据库隔离。
- 模型目录：`models/`（buffalo_l 检测 + 识别）。
