/**
 * 博主详情页「下载所有作品」的判据与提交参数（纯函数，便于单测）。
 *
 * 为什么单独成文件：这个按钮的可用性同时取决于「人物够不够格」（抖音 + 主页链接）
 * 与「f2 通道是否就绪」，判据混在组件里既没法测（该页用 Arco 卡片 + 瀑布流，测试
 * 环境挂不出来），也容易与后端 `/api/scraper/f2-import` 的 profiles 口径漂移。
 *
 * 后端口径（routers/scraper.py 的 profiles 参数）：传博主主页链接或 sec_user_id，
 * **不要求该博主先在 f2 用户库里**（f2 的下载目标只来自它自己的用户库，库里没有的
 * 账号跑不到，profiles 正是那个限制的出口）；首次采集自动用 `-i all` 翻全量，
 * 之后按日期窗口增量。
 */

import type { F2ImportOptions } from '@/composables/useF2Import'

/** 参与判定的最小人物字段（PersonDetail 的子集，便于直接把 detail 传进来） */
export interface WorksDownloadSubject {
  platform?: string | null
  profile_url?: string | null
}

/**
 * 能否「下载所有作品」：必须是**抖音博主**且填了主页链接。
 *
 * - 限定抖音：作品下载走 f2（抖音专用通道），小红书博主没有对应能力；
 * - 必须有主页链接：f2 靠主页链接 / sec_user_id 定位账号，没有它无从下起。
 */
export function canDownloadWorks(subject: WorksDownloadSubject): boolean {
  return subject.platform === 'douyin' && Boolean((subject.profile_url ?? '').trim())
}

/**
 * 能否「先自动解析抖音主页、再下载」：抖音博主 + 还没有主页链接 + f2 通道可用。
 *
 * 由来：抖音「我的喜欢 / 我的收藏」采回来的素材只带**作者昵称**（f2 把原作者写进文件名，
 * 目录名却是「我」），补建出的博主没有主页链接 → 按钮原来只能禁用，用户得去抖音复制一次
 * 主页链接再手工填。后端 `POST /api/bloggers/{id}/resolve-douyin-profile` 能用素材里的
 * **真实作品 ID** 反查出作者主页（细节见 backend 的 blogger_enrichment_service），所以
 * 「缺链接」不再是死路，前端据此把这个按钮从「禁用」改成「先解析再下载」。
 *
 * `profilesAvailable` 传 `undefined`（f2 状态还没回来 / 后端版本较旧）时**先放行**：与
 * 按钮可用性一贯的宽严口径一致——能点、让后端给出准确理由，好过永久禁用只留一句提示。
 */
export function canResolveProfile(
  subject: WorksDownloadSubject,
  profilesAvailable?: boolean,
): boolean {
  if (subject.platform !== 'douyin') return false
  if ((subject.profile_url ?? '').trim()) return false
  return profilesAvailable !== false
}

/** 按钮不可用的原因（可用时返回空串），用于 hover 提示。
 *
 * `canResolve` 为 true 表示「没链接但能自动解析」——此时按钮可用，原因留空
 * （由确认弹窗说明会先解析主页）。
 */
export function downloadWorksDisabledReason(
  subject: WorksDownloadSubject,
  canResolve = false,
): string {
  if (subject.platform !== 'douyin') {
    return '仅支持抖音博主：作品下载走 f2 的抖音通道'
  }
  if (!(subject.profile_url ?? '').trim()) {
    if (canResolve) return ''
    return '该博主还没有主页链接：先点「编辑」补上抖音主页链接，才能定位到她的作品'
  }
  return ''
}

/**
 * 「下载所有作品」的提交参数。
 *
 * **不带 `since_days`**：首次点名该博主时后端会自动用 `-i all` 翻全量（见 profiles
 * 口径），之后按日期窗口增量——既满足「下全部作品」，又不必每次点击都翻全量
 * （f2 每页固定等一次 timeout，全量翻页很慢）。
 */
export function worksDownloadOptions(profileUrl: string): F2ImportOptions {
  return {
    fetch: true,
    mode: 'post',
    profiles: [profileUrl.trim()],
    make_thumbnails: true,
  }
}
