/**
 * 「下载博主全部作品」的编排：没主页链接时先自动解析抖音主页，再提交 f2 任务。
 *
 * 为什么单独成 composable（而不是写在详情页里）：
 * 1. 视图只做编排、不自己弹提示——本模块内部自行 `Message.xxx()`（与 useF2Import 等
 *    既有 composable 同口径）；
 * 2. 解析 + 下载是**两步且第二步依赖第一步**：解析失败必须给出后端原因并中止，
 *    这部分值得单测（视图用 Arco 卡片 + 瀑布流，测试环境挂不出来）。
 *
 * 背景：抖音「我的喜欢 / 我的收藏」采回来的素材只带作者昵称（f2 把原作者写进文件名，
 * 目录名却是「我」），补建出的博主没有主页链接 → 按钮原来只能禁用，用户必须去抖音复制
 * 主页链接再手工填。后端现在能用素材里的**真实作品 ID** 反查作者主页，所以这里把
 * 「解析 → 下载」串成一次点击（见 `bloggersApi.resolveDouyinProfile`）。
 */

import { computed, ref } from 'vue'
import { Message } from '@arco-design/web-vue'

import { bloggersApi } from '@/api/persons'
import { useF2Import } from '@/composables/useF2Import'
import {
  canResolveProfile,
  downloadWorksDisabledReason,
  worksDownloadOptions,
  type WorksDownloadSubject,
} from '@/utils/bloggerWorks'

/** 参与判定的博主字段（人物详情的子集） */
export interface WorksDownloadSubjectWithId extends WorksDownloadSubject {
  id: number
}

/** 「下载所有作品」按钮状态 */
export interface WorksDownloadState {
  disabled: boolean
  /** 不可用原因（hover 提示；可用时为空串） */
  reason: string
  /** 可点，但会先自动解析抖音主页（按钮文案与确认提示据此变化） */
  needsResolve: boolean
}

/**
 * 组装「下载所有作品」按钮的状态与动作。
 *
 * @param getPerson 取当前人物（模特页 / 加载中返回 null，此时按钮禁用且无提示）
 * @param onProfileResolved 解析成功后回调（详情页据此重拉详情，让主页链接显示出来）
 */
export function useWorksDownload(
  getPerson: () => WorksDownloadSubjectWithId | null | undefined,
  onProfileResolved?: () => void | Promise<void>,
) {
  const { status, loadStatus, submit } = useF2Import()

  /** 正在解析抖音主页（联网反查，20~40 秒；按钮转圈防重复点击） */
  const resolving = ref(false)

  /**
   * 按钮状态。原因优先级刻意如此：
   * 1. 非抖音 → 「仅支持抖音」（平台本身不支持，与 f2 状态无关）；
   * 2. f2 通道**明确不可用** → 报 f2 的原因：解析与下载都走 f2，若先报「补主页链接」，
   *    用户补完链接还是点不动，白跑一趟；
   * 3. 缺链接且不能解析 → 报「先补主页链接」；
   * 4. 其余 → 可用。
   *
   * f2 状态为 `undefined`（还没回来 / 后端版本较旧）时**先放行**：能点、由后端给出准确
   * 理由，好过永久禁用只留一句提示。
   */
  const state = computed<WorksDownloadState>(() => {
    const person = getPerson()
    if (!person) return { disabled: true, reason: '', needsResolve: false }

    if (person.platform !== 'douyin') {
      return {
        disabled: true,
        reason: downloadWorksDisabledReason(person),
        needsResolve: false,
      }
    }

    if (status.value?.profiles_available === false) {
      return {
        disabled: true,
        reason: status.value.profiles_reason || 'f2 通道未就绪：作品下载依赖 f2（抖音专用通道）',
        needsResolve: false,
      }
    }

    const canResolve = canResolveProfile(person, undefined)
    const reason = downloadWorksDisabledReason(person, canResolve)
    if (reason) return { disabled: true, reason, needsResolve: false }
    return { disabled: false, reason: '', needsResolve: canResolve }
  })

  /** 点击「下载所有作品」：缺主页链接就先解析，解析成功（或本来就有）再提交下载 */
  async function downloadAllWorks(): Promise<void> {
    const person = getPerson()
    if (!person) return

    let profileUrl = (person.profile_url ?? '').trim()
    if (!profileUrl) {
      resolving.value = true
      try {
        const result = await bloggersApi.resolveDouyinProfile(person.id)
        profileUrl = (result.profile_url ?? '').trim()
        if (!result.ok || !profileUrl) {
          Message.warning(
            result.reason || '自动解析抖音主页失败：请在「编辑」里手工填写主页链接后重试',
          )
          return
        }
        Message.success('已解析到抖音主页，开始下载全部作品')
        await onProfileResolved?.()
      } catch (e) {
        Message.error(e instanceof Error ? e.message : '自动解析抖音主页失败')
        return
      } finally {
        resolving.value = false
      }
    }

    await submit(worksDownloadOptions(profileUrl))
  }

  return { loadStatus, resolving, state, downloadAllWorks }
}
