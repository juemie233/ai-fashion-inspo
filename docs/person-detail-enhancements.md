# 人物详情页增强方案（2026-10-27）

> 本文只做**方案、取舍与验收口径**，不含实现代码。所有数字都是当天在本库实测（SQL 见 §6，可复现）。
> 基线：**498 位博主 / 1 位模特 / 22,628 条素材绑定 / 30,452 条未删素材**。

## 1. 现状盘点

### 1.1 页面结构（`PersonDetailView.vue`，332 行 + 6 个子组件）

| 区块 | 现在能看到 / 能做什么 | 数据来源 |
|---|---|---|
| 头部卡 | 头像（手选/上传，未设置显示首字）、名称 + 内容类型徽标、平台、**素材数**、创建日期、简介（可 AI 生成）、主页链接、`下载所有作品`、编辑、删除 | `GET /api/bloggers/{id}` |
| 人物组 | 同一现实人物跨平台账号绑定（仅博主） | `person_group_id` + `/groups/*` |
| 人脸注册 | 上传照片或从已关联素材选 1~5 张注册（平均池化特征） | `POST /{id}/face` |
| 风格画像 | 高频标签 / 类别分布 / 素材趋势；点标签跳搜索 | `style_profile`（3 条聚合查询） |
| 素材区 | 瀑布流分页（20/页）、排序 `newest / oldest / confidence`、全屏浏览、人脸注销多选、清空全部绑定 | `GET /{id}/inspirations` |
| 照片组 | 模特写真分组（仅模特） | `photo_sets` |
| 编辑弹窗 | 名称/平台/账号/IP/主页/简介/头像/AI 生成简介 | `PATCH /{id}` |

### 1.2 数据家底（决定优先级）

| 指标 | 实测 | 说明 |
|---|---|---|
| 有头像 | **401 / 498**（80.5%） | 已做过批量补全 |
| **有简介** | **22 / 498（4.4%）** | 几乎全空 |
| **已注册人脸** | **22 / 498（4.4%）** | 人脸扫描的「已匹配」能力实际没运转 |
| 有人脸匹配记录的博主 | **30 / 498**（468 位 0 条） | 注册才可能匹配，所以只覆盖已注册的那批 |
| 素材里待匹配的人脸检测 | 32,179 条检测：confirmed 9,633 · pending 17,859 · 未标记 4,687 | 其中「单脸素材内 ≥3 条未匹配」的博主有 **141 位** |
| 有 IP 属地 | 412 / 498 | 批量补全主产物 |
| 有 `platform_user_id` | 421 / 498 | 抖音号（sec_uid） |
| 人物组 | **2 组** | 跨平台绑定功能使用率极低 |
| 素材数分布 | 1~4 条：315 位 · 5~19：125 · 20~99：35 · 100~499：14 · **500+：9** | 头部博主（最大 3,426 条）用瀑布流看不清 |
| 抖音素材带作品 ID | **19,641 / 19,641（100%）** | 「按作品分组」对抖音可行 |
| 素材质量 | approved 23,431 · pending 6,929 · rejected 92 | 详情页没有任何质量视角 |
| 平台 | 抖音 486 · 小红书 12 | 小红书是少数派 |

### 1.3 三个真问题

1. **资料面几乎空白，但数据其实在本地**：f2 用户库 `douyin_users.db` 有 **426 行**，能按 `sec_user_id` 精确匹配上 **407 位**博主，其中 **376 位带签名（signature）**、**407 位带粉丝数**、以及作品数/获赞/关注/性别/城市/抖音号。现在的补全只写了 `ip_location` 一项，签名与粉丝数全丢——**简介覆盖率可以从 4.4% 直接到 75%+，且离线、零网络、零风控**。
2. **人脸上限**：注册率 4.4% 意味着「按博主自动匹配素材」这条链几乎没跑起来；而注册入口藏在详情页中部，需要用户先上传/手动挑图，没人会为 498 人做这件事。
3. **素材规模两极分化**：315 位博主只有 1~4 条（瀑布流足够），但 9 位有 500+ 条。「逐张瀑布流」对头部博主是错误视图——他们需要**作品/时间线**。

## 2. 增强项

