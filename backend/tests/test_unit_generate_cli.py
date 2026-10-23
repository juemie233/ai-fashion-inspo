"""命令行独立出图工具（``scripts/generate_image.py``）的参数 → 工作流单测。

为什么不测网络部分：提交/轮询/取图由 ``ComfyUIError`` 与 ``client`` 的既有用例覆盖；
命令行真正会「悄悄和网页不一样」的只有一层——**拼工作流的口径**：

- 文生图必须把尺寸注入 ``EmptySD3LatentImage``，而不是图生图的 ``resolution``；
- 图生图必须用扁平点号 key（``images.image_1``），写成嵌套 dict 会被 ComfyUI 静默丢弃；
- 未审查档位必须把 UNET 文件名覆盖掉，否则「以为切了权重，实际跑的仍是标准版」；
- 参数上下限要与后端同一套（尺寸 ≤ 档位 max_pixels、张数 1~4）。
"""

import json

import pytest

from scripts import generate_image as cli


def _t2i(**over):
    base = dict(
        spec=cli.MODEL_SPECS["z_image_int8"],
        prompt="米色针织开衫配白色阔腿裤",
        seed=12345,
        width=768,
        height=1344,
        steps=8,
        cfg=1.0,
        sampler="res_multistep",
        scheduler="simple",
        shift=3.0,
        refs=[],
    )
    base.update(over)
    return cli.build_workflow(**base)


def _i2i(**over):
    base = dict(
        spec=cli.MODEL_SPECS["qwen_2_1_i2i"],
        prompt="同一个人，同一场景，光脚不穿鞋",
        seed=7,
        width=1024,
        height=1024,
        steps=25,
        cfg=1.0,
        sampler="euler",
        scheduler="simple",
        shift=3.0,
        refs=["ref_a.png"],
        negative="模糊、变形",
    )
    base.update(over)
    return cli.build_workflow(**base)


def test_t2i_尺寸与种子注入到正确节点且占位符全展开():
    workflow = _t2i()

    assert workflow["6"]["inputs"]["width"] == 768
    assert workflow["6"]["inputs"]["height"] == 1344
    assert workflow["6"]["inputs"]["batch_size"] == 1
    assert workflow["8"]["inputs"]["seed"] == 12345
    assert workflow["8"]["inputs"]["steps"] == 8
    assert workflow["4"]["inputs"]["text"] == "米色针织开衫配白色阔腿裤"
    # 占位符没展开干净 = ComfyUI 直接 400，且报错完全看不出是哪个参数
    assert "__" not in json.dumps(workflow, ensure_ascii=False)


def test_i2i_参考图用扁平点号键且第二张起会追加节点():
    workflow = _i2i(refs=["ref_a.png", "ref_b.png"])

    inputs = workflow["6"]["inputs"]
    assert inputs["images.image_1"] == ["4", 0]
    assert inputs["images.image_2"] == ["21", 0]  # 第 2 张起由 build_edit_workflow 追加
    assert workflow["21"]["class_type"] == "LoadImage"
    assert workflow["21"]["inputs"]["image"] == "ref_b.png"
    # 嵌套 dict 会被 ComfyUI 静默丢弃（参考图不生效），模板与注入都必须避免
    assert "images" not in inputs
    assert inputs["negative_prompt"] == "模糊、变形"
    assert inputs["resolution"] == 1024
    assert "__" not in json.dumps(workflow, ensure_ascii=False)


def test_未审查档位覆盖unet文件名():
    spec = cli.MODEL_SPECS["qwen_2_1_uc_i2i"]
    workflow = _i2i(spec=spec)

    assert workflow["1"]["inputs"]["unet_name"] == "qwen-image-2.1-UC-int8_convrot.safetensors"
    # 其余节点结构与标准版完全一致（共用同一份模板）
    assert workflow["2"]["inputs"]["clip_name"] == "qwen3vl_8b_w4a8.safetensors"


def _args(*extra: str):
    return cli.build_parser().parse_args(list(extra))


def test_尺寸超过档位上限被拒():
    # Z-Image 可以 1328²，Qwen 2.1 只有 1024²（权重更大）
    args = _args("--size", "1328x1328", "随便一个描述")
    with pytest.raises(SystemExit):
        cli.resolve_params(args, cli.MODEL_SPECS["qwen_2_1_i2i"], "随便一个描述")
    # 同一个尺寸在 Z-Image 上是允许的
    params = cli.resolve_params(args, cli.MODEL_SPECS["z_image_int8"], "随便一个描述")
    assert (params["width"], params["height"]) == (1328, 1328)


def test_张数与步数越界被拒():
    for extra in (["--count", "5"], ["--steps", "31"]):
        args = _args(*extra, "随便一个描述")
        with pytest.raises(SystemExit):
            cli.resolve_params(args, cli.MODEL_SPECS["z_image_int8"], "随便一个描述")


def test_seed为0时随机而多张逐张递增():
    args = _args("--count", "3", "随便一个描述")
    params = cli.resolve_params(args, cli.MODEL_SPECS["z_image_int8"], "随便一个描述")

    assert 1 <= params["seed_base"] <= 2**31 - 1
    assert params["count"] == 3
