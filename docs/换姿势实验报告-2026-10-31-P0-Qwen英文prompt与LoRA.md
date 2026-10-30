# P0 实验报告：Qwen-Image 2.1 英文约束 prompt + Pose Studio LoRA（6 组对比）

> 目的：验证社区（wangweihong/comfyui-pose-gallery）那套「Qwen2.1 + 英文约束 prompt + Pose Studio QI2.1 v2 LoRA」
> 能否在我们的本机栈复现——目标是一次出图同时做到「姿势跟随 + 保留身份/服装/发型」。
> 实验时间：2026-10-31 凌晨；样本：2 个（s01/s02，先 pilot 再决定是否扩 10）。
> 结论先行：**英文 prompt 解决了身份/服装/发型（5/5），但姿势不会跟随；加 LoRA 后姿势开始动，
> 可是 int8 量化模型下画质/人体结构崩坏（鬼图），社区那两张干净结果在我们 int8 栈上复现不了。**
> **需要 BF16 权重（Qwen-Image 2.1 是 20B 级，约 40 GB）才可能复现，16 GB 显存不现实。**

---

## 1. 实验设置

| 项 | 配置 |
| --- | --- |
| 模型 | `qwen_edit` 模板；UNET = `qwen-image-2.1-UC-int8_convrot`（未审查版）或 `qwen_image_2.1_int8_convrot`（标准版） |
| LoRA | `VNCCS_QI2_PoseStudioV2.safetensors`（152 MB，HF `MIUProject/VNCCS_PoseStudio_QI2.1`） |
| 提示词 | 社区原文英文约束（Replace the pose of the person in image2 with image1's pose… preserve image2 identity/clothing… do not copy line drawing/mannequin appearance） |
| 图序 | 图 1 = 图库姿势线稿（或 R2 阶段1 图）；图 2 = 主体素材 |
| 参数 | 25 steps、CFG 1.0、Euler / simple、resolution 1024；seed = 20261029+i；**参考图保持原始比例**（压成方图会破坏姿势，已修正） |
| 脚本 | `scripts/oneoff/r2_qwen_en_prompt_eval.py --execute …` |

## 2. 六组对比结果

![P0 对比图（主体 / 无 LoRA / LoRA1.0 / LoRA2.0 / std+LoRA / 混合）](P0-Qwen英文prompt与LoRA对比-20261031.jpg)

| # | 组合 | 姿势跟随 | 身份/服装/发型 | 画面质量 | 耗时 |
| --- | --- | :---: | :---: | :---: | ---: |
| T1 | 线稿 + 英文 prompt，**无 LoRA**（UC int8） | **0/5**（保持主体自己的坐姿） | **5/5**（白衬衫、卡其短裤、长发、包都回来了） | 好（接近主体照片） | 89.4 s |
| T2 | 线稿 + LoRA 1.0（UC int8） | 0~1/5（几乎无变化） | 5/5 | 好 | 56.5 s |
| T3 | 线稿 + **LoRA 2.0**（UC int8） | 2/5（出现抬腿/桥式倾向） | 5/5 | **差**：腿部/比例崩坏，人物像被拉长 | ~60 s |
| T4 | 线稿 + LoRA 1.0（**标准** int8） | 2/5（躺椅抬腿，部分接近目标） | 5/5 | **差**：腿部巨大、结构错乱 | 36.6 s |
| T5 | 线稿 + LoRA 1.5（标准 int8） | 2/5 | 5/5 | **差**：同样形体崩坏 | 35.4 s |
| T6 | 图1=R2 阶段1 图 + LoRA 1.2（标准 int8） | 2/5（姿势来自阶段1） | 5/5 | **很差**：把阶段1 的彩色素体纹理直接画到腿上 | 55.7 s |

**汇总**：身份/服装/发型 **5/5（英文 prompt 立竿见影）**；姿势在 int8+LoRA 下只能到 2/5 且伴随形体崩坏；
无 LoRA 时姿势完全不跟随。**没有任何一组同时达到「姿势准 + 画质可接受」。**

## 3. 为什么和社区结果差这么多

社区（wangweihong）用的是 **Qwen Image 2.1 BF16 + Qwen3-VL BF16 编码器 + ComfyUI 0.43.0**，
17 s/张，两例 P002/P031 站→跪，姿势与服装都保住了。

我们的差异：

1. **权重是 int8 convrot 量化版**（未审查 UC / 标准版都是 int8，7.2 GB 级），不是 BF16（20B 级、约 40 GB）；
   LoRA 是给 BF16 训练的，挂在 int8 上**效果被放大/失真**：强度 1.0 几乎无效，2.0 才动起来但结构崩坏；
2. 社区有 **ComfyUI 0.43.0**（我们 0.42.1）与专用节点的工作流配置，我们只是把 LoRA 硬插在 UNET 与缓存节点之间；
3. 他们只测了 2 个单人正面姿势，且明确写「不保证全部姿势」——本身也是小样本成功。

## 4. 判断与下一步

**P0 部分成功**：

- ✅ **身份/服装/发型**：社区英文 prompt 直接解决了我们之前「换黑裙/换人」的问题（5/5）；
- ❌ **姿势**：无 LoRA 不跟随；int8 + LoRA 能推动姿势但画质崩坏，不可用；
- ❌ 想复现社区效果需要 BF16 全量权重（≈40 GB，16 GB 显存需大量 offload，速度会非常慢），**不建议在本机继续这条**。

**下一步建议（按可行性排序）**：

1. **E3：Depth 路线**（已列入调研）——`Depth Anything V2`（小/中模型 100~400 MB 可先试）把图库线稿转深度图，
   接我们已有的 SDXL ControlNet-Union **depth** 模式；社区用同一思路（MOSS/Krea2）并靠「提前结束深度控制」
   减少素体外观。这是不依赖 BF16 大模型、也不依赖 LoRA 的路线。
2. **接受「英文 prompt 保身份/服装」但换姿势来源**：用 R2 阶段1 的**深度图/骨架**而不是原图做条件
   （避免彩色素体纹理被抄进去），再配英文 prompt。
3. 若坚持 Qwen 路线：需要换 24 GB+ 显卡 + BF16 权重，成本远超收益，暂不建议。

## 5. 数据位置

- T1 无 LoRA：`G:/fashion-inspo-storage/poc/p0_nolora/`
- T2 LoRA1.0 UC：`.../poc/p0_lora/`；T3 LoRA2.0 UC：`.../poc/p0_lora_s2/`
- T4/T5 标准 int8：`.../poc/p0_std_lora/`、`.../poc/p0_std_lora15/`
- T6 混合：`.../poc/p0_hybrid/`
- 模型：LoRA 在 `D:/AI/ComfyUI/ComfyUI/models/loras/qwen2.1/VNCCS_QI2_PoseStudioV2.safetensors`
