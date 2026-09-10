/** f2「一键获取素材」的状态与提交逻辑。
 *
 * 后端链路：`POST /api/scraper/f2-import` 创建后台任务（f2 增量下载 → 四层去重 →
 * 入库），由独立 worker 执行，进度在「任务中心」查看；本组合式只负责可用性检查
 * 与提交，不持有任务进度（避免与任务中心重复轮询）。
 */

import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import apiClient from '@/api/client'
import { getApiErrorMessage } from '@/utils/apiError'

/** 进行中任务的简要信息（后端 /api/scraper/f2-status 的 auto.running） */
export interface F2RunningTask {
  id: number
  status: string
  progress: number
  /** 已完成计数：下载阶段是作者数，入库阶段是文件数（看 stage） */
  done: number
  total: number
  /** 阶段标记：download / import / done（空串表示后端未标记） */
  stage: string
}

/** 每日自动获取的配置与到期信息（后端 GET /api/scraper/f2-status 的 auto 字段） */
export interface F2AutoStatus {
  /** 是否开启每日自动增量入库 */
  enabled: boolean
  /** 最小间隔（小时）：距上次任务不足则跳过本轮 */
  interval_hours: number
  /** 自动获取是否跳过 live 实况分段 */
  skip_live: boolean
  /** 环境是否可用（f2 已装 + 作者库非空） */
  available: boolean
  /** 不可用原因或可用性摘要 */
  reason: string
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
  /** f2 用户库里的作者数 */
  authors: number
  /** f2 工作目录 */
  f2_dir: string
  /** 下载产物扫描目录 */
  root: string
  /** 每日自动获取配置 */
  auto: F2AutoStatus
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
}

export function useF2Import() {
  const status = ref<F2ImportStatus | null>(null)
  const statusLoading = ref(false)
  const submitting = ref(false)
  const autoSaving = ref(false)

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

  /** 提交一键获取素材任务，成功返回 task_id（失败返回 null） */
  async function submit(options: F2ImportOptions): Promise<number | null> {
    submitting.value = true
    try {
      const { data } = await apiClient.post<{
        task_id: number | null
        message: string
        /** 后端已有进行中的同类任务，直接复用它（并发保护） */
        reused?: boolean
      }>('/scraper/f2-import', null, { params: options })
      if (!data.task_id) {
        Message.warning(data.message || '任务未创建')
        return null
      }
      if (data.reused) {
        Message.info(data.message || '已有进行中的「一键获取素材」任务')
        return data.task_id
      }
      Message.success('已提交「一键获取素材」任务，进度见任务中心')
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
  ): Promise<boolean> {
    autoSaving.value = true
    try {
      const { data } = await apiClient.put<{ message: string; auto: F2AutoStatus }>(
        '/scraper/f2-auto',
        null,
        { params: { enabled, interval_hours: intervalHours, persist } },
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

  return { status, statusLoading, submitting, autoSaving, loadStatus, submit, setAuto }
}
