/** 前后端 schema 握手 composable：启动时校验 /api/health 返回的 schema_version。 */

import { ref } from 'vue'
import apiClient from '@/api/client'
import { EXPECTED_SCHEMA_VERSION } from '@/utils/schemaVersion'

export function useSchemaCheck() {
  /** 是否正在校验 */
  const checking = ref(false)
  /** 是否版本不一致（true = 后端版本与前端期望不符） */
  const mismatch = ref(false)
  /** 后端实际返回的 schema_version（后端未返回时为 null） */
  const serverVersion = ref<string | null>(null)
  /** 校验失败原因（后端不可达等），null 表示无错误 */
  const error = ref<string | null>(null)

  async function check() {
    checking.value = true
    error.value = null
    try {
      const { data } = await apiClient.get<{ schema_version?: string }>('/health')
      serverVersion.value = data.schema_version ?? null
      // 前端期望版本为空（构建时未能从后端代码计算）时跳过校验，避免误报；
      // 否则后端未返回 schema_version（旧版本后端）也视为不一致，避免静默降级
      mismatch.value = !!EXPECTED_SCHEMA_VERSION && data.schema_version !== EXPECTED_SCHEMA_VERSION
    } catch {
      serverVersion.value = null
      mismatch.value = false
      error.value = '无法连接后端服务，未能校验版本一致性'
    } finally {
      checking.value = false
    }
  }

  return { checking, mismatch, serverVersion, error, check }
}