> 每项给：目标 / 交互 / 数据与接口 / 改动面 / 成本 / 风险。成本按「一个熟悉本仓库的人」估：**S < 半天、M 1~2 天、L 3 天+**。

### P0（低成本、高收益，建议先做）

#### A1 博主资料卡：从 f2 用户库补齐「粉丝 / 作品数 / 获赞 / 签名 / 城市 / 性别」
- **目标**：一次离线回填，把 407 位博主的公开资料栏填满，简介覆盖率 4.4% → 75%+。
- **交互**：详情页头部新增「数据快照」行：`粉丝 12.3万 · 作品 486 · 获赞 88.1万`，旁边小字标注**快照时间**（`enriched_at`）。资料缺失时给「补齐资料」按钮（单条）。
- **数据与接口**：
  - 读 `douyin_users.db` → `user_info_web`：`follower_count / following_count / aweme_count / total_favorited / signature / gender / city / short_id / unique_id`。**精确匹配** `bloggers.platform_user_id ↔ sec_user_id`（沿用现有补全的匹配纪律：不猜昵称）。
  - 现有 `POST /bloggers/enrich-missing-profile` 只写 `ip_location`，扩展为「同一次读库写全部字段」；新增单条 `POST /bloggers/{id}/enrich-profile`（详情页按钮）。
  - 需要新列：`follower_count / following_count / aweme_count / total_favorited / signature / city / gender / stats_synced_at`（Alembic 迁移，一次加完）。
- **改动面**：`bloggers` 表 + schema（`_PersonFields` 只读输出，不参与创建校验）+ 补全服务 + 头部组件。
- **成本**：**M**（迁移 + 服务 + UI）。
- **风险**：① 粉丝数是**快照**，不能暗示实时——必须显示同步时间；② 抖音数据可能被 f2 采到的是「旧值」，同一个人不同时间差别很大，所以不提供「排序按粉丝」以外的时间敏感用法；③ `signature` 可能含换行/emoji，前端要保留换行不做 HTML 渲染。

#### A2 头部信息重排：三个状态 chip
- **目标**：进页面 1 秒内判断「这号还活跃吗 / 值得追吗 / 人脸能用吗」，不用滚到下面。
- **交互**：名称下方一行 chip：
  - `最近素材 3 天前`（来自已有 `last_inspiration_at`，超 90 天标灰「疑似断更」）
  - `人脸 未注册 / 已注册`（已有 `face_registered`，点击可直接跳到注册区）
  - `粉丝 12.3万`（A1 完成后）
- **数据**：全部**现成字段**（`last_inspiration_at` 已在列表用过、`face_registered` 已在 `_to_dict` 输出，只是详情页没展示）。
- **成本**：**S**（纯前端 + 1 条字段确认）。

#### A3 人脸注册提效（两步走，别指望一条路解决）
现状：注册率 4.4%（22/498），而「已匹配到博主」的检测只存在于**已注册**的博主身上
（468 位一条都没有）——所以「拿已匹配素材一键注册」只能覆盖 **30 位**，解决不了大盘。

- **A3a 一键从已匹配素材注册（覆盖 30 位，低风险，S）**
  - 交互：人脸区 `未注册 → 用已匹配素材一键注册`：自动取 `inspiration_face_detections.matched_blogger_id = 该博主`、`match_status = confirmed`、单脸素材里 `det_score` 最高的 3 张，展示缩略图可替换。
  - 接口：`POST /{id}/face` 已支持 `inspiration_ids`（**无需新端点**）。
- **A3b 候选脸 + 人工确认批量注册（覆盖 141~168 位，M）**
  - 数据基础（实测）：在该博主**素材内**、`matched_blogger_id IS NULL` 的检测里，限定「单脸素材（该素材只检出 1 张脸）」后，**141 位博主有 ≥3 条干净候选**（不限定单脸则是 168 位有 ≥3 条）。
  - 交互：人脸区给「候选脸」面板——按该博主的候选检测聚类（同一张脸出现 ≥3 次算一个簇），每个簇给封面 + 出现次数 + 平均 `det_score`；用户勾选确认后，取该簇里质量最高的 3 张做一次注册。**一次可以审 10 位博主**（列表式批量确认）。
  - 为什么必须人工确认：采集来的素材里会出现路人、买家秀、其他博主；把别人的脸注册成她，会污染整库匹配（`match_status='confirmed'` 的误判比「没注册」代价高得多）。
