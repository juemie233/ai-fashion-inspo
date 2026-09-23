/** 人脸识别子服务可用性（模块级共享，多个入口只探一次）。
 *
 * 人脸相关入口（扫描页、人物详情页的人脸注册面板）都需要「事前」提示子服务
 * 离线，而不是等点了按钮才拿到 503。这里统一探测并共享结果，避免每个组件
 * 各发一个请求。
 *
 * 语义：
 * - ``status`` 为 null = 未知（尚未探测 / 探测请求本身失败）→ 不拦截操作，
 *   避免后端异常时把人脸功能整体锁死；
 * - ``offline`` 仅在明确探测到不可用时为 true。
 */

import { computed, ref } from 'vue'

import { fetchFaceServiceStatus, type FaceServiceStatus } from '@/api/faceScan'

const status = ref<FaceServiceStatus | null>(null)
/** 进行中的探测（并发调用复用同一个 Promise，避免重复请求） */
let inflight: Promise<void> | null = null

export function useFaceServiceStatus() {
  /** 拉取（或复用进行中的）子服务状态；失败保持「未知」态且不抛错 */
  async function load(): Promise<void> {
    if (!inflight) {
      inflight = fetchFaceServiceStatus()
        .then((s) => {
          status.value = s
        })
        .catch(() => {
          status.value = null
        })
        .finally(() => {
          inflight = null
        })
    }
    return inflight
  }

  /** 明确探测到不可用（未知态返回 false，不误伤） */
  const offline = computed(() => status.value !== null && !status.value.reachable)

  return { status, offline, load }
}
