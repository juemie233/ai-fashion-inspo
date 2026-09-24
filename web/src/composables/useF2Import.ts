/** f2「一键获取素材」的状态与提交逻辑。
 *
 * 后端链路：`POST /api/scraper/f2-import` 创建后台任务（f2 增量下载 → 五层去重 →
 * 入库），由独立 worker 执行，进度在「任务中心」查看；本组合式只负责可用性检查
 * 与提交，不持有任务进度（避免与任务中心重复轮询）。
 */

import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { getApiErrorMessage } from '@/utils/apiError'

/** 「我的喜欢」下载阶段的实时统计（后端 result.like_progress，其它阶段为空） */
export interface F2LikeProgress {
  /** 产物目录内已落盘的文件总数（含往次下载） */
  files: number
  /** 已落盘文件的总字节数 */
  bytes: number
  /** 本次任务新增的文件数（点赞总数事先未知，靠它看「这轮拉回来多少」） */
  added: number
  /** 本次任务新增的字节数 */
  added_bytes: number
  /** 下载已进行的秒数（仅在软进度阶段有值，收尾时为 undefined） */
  seconds?: number
}

/** 一个抖音收藏夹（后端 GET /api/scraper/f2-collects 的 folders 项） */
export interface F2CollectFolder {
  /** 收藏夹 ID（下载时按它指定「只下这个夹」） */
  id: string
  /** 收藏夹名（用户自己起的，如「过膝袜短袜JK」） */
  name: string
  /** 夹内作品数（下载进度就用它当分母） */
  total: number
}

/** 「先扫描」的结果：收藏夹清单（只读，不下载任何媒体） */
export interface F2CollectFolders {
  folders: F2CollectFolder[]
  /** 收藏夹总数 */
  total_folders: number
  /** 各夹作品数之和（含跨夹重复，仅供量级参考） */
  total_works: number
  /** Cookie 来源（f2 配置文件路径，出错时给用户看） */
  cookie_source: string
}

/** 进行中任务的简要信息（后端 /api/scraper/f2-status 的 auto.running） */
export interface F2RunningTask {
  id: number
  status: string
  progress: number
  /** 已完成计数：下载阶段是作者数，入库阶段是文件数（看 stage） */
  done: number
  total: number
  /** 阶段标记：download / scan / import / done（空串表示后端未标记） */
  stage: string
  /** 采集入口：post=博主主页作品；like=我的喜欢（决定阶段文案口径） */
  fetch_mode: string
  /** 「我的喜欢」下载期间的实时统计（无则 null） */
  like_progress: F2LikeProgress | null
}

/** 每日自动获取的配置与到期信息（后端 GET /api/scraper/f2-status 的 auto 字段） */
export interface F2AutoStatus {
  /** 是否开启每日自动增量入库 */
  enabled: boolean
  /** 自动获取走哪个入口：post=博主主页作品 / like=我的喜欢 / collection=我的收藏 */
  mode: string
  /** 最小间隔（小时）：距上次任务不足则跳过本轮 */
  interval_hours: number
  /** 自动获取是否跳过 live 实况分段 */
  skip_live: boolean
  /** 环境是否可用（f2 已装 + 作者库非空） */
  available: boolean
  /** 不可用原因或可用性摘要 */
  reason: string
  /** 「我的列表」模式（like/collection）是否可用（需先配「我的主页链接」） */
  like_available: boolean
  /** 「我的列表」模式不可用的原因 */
  like_reason: string
  /** 当前模式是 collection 时为 true：收藏模式必须手选收藏夹，调度器会跳过它 */
  collection_unsupported: boolean
  /** f2 用户库里的作者数 */
  authors: number
  /** 最近一次 f2 任务的创建时间（ISO） */
  last_task_at: string | null
  /** 下次到期时间（ISO，无历史任务时为空＝随时可触发） */
  next_due_at: string | null
  /** 进行中的 f2 任务 id（有则本轮不重复触发） */
  running_task_id: number | null
  /** 进行中任务的阶段与进度（无则 null），供卡片说明「现在在干什么」 */
  running: F2RunningTask | null
}

