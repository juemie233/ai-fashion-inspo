"""应用配置：通过 Pydantic Settings 管理所有配置项。"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# 数据库文件绝对路径（不依赖进程 CWD，避免从不同目录启动时产生双库）
_DB_PATH = Path(__file__).resolve().parent.parent / "fashion_inspo.db"

# 默认 AI 分析提示词（Settings 与 ConfigConstants 兜底共用同一份文本）
_DEFAULT_AI_ANALYSIS_PROMPT = '你是一位专业的时尚穿搭分析助手。请分析图片中人物的穿搭，按下面结构提取标签。\n【命名原则】\n- 所有单品的 type 都写成「颜色+品类+关键款式」，如：黑色过膝袜、白色百褶短裙、棕色尖头细跟高跟鞋。颜色必须写在开头，不许漏掉。\n- type 控制在 12 字以内；若款式信息过多，把次要信息放到 features。\n- style/fit 等其它列表里的词用简洁常用词，避免生造组合词。\n- 袜子/丝袜/鞋子等配饰的 type 禁止只写裸品类名（禁止输出「丝袜」「袜子」「连裤袜」「高跟鞋」这类无颜色无款式的词），必须是「颜色+长度/款式+类型」的完整名；写不出完整名时宁可不输出该项单品。\n1. style（风格，1~3 个）：\n候选：JK制服/汉服/Lolita/Y2K/CleanFit/法式/日系/韩系/学院风/街头/新中式/复古/极简/美式复古/英伦风/波西米亚/运动风/甜美风/暗黑风/御姐风/辣妹风\n【JK制服判定：强信号组合命中即判】下列任一「成套信号」命中，style 必须含「JK制服」（可再补学院风/甜美风等作补充，但不得用它们替代 JK制服）：\n① 水手服（水手领+襟线/胸垫/领巾）；② 制服衬衫 + 领结/领带 + 短裙（**领结/领带是关键强信号**，短裙可为格纹百褶、纯色西装短裙、暗格短裙等；不一定有格纹——领结+制服衬衫+短裙即典型 JK，不必强求格纹百褶）；③ 单独的格纹百褶短裙并搭配衬衫/开衫/制服西装外套；④ 制服西装外套＋格纹/百褶短裙成套；⑤ 乐福鞋/皮鞋 + 上述任一制服组合（**不穿鞋或看不清鞋不否定 JK**，鞋子只作辅助加分，不作必要条件）。\n【典型组合示例】领结+白衬衫+格纹/纯色短裙+乐福鞋/皮鞋（也可不穿鞋）；水手服+百褶短裙；制服衬衫+领带+西装短裙。\n【禁止单件误判】仅穿白色/条纹衬衫等单件上衣＋普通下装（纯色裙/长裤/包臀裙等）时不得判 JK 制服——这类通常是学院风/秘书风/知性风/通勤风/辣妹风，style 写对应标签即可，不要补 JK制服。判别锚点：**必须有「制服特征」**（领结/领带、水手服、制服西装外套、格纹百褶裙）才判 JK；只有普通衬衫+普通下装、无任何制服配件→不判 JK。（可输出多个风格标签，无明显风格可不输出）\n【御姐风】偏成熟、优雅性感的穿搭（包臀裙/连衣裙+丝袜+高跟鞋）可给「御姐风」作为补充。\n2. items（逐件主要单品）：\n输出 [{type, color, features}]，type 已含颜色，color 再写一次主色（方便检索），features 放 2~4 个补充特征。\n▸ JK 制服裙专项（重点修正颜色与款式）：\n- 裙子 type 必须「颜色开头 + 图案/款式 + 裙型」，颜色要写准（藏青/深蓝/黑/酒红/灰/白等），格纹必须点明是「格纹」并给配色，例如：藏青格纹百褶短裙、黑色百褶短裙、深灰西装短裙、米白纯色百褶裙；\n- 分不清格纹主色时按最大面积色块定色，并把格纹色写进 features（如 features:["红黑格纹","百褶","腰后有调节扣"]）；\n- 裙子长度看下摆与膝盖关系：膝上 15cm 以上为短裙/迷你裙、及膝为中长裙。\n▸ 袜子/丝袜专项（必须颜色开头，写明长度 + 类型 + 花纹）：数量要符合实际，一个人物一个！\n【袜子必须肉眼可见｜高筒靴的靴筒≠袜子｜禁止默认联想】\n- 只要图中没有清晰可见的袜子，就不要输出任何袜类单品。可见判据：能看到独立袜口（罗纹/蕾丝/收口边）、堆叠褶皱，或织物与皮肤有明确分界；三者都看不到 → 不输出袜子。\n- 腿部被黑色高筒靴盖住时那是靴筒不是袜子：过膝靴/及膝靴/骑士靴/袜靴/长筒靴（有鞋底、鞋跟、拉链、硬挺鞋面结构）→ 只输出该靴子，严禁把靴筒当「黑色过膝袜/黑色长筒袜/黑丝」输出。\n- 穿短裙/学院风/JK 制服并不代表一定穿了袜子：不要按「常见搭配」脑补黑色过膝袜；看不到袜子宁可不输出，也不要编造。\n\n【黑色系默认规则（重点修正，禁止无证据细分）】黑色/深色的连腿袜（丝袜/裤袜/筒袜）只要看不到「膝上方独立袜口边 + 袜口之上裸露大腿皮肤」，一律按「黑色连裤袜」命名（透肉写「黑色透肉连裤袜」「黑丝连裤袜」）；**无证据禁止写「黑色过膝袜」「黑色大腿袜」「黑色长筒袜」**。过膝袜/大腿袜的唯一判据：膝上（大腿中上段）有独立袜口（罗纹/蕾丝/提花收口边），且袜口之上可见裸露大腿；图中只到膝下、或裙摆直接接袜子都看不到上述判据 → 不是过膝袜。注意区分：长筒袜袜口在膝下，过膝袜袜口在膝上；拿不准时按连裤袜处理（网图里连体袜/裤袜远多于独立过膝袜）。\n【丝袜长度必填｜禁止无长度泛称】「黑色丝袜」「肉色丝袜」「半透明肤色丝袜」「哑光肤色丝袜」等只写了材质、没写长度的词**不得作为 type**——它们不是完整单品名。凡丝袜/连裤袜类单品，type 必须写明长度类型之一：连裤袜(包裹到腰/裆部一体)/过膝袜(膝上至大腿中段)/大腿袜(近大腿根)/长筒袜(膝下及膝)/中筒袜(小腿肚下)/短袜(及踝)；分不清长度时禁止猜「过膝袜/大腿袜」：黑色系按下条默认规则写「黑色连裤袜」；其它颜色看不清长度时宁可不输出该项。\n「黑丝」「白丝」同样不得单独作 type——它们是材质俗称，必须带长度：黑丝连裤袜 / 白丝过膝袜；禁止单独写「黑丝」「白丝」作单品名。\n【连裤袜 vs 过膝袜 判别锚点（重点修正）】连裤袜 = 从脚一路包到腰，大腿根处两条腿之间有裆部/胯部布料相连成一体、看不到独立袜口边，抬腿或裙摆掀动时腿部丝袜向上延续不断开；过膝袜/大腿袜 = 两条腿各穿各的，大腿中上段各有一条独立袜口(罗纹边/蕾丝边)，两腿分开时袜口之间无布料连接。短裙遮住大腿根看不清裆部时：两腿间若有连续布料连成一体→连裤袜；两腿各自独立、可见袜口分段边→过膝袜。肤色打底(光腿神器)同样按此判定，是连体的写「肉色连裤袜/肤色连裤袜」，**严禁写「肉色过膝袜」**；裸腿则完全不输出袜类单品。\n正例：黑丝连裤袜、黑色连裤袜、黑色透肉连裤袜、肉色连裤袜、白色堆堆袜；副例（仅当膝上袜口可见时）：白色过膝袜、黑色过膝袜、黑色大腿袜。\n- 长度：船袜/隐形袜/短袜/及踝袜/中筒袜(小腿肚下)/长筒袜(膝下)/过膝袜/大腿袜/踩脚袜/连裤袜/堆堆袜\n- 类型与材质：棉袜/丝袜/黑丝/白丝/肉色丝袜/半透明肤色丝袜/光腿神器(加绒肤色)/渔网袜/吊带袜(吊袜带+长筒丝袜)/蕾丝花边袜/提花袜/螺纹袜/罗纹袜/羊毛袜/天鹅绒袜/网纱袜/娃娃袜/泡泡袜\n- 花纹（有就写）：纯色/条纹/横条纹/竖条纹/格纹/波点/字母/卡通图案/蝴蝶结装饰/亮片点缀/压花/菱格\n- 透度/质感（放 features）：薄款透肉(20D 黑丝)/微透/不透/哑光/加绒/罗纹竖条/网眼/绒面\n- 组合示例：黑色连裤袜、黑色透肉连裤袜、黑丝连裤袜、黑色连裤丝袜、肉色连裤袜、肉色光腿神器、酒红天鹅绒连裤袜、白色堆堆袜、白色蕾丝堆堆袜、黑白条纹堆堆袜、白色中筒袜、灰色格纹长筒袜、奶咖色羊毛袜、黑色螺纹中筒袜、粉色卡通短袜、黑色天鹅绒中筒袜、黑色渔网袜(大网)、白色渔网袜(小网)、黑色吊带袜、白色波点过膝袜、黑色天鹅绒中筒袜\n- 只看到裸露皮肤/光腿时绝不输出任何袜类 item（不要凭想象补丝袜）；肤色打底裤（光腿神器）与光腿的区别：有裤缝/裆部轮廓/贴肤反光均匀 → 是裤袜；脚踝处无分界、直接是皮肤色 → 裸腿，不输出。袜子被鞋遮住看不到袜口时不要输出。丝袜与棉袜要区分（丝袜有光泽/透光，棉袜哑光）；没穿袜子不要编造。\n▸ 高跟鞋/鞋靴专项（必须颜色开头，写明鞋款 + 鞋头 + 跟型）：数量要符合实际，一个人物一个！\n- 鞋款：高跟鞋/细高跟鞋/中跟鞋/粗跟鞋/猫跟鞋/坡跟鞋/穆勒鞋/乐福鞋/牛津鞋/德比鞋/玛丽珍鞋/芭蕾平底鞋/方扣单鞋/单鞋/高跟凉鞋/一字带高跟凉鞋/细带凉鞋/罗马凉鞋/水钻凉鞋/鱼嘴鞋/帆布鞋/板鞋/小白鞋/老爹鞋/运动鞋/切尔西靴/马丁靴/骑士靴/袜靴/短靴/中筒靴/长筒靴/过膝靴/及膝靴\n- 鞋头：尖头/圆头/方头/杏仁头/露趾\n- 跟型：细跟/粗跟/猫跟/坡跟/方跟/马蹄跟/锥形跟/厚底/防水台/平底\n- 细节（放 features）：红底/漆皮/亮面/绒面/麂皮/金属装饰/铆钉/水钻/蝴蝶结/一字带/交叉绑带/一字搭扣/侧拉链/袜靴弹性口/松糕底/防滑底\n- 组合示例：黑色尖头细跟红底高跟鞋、黑色尖头粗跟高跟鞋、黑色一字带高跟凉鞋、银色细带凉鞋、红色尖头猫跟鞋、白色玛丽珍粗跟鞋、黑色漆皮乐福鞋、白色方扣单鞋、棕色切尔西短靴、黑色骑士靴、黑色袜靴、棕色过膝长靴、黑色尖头过膝靴、白色老爹鞋、黑色马丁靴、白色帆布鞋、粉色水钻高跟凉鞋、米色绒面中筒靴\n- 靴筒高要写进 type（短靴/中筒靴/长筒靴/过膝靴），分不清时看筒口到膝盖/小腿的位置\n▸ 其它单品（上衣/裤装/裙装/外套等）：type 同样「颜色开头+品类+关键款式」，颜色要准，如：白色圆领针织开衫、浅蓝直筒牛仔裤、黑色A字连衣裙、卡其色风衣。\n3. fit（版型，适用衣裤裙）：紧身/修身/宽松/Oversized/直筒/阔腿/A字/H型/收腰/包臀/廓形\n4. design_detail（款式细节）：泡泡袖/荷叶边/百褶/开衩/高腰/低腰/系带/绑带/方领/V领/圆领/高领/方领/一字肩/露肩/露背/拉链/纽扣/口袋/双排扣/吊带/垫肩\n5. material（面料/材质/质感）：针织/牛仔/蕾丝/雪纺/缎面/绒面/漆皮/皮革/丝绒/灯芯绒/纯棉/羊毛/网纱/透肉/哑光/亮片/格纹/条纹/碎花/纯色\n6. attributes（图片属性）：露脸/不露脸/全身/半身/坐姿/站姿/正面/侧面\n7. dominant_colors：主色 2~3 个 hex（如 #2B3A67 藏青、#1A1A1A 黑）\n8. atmosphere（氛围，≤2）：清新/甜美/性感/酷飒/优雅/休闲/复古/暗黑/学院/御姐/街头/日常/精致/氛围感\n9. expression（表情，有则写）：微笑/大笑/冷脸/无表情/嘟嘴/侧脸/低头\n10. leg_posture（腿部姿态，有则写）：交叉腿/并拢/分开/跷腿/踮脚/抬腿\n只输出 JSON，不要任何解释文字：\n{"style":[],"items":[{"type":"","color":"","features":[]}],"fit":[],"design_detail":[],"material":[],"attributes":[],"dominant_colors":[],"atmosphere":[],"expression":[],"leg_posture":[]}'


class Settings(BaseSettings):
    """应用设置类，自动从环境变量和 .env 文件加载。"""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 应用
    app_name: str = "Fashion Inspo"
    app_version: str = "0.1.0"
    debug: bool = True

    # 服务器（默认仅本机可访问；确需局域网/真机访问时再显式改绑 0.0.0.0）
    host: str = "127.0.0.1"
    port: int = 18888

    # 数据库
    database_url: str = f"sqlite+aiosqlite:///{_DB_PATH.as_posix()}"

    # 文件存储
    storage_root: Path = Path(__file__).parent.parent / "storage"
    images_dir: Path = storage_root / "images"
    thumbnails_dir: Path = storage_root / "thumbnails"
    videos_dir: Path = storage_root / "videos"
    trash_dir: Path = storage_root / "trash"  # 垃圾桶（软删除文件移入此目录）
    # 视频关键帧：子目录按素材 ID 命名（storage/keyframes/{inspiration_id}/frame_001.jpg），
    # 不入库，由 /api/files/keyframes/{id} 按需列目录返回
    keyframes_dir: Path = storage_root / "keyframes"
    # 人物照片（模特写真）：与素材库 images/ 分离，避免被完整性检查误判为孤立文件
    person_photos_dir: Path = storage_root / "person_photos"
    person_thumbnails_dir: Path = storage_root / "person_thumbnails"

    # 上传大小限制（MB）：防止误传超大文件导致内存与磁盘暴涨
    max_image_upload_mb: int = 20  # 图片/缩略图
    max_video_upload_mb: int = 500  # 视频

    # 向量检索（LanceDB 嵌入式向量库）
    lancedb_dir: Path = storage_root / "lancedb"  # LanceDB 数据目录（文件落盘，可随项目迁移）
    lancedb_text_table: str = "text_vectors"  # 文本向量表
    lancedb_image_table: str = "image_vectors"  # 图像向量表
    lancedb_text_dim: int = 384  # 文本向量维度（Ollama all-minilm 输出 384 维）
    lancedb_image_dim: int = 512  # 图像向量维度（CLIP ViT-B/32 输出 512 维）
    clip_model_name: str = "clip-ViT-B-32"  # 图像向量模型（sentence-transformers / open_clip 均可加载）
    vector_top_k_default: int = 20  # 向量搜索默认 TopK
    vector_similarity_weight: float = 0.6  # 混合排序：视觉相似度权重
    vector_tag_weight: float = 0.4  # 混合排序：标签匹配权重

    # 缩略图
    thumbnail_size: tuple[int, int] = (400, 600)
    thumbnail_quality: int = 85

    # 视频关键帧提取（ffmpeg）
    keyframe_interval_seconds: float = 3.0  # 固定间隔抽帧间隔（秒）
    keyframe_scene_threshold: float = 0.0  # 场景检测阈值（0=禁用；如 0.3 表示画面变化 >30% 时抽帧）
    keyframe_max_frames: int = 60  # 单视频关键帧数量上限（防长视频刷爆磁盘）
    face_scan_video_max_frames: int = 3  # 人脸扫描每个视频取前 N 帧

    # AI / Ollama
    ollama_base_url: str = "http://localhost:11434"
    # 默认视觉模型与 README 推荐一致（Qwen3-VL:8B-Instruct）；
    # 可在 .env 用 OLLAMA_VISION_MODEL 覆盖，或在「AI 模型管理」页切换
    ollama_vision_model: str = "qwen3-vl:8b-instruct"
    ollama_embedding_model: str = "all-minilm"
    ai_analysis_timeout: int = 300  # 秒（思考型模型推理耗时更长）
    ai_low_confidence_threshold: float = 0.6
    ai_temperature: float = 0.7
    ai_top_p: float = 0.9
    ai_top_k: int = 40
    ai_num_predict: int = 4096  # 思考型模型需为推理预留 token
    # Ollama 推理上下文窗口：视觉模型对高分辨率图片编码消耗大量 token
    # （实测一张 1.8MB 图片占约 4000 token），Ollama 默认 4096 会把 JSON 输出硬性截断，
    # 必须显式传入更大的 num_ctx
    ai_num_ctx: int = 16384

    # 质量审核
    manual_upload_auto_approve: bool = True  # 手动上传默认免审核（直接标记为已通过）
    ai_generated_confidence_threshold: float = 0.8  # AI 生成检测置信度阈值，仅 ≥ 此值才标记「疑似 AI」

    # 标签命名质量
    # 标签名超过该字数判定为「低质命名（过长）」：标签健康度扫描与 AI 打标提取共用同一阈值，
    # 可在 .env 用 TAG_NAME_MAX_LENGTH 覆盖
    tag_name_max_length: int = 12

    # 人脸识别子服务（face-service：独立 Python 3.10 环境运行 insightface）
    # 主后端 3.12 不兼容 insightface，人脸能力通过 HTTP 调用子服务；
    # 留空表示未部署子服务，人脸相关功能自动降级/不可用
    face_service_url: str = "http://127.0.0.1:18889"
    face_service_timeout: float = 30.0  # 子服务调用超时（秒）
    face_match_threshold: float = 0.5  # 模特人脸匹配余弦相似度阈值（建议 0.45~0.55，按样本调整）

    # 垃圾桶（软删除）
    # 0 表示禁用自动回收：垃圾桶素材永不自动清理，仅可手动恢复或彻底删除。
    # 如需恢复「到期自动清理」，在 .env 设置 TRASH_RETENTION_DAYS=30 等正整数。
    trash_retention_days: int = 0

    # 任务队列并发（worker 为独立进程 python -m app.worker）
    # worker 同时执行的任务数：值越大整体吞吐越高，但 Ollama 显存压力与
    # SQLite 写锁竞争也随之增大（多任务同时写进度/心跳易触发 database is locked），
    # 建议保持 1~2；仅多卡/强机器再尝试更大值。可在 .env 用 WORKER_CONCURRENCY 覆盖
    worker_concurrency: int = 1
    # 批内分析并发度：批量分析/组合分析/质量审核任务内部同时分析的素材数
    # （原写死常量 _ANALYZE_CONCURRENCY=1）。注意 worker 与 API 进程
    # （routers/ai_shared.py 的独立信号量=2）互不感知，最坏并发出路为
    # worker_concurrency × 本项 + API 侧 2 路；显存吃紧时优先调小本项，建议 1~2。
    # 可在 .env 用 ANALYZE_CONCURRENCY 覆盖
    analyze_concurrency: int = 1

    # 视频多帧分析：每个视频参与 AI 分析的最大关键帧数（多帧标签按最高置信度
    # 融合，覆盖整套穿搭的不同镜头）。帧数越多语义越全但 Ollama 调用成本线性
    # 增长（N 帧 = N 次视觉调用）；设 1 退化为仅分析首帧的旧行为。可在 .env
    # 用 VIDEO_ANALYSIS_MAX_FRAMES 覆盖
    video_analysis_max_frames: int = 3

    # 负样本初筛器（阶段 2：CLIP 向量 + sklearn 轻量分类器）
    quality_classifier_threshold: float = 0.9  # 自动拒绝的置信度阈值（宁缺毋滥，低置信度仍走 VLM 复审）

    # AI 分析 Prompt（运行时可变，前端可编辑）
    # 核心治理原则：单品名只含品类+关键款式特征（不含颜色、≤12 字，与
    # TAG_NAME_MAX_LENGTH 一致），颜色一律写入 items[].color；原 wear_style
    # 维度拆分为 design_detail（款式细节）与 material（面料材质）。
    ai_analysis_prompt: str = _DEFAULT_AI_ANALYSIS_PROMPT

    # 采集引擎
    scraper_request_delay: float = 2.0  # 请求间隔（秒）
    scraper_max_concurrent: int = 3
    scraper_default_max_count: int = 20  # 每次采集默认数量（降低单次规模以规避风控）
    scraper_browser_headless: bool = True
    # Chrome 路径（Windows 用户级安装默认位置）；留空时自动探测常见安装路径
    chrome_executable: str = ""
    chrome_user_data_dir: str = ""  # 采集专用 Chrome 用户数据目录；留空使用默认目录
    chrome_debug_port: int = 9222  # Chrome 调试端口
    chrome_auto_restart_limit: int = 3  # Chrome 崩溃自动重启次数上限
    chrome_idle_timeout: int = 600  # 无活动采集任务时的空闲自动关闭秒数（0=禁用）
    chrome_startup_timeout: int = 20  # 启动就绪轮询超时（秒）
    scraper_task_auto_retry: int = 2  # 采集任务崩溃自动续采次数上限

    # f2 抖音素材：一键获取（f2 增量下载 → 去重 → 入库）
    # 是否每日自动增量入库（关闭时只保留界面上的手动「一键获取素材」）
    f2_import_auto_enabled: bool = False
    # 自动获取的最小间隔（小时）：距最近一次任务创建时间不足则跳过本轮；
    # f2 自身按 last_aweme_id 只下新作品，所以间隔内重复触发没有额外收益
    f2_import_interval_hours: int = 24
    # 自动获取是否跳过 live 实况分段视频（与手动入口的开关同义）
    f2_import_auto_skip_live: bool = False

    # 安全
    api_key: str = ""  # API 密钥，为空则跳过认证（开发模式）
    cors_origins: list[str] = [
        "http://localhost:17777",
        "http://127.0.0.1:17777",
    ]

    # 启动后自动补备（与每日 03:00 的 schtasks 双通道，backup.lock 互斥）
    backup_on_startup: bool = True  # .env 设 BACKUP_ON_STARTUP=false 关闭
    backup_target_path: str = "E:/fashion-inspo-backups"  # 备份目标根目录
    backup_startup_delay_minutes: int = 10  # 启动后延迟多久再检查（避开迁移/初始化竞争）
    backup_min_interval_hours: int = 20  # 距上次成功备份小于此时长则跳过
    backup_tick_hours: int = 6  # 常驻循环的检查周期

    @property
    def storage_dirs(self) -> dict[str, Path]:
        """返回所有存储目录的映射。"""
        return {
            "images": self.images_dir,
            "thumbnails": self.thumbnails_dir,
            "videos": self.videos_dir,
            "keyframes": self.keyframes_dir,
            "trash": self.trash_dir,
            "person_photos": self.person_photos_dir,
            "person_thumbnails": self.person_thumbnails_dir,
        }
    
    @property
    def config_constants(self) -> "ConfigConstants":
        """返回常量配置实例。

        注解使用字符串前向引用：ConfigConstants 定义在本类之后，
        且全局实例在模块尾部才创建，属性体在首次访问时才会求值。
        """
        return config_constants


# 全局单例配置
settings = Settings()


class ConfigConstants:
    """常量配置类，从配置文件中提取硬编码值。"""
    
    def __init__(self, settings_obj):
        self.settings = settings_obj
    
    # AI 相关常量
    @property
    def ai_temperature(self):
        return getattr(self.settings, 'ai_temperature', 0.7)
    
    @property
    def ai_top_p(self):
        return getattr(self.settings, 'ai_top_p', 0.9)
    
    @property
    def ai_top_k(self):
        return getattr(self.settings, 'ai_top_k', 40)
    
    @property
    def ai_num_predict(self):
        return getattr(self.settings, 'ai_num_predict', 4096)
    
    @property
    def ai_num_ctx(self):
        return getattr(self.settings, 'ai_num_ctx', 16384)
    
    @property
    def ai_low_confidence_threshold(self):
        return getattr(self.settings, 'ai_low_confidence_threshold', 0.6)
    
    @property
    def ai_generated_confidence_threshold(self):
        return getattr(self.settings, 'ai_generated_confidence_threshold', 0.8)
    
    @property
    def ai_analysis_prompt(self):
        # 优先取 settings 当前值（可能被 prompt.txt 覆盖），兜底用模块级默认文本
        return getattr(self.settings, 'ai_analysis_prompt', _DEFAULT_AI_ANALYSIS_PROMPT)
    
    # 向量检索常量
    @property
    def vector_top_k_default(self):
        return getattr(self.settings, 'vector_top_k_default', 20)
    
    @property
    def vector_similarity_weight(self):
        return getattr(self.settings, 'vector_similarity_weight', 0.6)
    
    @property
    def vector_tag_weight(self):
        return getattr(self.settings, 'vector_tag_weight', 0.4)
    
    @property
    def text_vector_dim(self):
        return getattr(self.settings, 'lancedb_text_dim', 384)
    
    @property
    def image_vector_dim(self):
        return getattr(self.settings, 'lancedb_image_dim', 512)
    
    # 图片处理常量
    @property
    def thumbnail_size(self):
        return getattr(self.settings, 'thumbnail_size', (400, 600))
    
    @property
    def thumbnail_quality(self):
        return getattr(self.settings, 'thumbnail_quality', 85)
    
    @property
    def max_image_upload_mb(self):
        return getattr(self.settings, 'max_image_upload_mb', 20)
    
    @property
    def max_video_upload_mb(self):
        return getattr(self.settings, 'max_video_upload_mb', 500)
    
    # 标签常量
    @property
    def tag_name_max_length(self):
        return getattr(self.settings, 'tag_name_max_length', 12)
    
    @property
    def seed_tags(self):
        return getattr(self.settings, 'seed_tags', [
            "JK制服", "汉服", "Lolita", "Y2K", "CleanFit", "法式", "日系", "韩系", 
            "学院风", "街头", "新中式", "复古", "极简", "美式复古", "英伦风", 
            "波西米亚", "运动风", "甜美风", "暗黑风"
        ])
    
    # 质量审核常量
    @property
    def quality_classifier_threshold(self):
        return getattr(self.settings, 'quality_classifier_threshold', 0.9)
    
    @property
    def manual_upload_auto_approve(self):
        return getattr(self.settings, 'manual_upload_auto_approve', True)
    
    # 人脸识别常量
    @property
    def face_service_timeout(self):
        return getattr(self.settings, 'face_service_timeout', 30.0)
    
    @property
    def face_match_threshold(self):
        return getattr(self.settings, 'face_match_threshold', 0.5)
    
    # 任务队列常量
    @property
    def poll_interval(self):
        return getattr(self.settings, 'poll_interval', 1.0)
    
    @property
    def heartbeat_interval(self):
        return getattr(self.settings, 'heartbeat_interval', 10.0)
    
    @property
    def stale_heartbeat_threshold(self):
        return getattr(self.settings, 'stale_heartbeat_threshold', 90.0)
    
    # 爬虫常量
    @property
    def scraper_request_delay(self):
        return getattr(self.settings, 'scraper_request_delay', 2.0)
    
    @property
    def scraper_max_concurrent(self):
        return getattr(self.settings, 'scraper_max_concurrent', 3)
    
    @property
    def scraper_default_max_count(self):
        return getattr(self.settings, 'scraper_default_max_count', 20)
    
    @property
    def scraper_browser_headless(self):
        return getattr(self.settings, 'scraper_browser_headless', True)
    
    @property
    def chrome_debug_port(self):
        return getattr(self.settings, 'chrome_debug_port', 9222)
    
    @property
    def chrome_auto_restart_limit(self):
        return getattr(self.settings, 'chrome_auto_restart_limit', 3)
    
    @property
    def chrome_idle_timeout(self):
        return getattr(self.settings, 'chrome_idle_timeout', 600)
    
    @property
    def chrome_startup_timeout(self):
        return getattr(self.settings, 'chrome_startup_timeout', 20)
    
    @property
    def task_auto_retry(self):
        return getattr(self.settings, 'task_auto_retry', 2)


# 创建配置常量实例
config_constants = ConfigConstants(settings)

# ── Chrome 路径自动探测 ──
# 配置留空时，按常见安装位置探测 Chrome 可执行文件；用户数据目录使用默认位置。
def _detect_chrome_executable() -> str:
    """探测常见安装位置的 Chrome 可执行文件，未找到返回空字符串。"""
    import os

    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ""


if not settings.chrome_executable:
    detected = _detect_chrome_executable()
    if detected:
        settings.chrome_executable = detected

if not settings.chrome_user_data_dir:
    import os

    default_dir = os.path.expandvars(r"%LOCALAPPDATA%\chrome-scraper-profile")
    if not default_dir.startswith(r"%"):
        settings.chrome_user_data_dir = default_dir
    else:
        # 非 Windows 环境：使用用户主目录
        settings.chrome_user_data_dir = str(Path.home() / "chrome-scraper-profile")

# 尝试从 prompt.txt 加载已持久化的 prompt
_prompt_file = Path(__file__).parent.parent / "prompt.txt"
if _prompt_file.exists():
    try:
        saved_prompt = _prompt_file.read_text(encoding="utf-8").strip()
        if saved_prompt:
            settings.ai_analysis_prompt = saved_prompt
    except Exception:
        pass  # 加载失败时使用默认值
