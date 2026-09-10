/**
 * ui store 的「素材打开模式」偏好单测：默认值、切换、localStorage 持久化与非法值回退。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useUiStore } from '@/stores/ui'

const STORAGE_KEY = 'material-open-mode'

describe('ui store —— 素材打开模式', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('默认「新标签页打开」（无持久化记录时）', () => {
    const store = useUiStore()
    expect(store.materialOpenMode).toBe('new_tab')
  })

  it('读取持久化的「当前页打开」偏好', () => {
    localStorage.setItem(STORAGE_KEY, 'current_tab')
    const store = useUiStore()
    expect(store.materialOpenMode).toBe('current_tab')
  })

  it('非法持久化值回退默认「新标签页打开」', () => {
    localStorage.setItem(STORAGE_KEY, 'not-a-mode')
    const store = useUiStore()
    expect(store.materialOpenMode).toBe('new_tab')
  })

  it('切换模式写入 localStorage（跨会话保留）', async () => {
    const store = useUiStore()
    store.setMaterialOpenMode('current_tab')
    await Promise.resolve() // 等待 watch 副作用（flush: pre 默认在微任务）
    expect(localStorage.getItem(STORAGE_KEY)).toBe('current_tab')

    store.setMaterialOpenMode('new_tab')
    await Promise.resolve()
    expect(localStorage.getItem(STORAGE_KEY)).toBe('new_tab')
  })

  it('通过 store 直接赋值也持久化（模板 v-model 场景）', async () => {
    const store = useUiStore()
    store.materialOpenMode = 'current_tab'
    await Promise.resolve()
    expect(localStorage.getItem(STORAGE_KEY)).toBe('current_tab')
  })
})

describe('ui store —— 原有状态不受影响', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('侧边栏折叠与通知计数', () => {
    const store = useUiStore()
    expect(store.sidebarCollapsed).toBe(false)
    store.toggleSidebar()
    expect(store.sidebarCollapsed).toBe(true)
    store.setAnalyzingCount(3)
    expect(store.analyzingCount).toBe(3)
  })
})
