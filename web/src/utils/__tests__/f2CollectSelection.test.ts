/**
 * 「收藏夹选择」本地记忆单测（只保留最近一次）。
 *
 * 关注三件事：**只留一条**（新的覆盖旧的，不累积历史）、**脏数据不卡流程**
 * （解析失败/形状不对＝没有记录）、**只恢复仍然存在的夹**（夹被删后不能凭空勾）。
 */

import { beforeEach, describe, expect, it } from 'vitest'
import {
  loadFolderSelection,
  restoreFolderSelection,
  saveFolderSelection,
} from '../f2CollectSelection'

const KEY = 'f2-collect-folders'

describe('f2CollectSelection', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('没有记录时返回 null（界面按默认全选处理）', () => {
    expect(loadFolderSelection()).toBeNull()
  })

  it('保存后能读回来，且带上保存时间', () => {
    const now = new Date('2026-09-22T22:00:00Z')
    saveFolderSelection(['111', '333'], now)

    const saved = loadFolderSelection()
    expect(saved?.ids).toEqual(['111', '333'])
    expect(saved?.savedAt).toBe('2026-09-22T22:00:00.000Z')
  })

  it('只保留最近一次：再存一次就覆盖，不累积历史', () => {
    saveFolderSelection(['111', '222'])
    saveFolderSelection(['333'])

    expect(loadFolderSelection()?.ids).toEqual(['333'])
    expect(JSON.parse(localStorage.getItem(KEY) as string).ids).toEqual(['333'])
  })

  it('保存时去重并丢弃空值', () => {
    saveFolderSelection(['111', '111', '', '222'])

    expect(loadFolderSelection()?.ids).toEqual(['111', '222'])
  })

  it('脏数据/形状不对一律当作没有记录', () => {
    localStorage.setItem(KEY, '{不是 JSON')
    expect(loadFolderSelection()).toBeNull()

    localStorage.setItem(KEY, JSON.stringify({ ids: 'not-an-array' }))
    expect(loadFolderSelection()).toBeNull()

    localStorage.setItem(KEY, JSON.stringify({ ids: [] }))
    expect(loadFolderSelection()).toBeNull()
  })

  it('恢复时只勾仍然存在的夹，并报告失效数量', () => {
    const saved = { ids: ['111', '222', '999'], savedAt: '' }

    const restored = restoreFolderSelection(saved, ['111', '222', '333'])

    expect(restored.ids).toEqual(['111', '222'])
    expect(restored.fromSaved).toBe(true)
    expect(restored.staleCount).toBe(1)
  })

  it('上次勾的夹一个都不剩时回退成默认全选', () => {
    const restored = restoreFolderSelection({ ids: ['999'], savedAt: '' }, ['111', '222'])

    expect(restored.ids).toEqual(['111', '222'])
    expect(restored.fromSaved).toBe(false)
    expect(restored.staleCount).toBe(1)
  })

  it('没有记录时就是全选（fromSaved=false）', () => {
    const restored = restoreFolderSelection(null, ['111', '222'])

    expect(restored.ids).toEqual(['111', '222'])
    expect(restored.fromSaved).toBe(false)
    expect(restored.staleCount).toBe(0)
  })

  it('localStorage 不可用时不抛异常（隐私模式）', () => {
    const original = Object.getOwnPropertyDescriptor(window, 'localStorage')
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new Error('SecurityError')
      },
    })
    try {
      expect(loadFolderSelection()).toBeNull()
      expect(() => saveFolderSelection(['111'])).not.toThrow()
    } finally {
      if (original) Object.defineProperty(window, 'localStorage', original)
    }
  })
})
