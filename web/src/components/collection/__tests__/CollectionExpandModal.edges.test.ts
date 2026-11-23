/**
 * 合集「按此扩充」弹窗（`CollectionExpandModal`）边界补测。
 *
 * 既有 `CollectionExpandModal.test.ts` 覆盖打开即生成、全选加入、无候选、全已存在、生成失败重试；
 * 本文件补：
 * - 部分勾选只提交选中项；
 * - 部分已在合集内时的提示文案（跳过数）；
 * - 提交中重复点击不会重复写库（防重复提交）；
 * - 扩充请求失败时不 emit applied、不关弹窗；
 * - 相似度/开关变化走 300ms 防抖，且「重新生成」会取消防抖只发一次；
 * - 无候选且向量不可用时的匹配方式提示；切换合集重新生成并丢弃旧响应。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  suggest: vi.fn(),
  expand: vi.fn(),
  convert: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

vi.mock('@/api/collections', () => ({
  suggestCollectionItems: mocks.suggest,
  expandCollection: mocks.expand,
  convertCollectionToSemi: mocks.convert,
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import CollectionExpandModal from '../CollectionExpandModal.vue'
import type { CollectionOut } from '@/api/collections'

const COLLECTION: CollectionOut = {
  id: 5,
  name: '通勤穿搭',
  description: null,
  kind: 'manual',
  position: 0,
  parent_id: null,
  auto_source: null,
  child_count: 0,
  cover_inspiration_id: null,
  cover_thumbnail_path: null,
  item_count: 3,
  query_json: null,
  created_at: '',
  updated_at: '',
}

function inspiration(id: string) {
  return {
    id,
    file_path: `images/${id}.jpg`,
    thumbnail_path: `thumbnails/${id}.jpg`,
    media_type: 'image',
    is_favorite: false,
    created_at: '2026-11-01T00:00:00Z',
    tags: [],
  }
}

function suggestItem(id: string, over: Record<string, unknown> = {}) {
  return {
    inspiration: inspiration(id),
    similarity: 0.88,
    shared_tags: ['通勤'],
    outfit_tags: ['通勤'],
    match_source: 'hybrid',
    reason: '视觉相似 86% · 同大标签：通勤',
    ...over,
  }
}

function suggestResult(items = [suggestItem('i1', { similarity: 0.88 })]) {
  return {
    items,
    total: items.length,
    profile: {
      top_tags: ['通勤'],
      outfit_tags: ['通勤'],
      seeds_used: 3,
      vector_available: true,
    },
  }
}

function mountModal(collection: CollectionOut = COLLECTION) {
  return mount(CollectionExpandModal, {
    props: { visible: true, collection },
    global: { plugins: [ArcoVue] },
    attachTo: document.body,
  })
}

type ModalWrapper = ReturnType<typeof mountModal>

function bodyButtons(): HTMLButtonElement[] {
  return [...document.body.querySelectorAll('button')] as HTMLButtonElement[]
}

function clickBodyButton(text: string): void {
  const button = bodyButtons().find((b) => b.textContent?.includes(text))
  expect(button, `按钮「${text}」应存在`).toBeTruthy()
  button!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

interface ModalVm {
  toggleSelect: (id: string) => void
  toggleSelectAll: () => void
  minSimilarity: number
  outfitOnly: boolean
  excludeOtherAuto: boolean
  scheduleGenerate: () => void
  runNow: () => void
}

function vmOf(wrapper: ModalWrapper): ModalVm {
  return wrapper.vm as unknown as ModalVm
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

beforeEach(() => {
  vi.clearAllMocks()
  document.body.innerHTML = ''
  mocks.suggest.mockResolvedValue(suggestResult())
  mocks.expand.mockResolvedValue({
    batch_id: 'batch-1',
    added: 1,
    skipped: 0,
    not_found: 0,
    added_ids: ['i1'],
  })
  mocks.convert.mockResolvedValue({ ...COLLECTION, kind: 'smart', semi_manual: true })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('CollectionExpandModal 边界', () => {
  it('部分勾选：只提交选中的 id，提示条通过 applied 带出', async () => {
    mocks.suggest.mockResolvedValue(suggestResult([suggestItem('i1'), suggestItem('i2')]))
    mocks.expand.mockResolvedValue({
      batch_id: 'batch-2',
      added: 1,
      skipped: 0,
      not_found: 0,
      added_ids: ['i2'],
    })
    const wrapper = mountModal()
    await flushPromises()

    vmOf(wrapper).toggleSelect('i2')
    await flushPromises()
    clickBodyButton('加入合集')
    await flushPromises()

    expect(mocks.expand).toHaveBeenCalledWith(5, ['i2'])
    expect(wrapper.emitted('applied')?.[0]).toEqual([
      { collectionId: 5, batch_id: 'batch-2', added: 1 },
    ])
    expect(wrapper.emitted('update:visible')?.[0]).toEqual([false])
  })

  it('纯智能合集：提示需转半智能；点「转为半智能并加入」先转换再扩充', async () => {
    const smart = { ...COLLECTION, kind: 'smart' as const }
    const wrapper = mountModal(smart)
    await flushPromises()

    expect(document.body.textContent).toContain('点「加入」会先转为半智能')
    expect(bodyButtons().some((b) => b.textContent?.includes('转为半智能并加入'))).toBe(true)

    vmOf(wrapper).toggleSelect('i1')
    await flushPromises()
    clickBodyButton('转为半智能并加入')
    await flushPromises()

    expect(mocks.convert).toHaveBeenCalledWith(5)
    expect(mocks.expand).toHaveBeenCalledWith(5, ['i1'])
    // 顺序：先转半智能，再写入成员（否则后端会 400）
    expect(mocks.convert.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.expand.mock.invocationCallOrder[0],
    )
    expect(wrapper.emitted('applied')?.[0]).toEqual([
      { collectionId: 5, batch_id: 'batch-1', added: 1 },
    ])
  })

  it('转半智能失败：提示 detail，且不再提交扩充（避免半截写入）', async () => {
    mocks.convert.mockRejectedValue({ response: { data: { detail: '合集已被别处修改' } } })
    const wrapper = mountModal({ ...COLLECTION, kind: 'smart' })
    await flushPromises()

    vmOf(wrapper).toggleSelect('i1')
    await flushPromises()
    clickBodyButton('转为半智能并加入')
    await flushPromises()

    expect(mocks.message.error).toHaveBeenCalledWith('合集已被别处修改')
    expect(mocks.expand).not.toHaveBeenCalled()
    expect(wrapper.emitted('applied')).toBeUndefined()
  })

  it('部分已在合集内：成功提示带跳过数', async () => {
    mocks.suggest.mockResolvedValue(suggestResult([suggestItem('i1'), suggestItem('i2')]))
    mocks.expand.mockResolvedValue({
      batch_id: 'batch-3',
      added: 1,
      skipped: 1,
      not_found: 0,
      added_ids: ['i1'],
    })
    const wrapper = mountModal()
    await flushPromises()

    vmOf(wrapper).toggleSelectAll()
    await flushPromises()
    clickBodyButton('加入合集')
    await flushPromises()

    expect(mocks.message.success).toHaveBeenCalledWith('已扩充 1 个素材（1 个已在合集内）')
  })

  it('提交中重复点击不会重复写库', async () => {
    const pending = deferred<{
      batch_id: string
      added: number
      skipped: number
      not_found: number
      added_ids: string[]
    }>()
    mocks.expand.mockReturnValue(pending.promise)
    const wrapper = mountModal()
    await flushPromises()

    vmOf(wrapper).toggleSelect('i1')
    await flushPromises()
    clickBodyButton('加入合集')
    clickBodyButton('加入合集')
    await flushPromises()

    expect(mocks.expand).toHaveBeenCalledTimes(1)

    pending.resolve({
      batch_id: 'batch-4',
      added: 1,
      skipped: 0,
      not_found: 0,
      added_ids: ['i1'],
    })
    await flushPromises()
    expect(wrapper.emitted('applied')).toHaveLength(1)
  })

  it('扩充请求失败：提示 detail、不 emit applied、不关弹窗', async () => {
    mocks.expand.mockRejectedValue({ response: { data: { detail: '合集不存在' } } })
    const wrapper = mountModal()
    await flushPromises()

    vmOf(wrapper).toggleSelect('i1')
    await flushPromises()
    clickBodyButton('加入合集')
    await flushPromises()

    expect(mocks.message.error).toHaveBeenCalledWith('合集不存在')
    expect(wrapper.emitted('applied')).toBeUndefined()
    expect(wrapper.emitted('update:visible')).toBeUndefined()
  })

  it('无候选且向量不可用：提示匹配方式为「仅标签（向量不可用）」', async () => {
    mocks.suggest.mockResolvedValue({
      items: [],
      total: 0,
      profile: { top_tags: [], outfit_tags: [], seeds_used: 0, vector_available: false },
    })
    mountModal()
    await flushPromises()

    const text = document.body.textContent ?? ''
    expect(text).toContain('仅标签（向量不可用）')
    expect(text).toContain('没有找到合适的候选')
  })

  it('相似度/开关变化走 300ms 防抖，合并成一次请求', async () => {
    const wrapper = mountModal()
    await flushPromises()
    expect(mocks.suggest).toHaveBeenCalledTimes(1)
    vi.useFakeTimers()

    const vm = vmOf(wrapper)
    vm.minSimilarity = 0.8
    vm.outfitOnly = true
    vm.excludeOtherAuto = false
    vm.scheduleGenerate()
    await vi.advanceTimersByTimeAsync(299)
    expect(mocks.suggest).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1)
    expect(mocks.suggest).toHaveBeenCalledTimes(2)
    expect(mocks.suggest).toHaveBeenLastCalledWith(5, {
      limit: 40,
      min_similarity: 0.8,
      outfit_only: true,
      exclude_other_auto: false,
    })
  })

  it('「重新生成」会取消尚未触发的防抖，只发一次请求', async () => {
    const wrapper = mountModal()
    await flushPromises()
    expect(mocks.suggest).toHaveBeenCalledTimes(1)
    vi.useFakeTimers()

    const vm = vmOf(wrapper)
    vm.minSimilarity = 0.7
    vm.scheduleGenerate()
    vm.runNow()
    await vi.advanceTimersByTimeAsync(0)
    expect(mocks.suggest).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(600)
    expect(mocks.suggest).toHaveBeenCalledTimes(2)
  })

  it('切换合集重新生成：筛选回到默认，旧合集的在途响应被丢弃', async () => {
    const old = deferred<ReturnType<typeof suggestResult>>()
    mocks.suggest.mockReturnValueOnce(old.promise)
    const wrapper = mountModal()
    await flushPromises()
    expect(mocks.suggest).toHaveBeenCalledTimes(1)

    mocks.suggest.mockResolvedValueOnce(suggestResult([suggestItem('new')]))
    await wrapper.setProps({ collection: { ...COLLECTION, id: 6 } })
    await flushPromises()

    expect(mocks.suggest).toHaveBeenCalledTimes(2)
    expect(mocks.suggest).toHaveBeenLastCalledWith(6, {
      limit: 40,
      min_similarity: 0.5,
      outfit_only: false,
      exclude_other_auto: true,
    })

    old.resolve(suggestResult([suggestItem('stale')]))
    await flushPromises()
    expect(document.body.textContent).toContain('88%')
  })

  it('关闭弹窗：取消按钮 emit update:visible=false 并清空结果', async () => {
    const wrapper = mountModal()
    await flushPromises()

    clickBodyButton('取消')
    await flushPromises()

    expect(wrapper.emitted('update:visible')?.[0]).toEqual([false])
  })
})
