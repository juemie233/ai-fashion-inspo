/** 出图配方（localStorage）：保存/覆盖/上限/删除/空名拒绝。 */

import { beforeEach, describe, expect, it } from 'vitest'
import { useGenerationPresets, type GenerationPreset } from '../useGenerationPresets'

function preset(name: string, over: Partial<GenerationPreset> = {}): GenerationPreset {
  return {
    name,
    prompt: '米色针织开衫',
    negative: '',
    size: '1024x1024',
    steps: 8,
    count: 2,
    kind: 't2i',
    ...over,
  }
}

describe('useGenerationPresets', () => {
  beforeEach(() => localStorage.clear())

  it('保存后能读出，同名覆盖且排在最近使用第一位', () => {
    const { presets, save } = useGenerationPresets()
    save(preset('工作室'))
    save(preset('街拍', { steps: 12 }))
    expect(presets.value.map((p) => p.name)).toEqual(['街拍', '工作室'])

    save(preset('工作室', { steps: 20 }))
    expect(presets.value.map((p) => p.name)).toEqual(['工作室', '街拍'])
    expect(presets.value[0].steps).toBe(20)
  })

  it('空名拒绝保存', () => {
    const { presets, save } = useGenerationPresets()
    expect(save(preset('   '))).toBe(false)
    expect(presets.value).toHaveLength(0)
  })

  it('超过上限时只保留最近的 20 条', () => {
    const { presets, save, MAX_PRESETS } = useGenerationPresets()
    for (let i = 0; i < MAX_PRESETS + 5; i++) save(preset(`p${i}`))
    expect(presets.value).toHaveLength(MAX_PRESETS)
    expect(presets.value[0].name).toBe(`p${MAX_PRESETS + 4}`) // 最新的在前
  })

  it('删除与按名查找', () => {
    const { presets, save, remove, find } = useGenerationPresets()
    save(preset('A'))
    expect(find('A')?.name).toBe('A')
    remove('A')
    expect(presets.value).toHaveLength(0)
    expect(find('A')).toBeUndefined()
  })

  it('持久化到 localStorage（重新初始化仍能读到）', () => {
    useGenerationPresets().save(preset('持久化'))
    const again = useGenerationPresets()
    expect(again.presets.value[0].name).toBe('持久化')
  })

  it('历史脏数据不抛错（解析失败退化为空数组）', () => {
    localStorage.setItem('generate-presets', '{not json')
    const { presets } = useGenerationPresets()
    expect(presets.value).toEqual([])
  })
})
