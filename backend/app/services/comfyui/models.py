"""出图档位定义（纯数据：不依赖 httpx / pydantic，命令行工具也能直接用）。

单独拆出来而不是留在 ``probe.py``：probe 要连 ComfyUI、要读 ``Settings``，而
「启动 ComfyUI 后独立出图」的命令行工具（``backend/scripts/generate_image.py``）
只需要这张表。放这里两边共用一份，避免档位参数（steps/cfg/尺寸上限/权重覆盖）
出现「网页是一套、命令行是另一套」的漂移。

字段含义：

- ``workflow``：模板名（对应 ``workflows/<name>.json``）
- ``vram_gib``：出图所需显存（**取实测/保守峰值**，不动态算 —— 算不准还不如早判）
- ``defaults``：前端/命令行填默认值用
- ``files``：覆盖模板里的模型文件名（同一套图结构换权重时用，见
  :func:`app.services.comfyui.workflow.apply_file_overrides`）
- ``uncensored``：无内容安全过滤的权重。前端据此打「未审查」标记并给出警示，
  **不是**装饰字段 —— 换掉它等于换掉一道风险边界，必须能一眼看出来
- ``notice``：交给前端展示的风险说明（文案随模型走，不写死在前端）
"""

MODEL_SPECS: dict[str, dict] = {
    "z_image_int8": {
        "label": "Z-Image-Turbo int8（快，1024² 约 6 秒）",
        "workflow": "z_image_t2i",
        "kind": "t2i",
        "vram_gib": 15.0,
        "defaults": {
            "width": 1024,
            "height": 1024,
            "steps": 8,
            "cfg": 1.0,
            "sampler": "res_multistep",
            "scheduler": "simple",
            "shift": 3.0,
        },
        "max_pixels": 1328 * 1328,
    },
    "qwen_2_1_i2i": {
        "label": "Qwen-Image 2.1（参考图改款，1024² 约 20~45 秒）",
        "workflow": "qwen_edit",
        "kind": "i2i",
        "vram_gib": 14.5,
        "max_refs": 10,
        "defaults": {
            "width": 1024,
            "height": 1024,
            "steps": 25,
            "cfg": 1.0,
            "sampler": "euler",
            "scheduler": "simple",
            "shift": 3.0,
        },
        # Qwen-Image 2.1 在 16 GB 上 1024² 已是上限（权重 13.6 GiB + 激活）
        "max_pixels": 1024 * 1024,
    },
    "qwen_2_1_uc_i2i": {
        "label": "Qwen-Image 2.1 未审查版（参考图改款，1024² 约 20~45 秒）",
        # 与标准版共用同一份模板：只有 UNET 权重不同，其余节点结构完全一致
        "workflow": "qwen_edit",
        "kind": "i2i",
        "vram_gib": 14.5,
        "max_refs": 10,
        "uncensored": True,
        "notice": (
            "该档位使用无内容审查的权重：没有安全过滤，提示词不会被拦截、出图不会被"
            "打码或模糊。生成结果只取决于你输入的描述，请自行把握尺度。"
        ),
        "files": {"unet_name": "qwen-image-2.1-UC-int8_convrot.safetensors"},
        "defaults": {
            "width": 1024,
            "height": 1024,
            "steps": 25,
            "cfg": 1.0,
            "sampler": "euler",
            "scheduler": "simple",
            "shift": 3.0,
        },
        "max_pixels": 1024 * 1024,
    },
}

"""参数硬上限（服务端校验，不信任前端；命令行工具同样受它约束）。

依据是 16 GB 显存的实测：Z-Image int8 在 2560×1440 峰值已达 15.2 GiB，
再往上必然 OOM；张数限制 4 是为了别让一个任务长时间占着 GPU 互斥组。
"""

MAX_PIXELS = 1328 * 1328
MAX_COUNT = 4
MAX_STEPS = 30
MIN_STEPS = 1
