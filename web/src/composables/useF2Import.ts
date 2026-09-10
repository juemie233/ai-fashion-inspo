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
}

export function useF2Import() {
  const status = ref<F2ImportStatus | null>(null)
  const statusLoading = ref(false)
  const submitting = ref(false)

  /** 读取可用性（卡片挂载与刷新按钮调用） */
  async function loadStatus(): Promise<void> {
    statusLoading.value = true
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

  return { status, statusLoading, submitting, loadStatus, submit }
}