- **成本**：A3a **S**、A3b **M**（后端聚类预选 + 前端批量确认弹窗）。
- **风险**：① 侧脸/合照污染平均池化 → 只取单脸素材 + `det_score` 阈值；② 候选脸不一定是博主本人（尤其「我的喜欢」来源的素材）→ 人工确认是硬闸门。

#### A4 简介来源可见 + AI 生成增强
- **目标**：区分「f2 签名原文 / AI 生成 / 手填」，AI 生成时把签名与风格画像一起喂进去，避免 AI 编造风格。
- **交互**：简介块右上角小标签显示来源；AI 生成弹窗里展示「将参考：签名 + Top8 标签」。
- **数据**：A1 的 `signature` + 现有 `style_profile.top_tags`。
- **成本**：**S**（生成接口已存在，只需扩 prompt 输入 + 前端标签）。

### P1（中等成本，解决体感问题）

#### B1 素材区双视图：「作品」/「时间线」
- **目标**：头部博主（9 位 500+ 素材，最大 3,426）不再逐张刷瀑布流。
- **交互**：素材区顶部切 `作品 | 全部`：
  - **作品视图**：按 `source_platform_id` 分组（抖音 100% 有），一组一个封面（取该组第一张），角标「6 张」；点开看组内所有图。
  - **时间线视图**：按「入库时间」分月折叠（`2026-10 · 128 张`），配活跃度小柱状。
- **数据**：现有列即可（`source_platform_id`、`created_at`）；需要后端 `group_by=work` 参数或新端点返回分组摘要。
- **成本**：**M**。
- **风险**：小红书素材没有稳定作品 ID（12 位博主的素材多为单图），分组会退化成 1 张 1 组 → 该平台隐藏作品视图。

#### B2 素材筛选：标签 / 媒体类型 / 质量 / 评分 / 收藏 / 有无人脸
- **目标**：从「翻完 3,426 张」变成「只看这 20 张」。
- **交互**：素材区工具条：标签下拉（该博主使用过的标签）、`图片/视频`、`质量状态`、`评分 ≥N`、`已收藏`、`人脸已匹配`。
- **数据**：全部现成（`is_favorite`、`rating`、`quality_status`、`media_type`、`inspiration_tags`、`face_detections`）。
- **成本**：**M**（后端给 `list_inspirations` 加筛选参数 + 前端工具条）。
- **风险**：筛选 + 瀑布流分页的组合要保证 total 与分页一致（沿用素材库现有的「筛选变则回第 1 页」约定）。

#### B3 活跃度卡：新增曲线 + 断更提示
- **目标**：判断「还在更新吗」，用于决定是否继续追（或删除）。
- **交互**：近 12 个月「入库新增」柱状 + 最近素材时间 + 「已 92 天未新增」提示。
- **数据**：`inspiration_bloggers` × `inspirations.created_at` 聚合（一条 SQL）。
- **成本**：**S**（复用风格画像的趋势数据形状，直接同一条接口返回）。
- **注**：`style_profile.trend` 现在已存在，先确认它的口径（按素材 created_at 还是作品时间）再决定是否复用。

#### B4 质量构成
- **目标**：知道「这位博主的素材有多少是审核通过的」，决定清理与追更。
- **交互**：头部件一条三段比例条 `通过 88% / 待审 11% / 拒绝 1%`，点段跳筛选（B2）。
- **数据**：`quality_status` 聚合（一条 SQL）。
- **成本**：**S**。
- **风险**：待审占比高不等于素材差（只是没跑审核），文案要写清。

#### B5 「可能是同一个人 / 风格相近」
- **目标**：把只有 2 组的人物组用起来；也给「找同类博主」一个入口。
- **交互**：详情页底部两个区块：
  - **疑似重复**：归一化同名或（同平台 + 同 IP + 头像相似）→ 「合并为同一人」。
  - **风格相近**：用 `top_tags` 余弦相似度取前 5，跳人物列表/详情。