/** f2 可用性状态（后端 GET /api/scraper/f2-status 返回） */
export interface F2ImportStatus {
  /** 是否可用（f2 已安装 + 工作目录存在 + 作者库非空） */
  available: boolean
  /** 不可用原因或可用性摘要（直接展示给用户） */
  reason: string
  /** 已登记到博主库、可增量下载的作者数（不含 f2 里未登记的账号） */
  authors: number
  /** f2 用户库里有、但库里没有对应博主的账号名（默认会被跳过，供卡片提示） */
  unknown_authors: string[]
  /** f2 工作目录 */
  f2_dir: string
  /** 下载产物扫描目录 */
  root: string
  /** 「我的喜欢」（点赞）产物扫描目录 */
  like_root: string
  /** 「我的收藏」（抖音收藏列表）产物扫描目录 */
  collect_root: string
  /** 已保存的「我的主页链接」（点赞/收藏列表只有本人可见，采集时必须填自己） */
  like_user: string
  /** 「我的喜欢」是否可用（f2 + 工作目录 + 已配置主页链接；与博主白名单无关） */
  like_available: boolean
  /** 「我的喜欢」不可用原因 / 可用性摘要 */
  like_reason: string
  /** 「我的收藏」是否可用（前提与点赞一致：f2 + 工作目录 + 已配置主页链接） */
  collect_available: boolean
  /** 「我的收藏」不可用原因 / 可用性摘要 */
  collect_reason: string
  /** 「我的喜欢」每次最多翻多少条点赞（0=全量翻到底）。
   *  为什么需要：f2 的点赞分页没有「遇到已下载就停」，每页还固定等一次 timeout，
   *  全量时零新增也要空翻数分钟，且进度条会因「无新文件」停在 0；点赞列表最新在
   *  前，填 100~200 即可覆盖日常新增 */
  like_max_counts: number
  /** 后端配置的默认日期窗口天数（F2_FETCH_SINCE_DAYS）：作为「只翻最近 N 天」的初值，
   *  避免前端硬编码默认值把 .env 配置顶掉 */
  fetch_since_days: number
  /** 每日自动获取配置 */
  auto: F2AutoStatus
}

/** f2 博主清单的一行（后端 GET /api/scraper/f2-authors 返回） */
export interface F2AuthorRow {
  /** f2 用户库里的昵称（可能与库内博主名不同） */
  nickname: string
  /** sec_user_id（抖音主页 user/ 后面那段） */
  sec_user_id: string
  /** f2 记录的作品总数（她主页一共多少作品） */
  aweme_count: number
  /** 已入库素材数（按来源作者聚合，不含垃圾桶） */
  materials: number
  /** 命中库内博主时的博主 id / 名称；未登记账号为 null */
  blogger_id: number | null
  blogger_name: string | null
  /** 抖音主页链接（sec_user_id 缺失时为空串） */
  profile_url: string
}

/** f2 博主清单：下载白名单里到底有谁、各自已入库多少条素材 */
export interface F2AuthorsOverview {
  /** f2 用户库里是否有账号（空库时清单没意义） */
  available: boolean
  /** f2 工作目录 */
  f2_dir: string
  /** 下载白名单是否生效。库里一个抖音博主都没登记时为 false（此时不按作者过滤） */
  filter_active: boolean
  /** 白名单内的已登记博主（素材多的在前） */
  registered: F2AuthorRow[]
  /** f2 有、库里没有对应博主的账号（默认会被跳过） */
  unknown: F2AuthorRow[]
  registered_count: number
  unknown_count: number
  /** 一句话说明（库为空 / 白名单未生效 / 有未登记账号） */
  note: string
}

