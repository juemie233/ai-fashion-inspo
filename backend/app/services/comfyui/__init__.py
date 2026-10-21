"""ComfyUI 服务包：HTTP 客户端、工作流模板、就绪探测、显存守门。

为什么要包一层而不是让前端直连 8188：

1. **安全**：ComfyUI 没有鉴权，且 `/view` 之类接口能读它自己的输出目录；只把
   127.0.0.1:8188 暴露给本机后端，前端永远不碰它。
2. **可观测**：出图过程要写进任务队列（进度/暂停/取消/失败原因），这些只有后端能做。
3. **可测试**：把 HTTP 调用收敛到一个 client，测试用 `httpx.MockTransport` 就能覆盖
   正常/超时/OOM/节点缺失全部分支，不必真跑模型。
"""

from app.services.comfyui.client import (
    ComfyUIClient,
    ComfyUIError,
    ComfyUINotRunningError,
    ComfyUIWorkflowError,
)

__all__ = [
    "ComfyUIClient",
    "ComfyUIError",
    "ComfyUINotRunningError",
    "ComfyUIWorkflowError",
]