- **数据**：同名已有 `/duplicate-names`（列表页在用）；相似度用 `inspiration_tags` 聚合，一条 SQL。
- **成本**：**M**（相似度接口）。
- **风险**：相似度阈值要实测调（建议先用「共同标签数 ≥ 3 且 Jaccard ≥ 0.25」）。

### P2（可选，价值更长期）

| # | 能力 | 说明 | 成本 |
|---|---|---|---|
| C1 | 人物自有标签/备注 | 「极简 / 通勤 / 已合作 / 素人」这类**人物级**标注（不是素材标签） | M（新表 + 列表筛选） |
| C2 | 两位博主对比 | 风格画像并排 + 共同标签差异 | M |
| C3 | 收藏/置顶人物 | 常看的人置顶（现在只有标签置顶，人物没有） | S~M |
| C4 | 采集日历热力图 | 按作品发布时间 vs 入库时间两组颜色，看「补采是否到位」 | M |
| C5 | 该博主的人脸待审核候选 | 直接从详情页跳人脸扫描审核页并预筛该博主 | S |

## 3. 明确不做 / 暂缓（附理由）

| 想法 | 为什么不做 |
|---|---|
| 粉丝数「实时刷新」 | 抖音没有稳定免登录接口；f2 只在采主页时写库，实时刷新等于把风控风险引进来。只做**快照 + 显示同步时间**。 |
| 模特页对齐博主全部能力 | 全库只有 **1 位模特**，收益接近 0；现有照片组 + 人脸注册够用。 |
| 作品分组自动推荐跨平台合并 | 人物组只有 2 组，先观察；已有同名建议覆盖最常见场景。 |
| 按「清晰度/尺寸」排序素材 | `inspirations` 没有宽高/清晰度列，要加列 + 回填 3 万张（跑一遍全库解码）——成本远高于收益。 |
| 瀑布流虚拟化重写 | 现在 20 条/页，无性能问题；真正的问题是「视图不对」（B1），不是渲染慢。 |
| 详情页内嵌「按此博主采集」 | 已有列表页批量采集 + f2 下载入口；重复入口会让采集链路两套口径。 |

## 4. 分期与验收

| 阶段 | 内容 | 验收口径（可 SQL 复现） |
|---|---|---|
| **P0**（1 周内） | A1 + A2 + A3 + A4 | 简介覆盖率 **22 → ≥370**（`bio` 非空计数）；资料卡 6 字段填充率 ≥ 80%；人脸注册 **22 → ≥30**（A3a 一键注册，覆盖全部有匹配记录的人）+ 候选脸面板可用（A3b 首批人工确认 **≥50 位**）；详情页首屏含「最近素材 / 人脸 / 粉丝」三个 chip |
| **P1**（1~2 周） | B1 + B2 + B3 + B4（B5 视余量） | 3,000 条以上的博主可切「作品」视图并按作品翻页；素材筛选 4 类可用且 total 与筛选一致；每个博主详情页 1 秒内能看到「近 12 月新增」与「质量构成」 |
| **P2**（按需） | C1~C5 | 单项评审，不设整体指标 |

## 5. 依赖与风险总表

| 依赖/风险 | 影响 | 应对 |
|---|---|---|
| f2 用户库 schema（`user_info_web`）可能随 f2 版本变 | A1 直接读库，schema 一改就静默空 | 读取时**显式列出列名**并做缺列兜底（缺列 → 只补能补的，日志告警），不写 `SELECT *` |
| 粉丝/获赞是 f2 采集时刻的快照 | 用户以为是实时的 | 头部固定展示「同步于 X 天前」；超过 30 天标灰 |
| 人脸注册质量取决于素材正脸 | 平均池化被侧脸/路人污染 → 误匹配（比不注册更糟） | 只取「单脸素材 + `det_score` 前 3」+ **人工确认**（A3b）；注册后可用「人脸注销」回退 |
| 作品分组依赖 `source_platform_id` | 小红书缺失 → 分组退化 | 小红书隐藏作品视图；`source_platform_id` 缺失的素材归入「未分组」 |
| 与「批量补全头像/资料」任务的关系 | 两套补全逻辑容易漂移 | 单条补全与批量补全**共用同一个 service 函数**，批量只是循环调用 |

