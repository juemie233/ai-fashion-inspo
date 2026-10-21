/** 出图参数预设（「我的出图配方」）：存在 localStorage，不占数据库。
 *
 * 为什么先用 localStorage：配方是**个人设备的偏好**（尺寸/步数/常用描述），
 * 不是需要跨端同步的业务数据；存本地能立刻上线，且不引入新表与迁移。
 * 若将来要跨设备共享，再迁到 `generation_presets` 表（接口形状保持不变）。
 */

import { ref } from 'vue'
import { safeJsonParse } from '@/utils/format'

const STORAGE_KEY = 'generate-presets'
/** 上限：防止 localStorage 被写爆（5MB 常见上限） */
const MAX_PRESETS = 20

export interface GenerationPreset {
  name: string
  prompt: string
  negative: string
  size: string
  steps: number
  count: number
  kind: 't2i' | 'i2i'
}

export function useGenerationPresets() {
  const presets = ref<GenerationPreset[]>(
    safeJsonParse<GenerationPreset[]>(localStorage.getItem(STORAGE_KEY), []),
  )

  function persist(): void {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(presets.value.slice(0, MAX_PRESETS)))
  }

  /** 保存配方（同名覆盖，保持最近使用在前） */
  function save(preset: GenerationPreset): boolean {
    const name = preset.name.trim()
    if (!name) return false
    const rest = presets.value.filter((p) => p.name !== name)
    presets.value = [{ ...preset, name }, ...rest].slice(0, MAX_PRESETS)
    persist()
    return true
  }

  function remove(name: string): void {
    presets.value = presets.value.filter((p) => p.name !== name)
    persist()
  }

  function find(name: string): GenerationPreset | undefined {
    return presets.value.find((p) => p.name === name)
  }

  return { presets, save, remove, find, MAX_PRESETS }
}