/** 提交选项（与后端 Query 参数一一对应） */
export interface F2ImportOptions {
  /** 是否先调 f2 增量下载（否则只入库已下载文件） */
  fetch: boolean
  /** 只处理这些作者（逗号分隔，归一化名） */
  authors?: string
  /** 最多导入多少个作品 */
  limit?: number
  /** 跳过 live 实况的分段视频 */
  skip_live?: boolean
  /** 下载阶段最多处理多少个作者（试跑用） */
  fetch_limit?: number
  /** 是否生成缩略图 */
  make_thumbnails?: boolean
  /** f2 日期窗口天数（0=全历史；缺省取后端配置，默认 14） */
  since_days?: number
  /** 是否连「未登记到博主库」的 f2 账号一起处理（缺省否：只处理已登记博主） */
  include_unknown_authors?: boolean
  /** 采集模式：post=博主主页作品（缺省）；like=我的喜欢（点赞，需 like_user） */
  mode?: 'post' | 'like' | 'collection'
  /** mode=like 时的「我的主页链接 / sec_user_id」（缺省用后端已保存的配置） */
  like_user?: string
  /** mode=like 时：入库后把未登记的来源作者补建成抖音博主并绑定素材（缺省开；
   *  补建的博主标记为「自动登记」，不算已登记博主、不进一键获取素材的下载白名单） */
  register_bloggers?: boolean
  /** mode=like 时最多翻多少条点赞（0=全量翻到底）。
   *  收窄的是 f2 的翻页量（`-o`）：点赞分页没有「遇到已下载就停」，全量每次都要空翻
   *  到底；点赞列表最新在前，填 100~200 可把日常增量降到一两页。代价是两次运行之间
   *  新增点赞超过该值会漏。注意 f2 的点赞模式**不读 `-i`**，日期窗口在此无效。 */
  like_max_counts?: number
  /** **按博主全量下载**：博主主页链接或 sec_user_id 列表。
   *  用途：给一个博主，下她**全部**作品。不要求该博主已在 f2 用户库里（f2 的下载
   *  目标只来自它自己的用户库，库里没有的账号跑不到，这是那个限制的出口）；
   *  首次采集自动用 `-i all` 翻全量，入库范围就是这些博主的产物。
   *  抖音号与 v.douyin.com 短链不支持（后端会明确报错）。 */
  profiles?: string[]
  /** mode=collection 时**只下这些收藏夹**（夹 ID，来自「先扫描」的结果）。
   *  非空时后端不再走平铺收藏列表，改为逐夹枚举作品后交给 f2 的下载器——
   *  没被选中的夹一件都不会下载，**也不会入库**。 */
  collect_ids?: string[]
  /** mode=collection 且没勾收藏夹时，是否允许导入**全部**收藏（含未勾选的夹）。
   *  默认 false：后端会直接拒绝（这条路径正是「我只想要 19 个夹，结果 31 个夹
   *  全进来了」的成因）。只有用户在「一键下载全部收藏」的确认弹窗上点了确认，
   *  这里才传 true。 */
  allow_all_collect?: boolean
}