## 6. 附：现状数据索引（可复现）

```sql
-- 覆盖率
select count(*) total,
       sum(avatar_path is not null) avatar,
       sum(bio is not null and bio <> '') bio,
       sum(platform_user_id is not null) douyin_id,
       sum(ip_location is not null) ip
from bloggers;

-- 人脸注册与匹配覆盖
select count(*) from blogger_face_embeddings;                     -- 22
select coalesce(match_status,'null'), count(*) from inspiration_face_detections group by 1;
select case when coalesce(cnt,0)=0 then '0' else '10+' end bucket, count(*)
from bloggers b left join (select matched_blogger_id bid, count(*) cnt
     from inspiration_face_detections where matched_blogger_id is not null group by 1) d
     on d.bid = b.id group by bucket;                             -- 0:468 / 10+:30

-- A3b 候选覆盖（单脸素材里的未匹配检测）
with per_insp as (select inspiration_id, count(*) faces from inspiration_face_detections group by 1)
select case when coalesce(n,0)=0 then '0' when n<3 then '1-2' else '3+' end bucket, count(*)
from bloggers b left join (
  select ib.blogger_id bid, count(*) n
  from inspiration_face_detections d
  join inspiration_bloggers ib on ib.inspiration_id = d.inspiration_id
  join per_insp p on p.inspiration_id = d.inspiration_id
  where d.matched_blogger_id is null and p.faces = 1
  group by 1) x on x.bid = b.id group by bucket;                  -- 3+:141

-- 素材分档
select case when cnt<5 then '1-4' when cnt<20 then '5-19'
            when cnt<100 then '20-99' when cnt<500 then '100-499' else '500+' end bucket,
       count(*) bloggers
from (select b.id, count(ib.inspiration_id) cnt
      from bloggers b left join inspiration_bloggers ib on ib.blogger_id = b.id
      group by b.id) group by bucket order by 2 desc;

-- 抖音作品 ID 覆盖（决定 B1 可行性）
select count(*) total,
       sum(source_platform_id is not null) with_work_id
from inspirations where deleted_at is null and source_type = 'douyin';

-- f2 用户库可补多少（在 f2 目录下的 douyin_users.db 上跑）
select count(*) from user_info_web;                    -- 426
-- 与 bloggers.platform_user_id ↔ sec_user_id 的连接在 Python 侧做（跨库）
```

**相关文件（现状）**

- 视图：[PersonDetailView.vue](../web/src/views/PersonDetailView.vue)
- 子组件：[components/person/detail/](../web/src/components/person/detail/)
- 逻辑：[usePersonDetail.ts](../web/src/composables/usePersonDetail.ts)
- 后端路由：[routers/bloggers.py](../backend/app/routers/bloggers.py)、服务：[services/person/](../backend/app/services/person/)
- 补全服务：[blogger_enrichment_service.py](../backend/app/services/blogger_enrichment_service.py)

## 7. 落地补充：随机浏览（2026-10-28）

方案之外补的一个小功能，解决「9 位博主的素材 500~3,426 条，分页翻不完」：

- **接口**：`GET /api/bloggers/{id}/random?size=1..30` + 与 `/inspirations` **完全一致**的
  筛选参数——所以在筛选结果里随机也成立；返回 `total`（满足筛选的素材数，前端据此
  显示「从 N 张里随机」）。实测博主 287（3,261 条）随机抽 3 张 **47 ms**。
- **交互**：素材区「🎲 随机浏览」→ 弹窗一次一张；「换一张」重新随机，「上一张」回溯
  **本次浏览历史**（本地栈，不发请求——随机倒退会让人迷路）；空格/→ 换一张、← 上一张、
  Esc 关闭；图片可「看大图」交给既有灯箱，视频直接内联播放。
- **与全屏浏览的分工**：灯箱翻的是「当前页」固定列表（细看），随机浏览每次都是新结果（逛）。
- **口径**：换人物即清空当前图与历史（否则会先看到上一位的图）；抽取中重复点击
  只发一次请求（防连点乱序）；抽到重复不特殊处理（素材只有 1~4 条的 315 位博主，
  强行去重会让「换一张」变成「换不出来」）。