export function useF2Import() {
  const status = ref<F2ImportStatus | null>(null)
  const statusLoading = ref(false)
  const submitting = ref(false)
  const autoSaving = ref(false)
  const likeUserSaving = ref(false)
  const likeMaxSaving = ref(false)
  /** 博主清单（展开清单时才拉取，不参与状态轮询） */
  const f2Authors = ref<F2AuthorsOverview | null>(null)
  const authorsLoading = ref(false)
  /** 「先扫描」结果：收藏夹清单（供勾选，默认全选） */
  const collectFolders = ref<F2CollectFolders | null>(null)
  const collectScanning = ref(false)

  /**
   * 读取可用性（卡片挂载、刷新按钮与「有任务在跑」时的轮询都调它）。
   *
   * @param options.silent 静默刷新：不改 statusLoading（轮询每 5 秒一次，改它会让
   *   状态行每 5 秒闪一次加载态；首次加载与手动刷新仍走非静默）。
   */
  async function loadStatus(options: { silent?: boolean } = {}): Promise<void> {
    if (!options.silent) statusLoading.value = true
    try {
      const { data } = await apiClient.get<F2ImportStatus>('/scraper/f2-status')
      status.value = data
    } catch (e) {
      status.value = null
      Message.warning(getApiErrorMessage(e, 'f2 状态读取失败'))
    } finally {
      statusLoading.value = false
    }
  }

  /** 读取 f2 博主清单（展开清单时调，也可手动刷新） */
  async function loadAuthors(): Promise<void> {
    authorsLoading.value = true
    try {
      const { data } = await apiClient.get<F2AuthorsOverview>('/scraper/f2-authors')
      f2Authors.value = data
    } catch (e) {
      f2Authors.value = null
      Message.warning(getApiErrorMessage(e, 'f2 博主清单读取失败'))
    } finally {
      authorsLoading.value = false
    }
  }

  /**
   * 「先扫描」：列出抖音收藏夹（夹名 / 夹 ID / 夹内作品数），只读、不下载任何媒体。
   *
   * 为什么要先扫描：平铺的「我的收藏」会把收藏夹里的作品一并下下来（实测 31 个夹
   * 约 2000 件，其中混着「股票 / 哲学 / 历史」这类明显不想要的 1 件夹），用户需要
   * 在下载前看到清单、勾掉不要的夹，再只下勾选的。
   *
   * @returns 扫描结果；失败（Cookie 失效 / 风控）返回 null 并提示原因。
   */
  async function scanCollects(): Promise<F2CollectFolders | null> {
    collectScanning.value = true
    try {
      const { data } = await apiClient.get<F2CollectFolders>('/scraper/f2-collects')
      collectFolders.value = data
      return data
    } catch (e) {
      collectFolders.value = null
      Message.error(getApiErrorMessage(e, '读取收藏夹失败'))
      return null
    } finally {
      collectScanning.value = false
    }
  }

  /**
   * 组装查询参数：**数组一律自己拼成逗号分隔的字符串**。
   *
   * 为什么不能直接把数组交给 axios：axios 1.x 默认把数组序列化成
   * `collect_ids[]=111&collect_ids[]=222`（键带方括号），而后端（FastAPI）按
   * `collect_ids` 取值 → **永远收到空**。真实后果：用户勾了 19 个收藏夹，后端当成
   * 「一个都没勾」——旧代码退回平铺收藏把整棵目录收进素材库（任务 #386/#387），
   * 加了硬前置之后又变成任务一开始就失败（#389）。显式拼串后这种"静默丢参数"
   * 不可能再发生（后端也同时兼容三种写法，见 routers/scraper.py 的 _split_multi）。
   */
  function toWireParams(options: F2ImportOptions): Record<string, unknown> {
    const { collect_ids, profiles, ...rest } = options
    return {
      ...rest,
      ...(collect_ids?.length ? { collect_ids: collect_ids.join(',') } : {}),
      ...(profiles?.length ? { profiles: profiles.join(',') } : {}),
    }
  }

  /** 提交一键获取素材任务，成功返回 task_id（失败返回 null） */
  async function submit(options: F2ImportOptions): Promise<number | null> {
    submitting.value = true
    // 三个入口共用同一个接口，提示文案按用途区分（点赞入口说「一键获取素材」会让人以为点错了）
    const label = options.profiles?.length
      ? '按博主全量下载'
      : options.collect_ids?.length
        ? `按收藏夹下载（${options.collect_ids.length} 个夹）`
        : options.mode === 'like'
          ? '采集我的喜欢'
          : '一键获取素材'
    try {
      const { data } = await apiClient.post<{
        task_id: number | null
        message: string
        /** 后端已有进行中的同类任务，直接复用它（并发保护） */
        reused?: boolean
      }>('/scraper/f2-import', null, { params: toWireParams(options) })
      if (!data.task_id) {
        Message.warning(data.message || '任务未创建')
        return null
      }
      if (data.reused) {
        Message.info(data.message || `已有进行中的「${label}」任务`)
        return data.task_id
      }
      Message.success(`已提交「${label}」任务，进度见任务中心`)
      return data.task_id
    } catch (e) {
      Message.error(getApiErrorMessage(e, '提交失败'))
      return null
    } finally {
      submitting.value = false
    }
  }

  /**
   * 设置每日自动获取（开关 / 间隔小时），成功返回 true。
   *
   * 后端会立即把配置写入 .env（persist=true），并回传最新的 auto 状态，
   * 因此这里直接用返回值覆盖本地状态，避免再多发一次 status 请求。
   */
  async function setAuto(
    enabled: boolean,
    intervalHours?: number,
    persist = true,
    mode?: 'post' | 'like' | 'collection',
  ): Promise<boolean> {
    autoSaving.value = true
    try {
      const { data } = await apiClient.put<{ message: string; auto: F2AutoStatus }>(
        '/scraper/f2-auto',
        null,
        {
          params: {
            enabled,
            interval_hours: intervalHours,
            persist,
            // 入口只在显式指定时下发（未指定＝沿用后端已保存的模式）
            ...(mode ? { mode } : {}),
          },
        },
      )
      if (status.value) status.value.auto = data.auto
      Message.success(data.message || '设置已保存')
      return true
    } catch (e) {
      Message.error(getApiErrorMessage(e, '自动获取设置失败'))
      // 失败后回读一次，避免界面停留在错误的开关状态
      await loadStatus()
      return false
    } finally {
      autoSaving.value = false
    }
  }

  /**
   * 保存「我的喜欢」用的主页链接（空串表示清除），成功返回 true。
   *
   * 点赞列表只有本人可见，f2 的 `-M like` 要求填自己的主页链接；后端会归一成
   * URL 并写入 .env（persist=true），之后手动/任务都直接用这个配置。
   */
  async function setLikeUser(likeUser: string, persist = true): Promise<boolean> {
    likeUserSaving.value = true
    try {
      const { data } = await apiClient.put<{ message: string; like_user: string }>(
        '/scraper/f2-like-user',
        null,
        { params: { like_user: likeUser, persist } },
      )
      if (status.value) {
        status.value.like_user = data.like_user
        // 链接变了，可用性随之变化：回读一次拿到 like_available / like_reason
        await loadStatus({ silent: true })
      }
      Message.success(data.message || '已保存')
      return true
    } catch (e) {
      Message.error(getApiErrorMessage(e, '保存「我的主页链接」失败'))
      return false
    } finally {
      likeUserSaving.value = false
    }
  }

  /**
   * 保存「我的喜欢」每次最多翻多少条点赞（0=全量），成功返回 true。
   *
   * 为什么需要收窄：f2 的点赞分页**没有「遇到已下载就停」**，从 cursor=0 一路翻到底，
   * 每页还固定 `asyncio.sleep(timeout)`（本机 10 秒）——点赞目录 919 个作品即 ≥46 页、
   * ≥7.7 分钟纯等待，每次运行都一样，哪怕零新增；同时进度条按「已落盘文件数」算，
   * 零新增时会全程停在 0，看起来像卡死。
   *
   * 点赞列表最新在前，所以只翻最近 N 条即可覆盖新增：后端把它转成 f2 的
   * `-o/--max-counts`（实测该参数确实 gate 住翻页循环）。
   * ⚠ f2 的点赞模式**不读 `-i`**（源码实测），所以日期窗口在这里无效——能收窄的只有它。
   */
  async function setLikeMaxCounts(maxCounts: number, persist = true): Promise<boolean> {
    likeMaxSaving.value = true
    try {
      const { data } = await apiClient.put<{ message: string; like_max_counts: number }>(
        '/scraper/f2-like-max-counts',
        null,
        { params: { max_counts: maxCounts, persist } },
      )
      if (status.value) status.value.like_max_counts = data.like_max_counts
      Message.success(data.message || '已保存')
      return true
    } catch (e) {
      Message.error(getApiErrorMessage(e, '保存「点赞翻页条数」失败'))
      return false
    } finally {
      likeMaxSaving.value = false
    }
  }

  return {
    status,
    statusLoading,
    submitting,
    autoSaving,
    likeUserSaving,
    likeMaxSaving,
    f2Authors,
    authorsLoading,
    collectFolders,
    collectScanning,
    loadStatus,
    loadAuthors,
    scanCollects,
    submit,
    setAuto,
    setLikeUser,
    setLikeMaxCounts,
  }
}
