/**
 * 合集「按此扩充」弹窗测试。
 *
 * 关键约束：
 * - 打开即生成候选（只读接口），不自动写库；
 * - 勾选后才「加入合集」，成功后 emit applied 供父级显示撤销；
 * - 无候选时给出可操作的提示。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
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

const SUGGEST_RESULT = {
  items: [
    {
      inspiration: inspiration('i1'),
      similarity: 0.88,
      shared_tags: ['通勤', '衬衫'],
      outfit_tags: ['通勤'],
      match_source: 'hybrid',
      reason: '视觉相似 86% · 同大标签：通勤',
    },
    {
      inspiration: inspiration('i2'),
      similarity: 0.71,
      shared_tags: ['通勤'],
      outfit_tags: ['通勤'],
      match_source: 'tag',
      reason: '共同标签：通勤',
    },
  ],
  total: 2,
  profile: {
    top_tags: ['通勤', '衬衫'],
    outfit_tags: ['通勤'],
    seeds_used: 3,
    vector_available: true,
  },
}

function mountModal(collection: CollectionOut = COLLECTION) {
  return mount(CollectionExpandModal, {
    props: { visible: true, collection },
    global: { plugins: [ArcoVue] },
    attachTo: document.body,
  })
}

function bodyButtons(): HTMLButtonElement[] {
  return [...document.body.querySelectorAll('button')] as HTMLButtonElement[]
}

function clickBodyButton(text: string): void {
  const button = bodyButtons().find((b) => b.textContent?.includes(text))
  expect(button, `按钮「${text}」应存在`).toBeTruthy()
  button!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

beforeEach(() => {
  vi.clearAllMocks()
  document.body.innerHTML = ''
  mocks.suggest.mockResolvedValue(SUGGEST_RESULT)
  mocks.convert.mockResolvedValue({ ...COLLECTION, semi_manual: true })
  mocks.expand.mockResolvedValue({
    batch_id: 'batch-1',
    added: 2,
    skipped: 0,
    not_found: 0,
    added_ids: ['i1', 'i2'],
  })
})

describe('CollectionExpandModal', () => {
  it('打开即生成候选：按默认条件调用只读接口并展示相似度角标', async () => {
    mountModal()
    await flushPromises()

    expect(mocks.suggest).toHaveBeenCalledWith(5, {
      limit: 40,
      min_similarity: 0.5,
      outfit_only: false,
      exclude_other_auto: true,
    })
    const text = document.body.textContent ?? ''
    expect(text).toContain('88%')
    expect(text).toContain('种子 3 条')
    expect(text).toContain('视觉 + 标签')
  })

  it('全选后加入合集：按批次写库并通知父级', async () => {
    const wrapper = mountModal()
    await flushPromises()

    clickBodyButton('全选本页')
    await flushPromises()
    clickBodyButton('加入合集')
    await flushPromises()

    expect(mocks.expand).toHaveBeenCalledWith(5, ['i1', 'i2'])
    expect(wrapper.emitted('applied')?.[0]).toEqual([
      { collectionId: 5, batch_id: 'batch-1', added: 2 },
    ])
    expect(mocks.message.success).toHaveBeenCalled()
  })

  it('没有候选时给出可操作提示', async () => {
    mocks.suggest.mockResolvedValue({
      items: [],
      total: 0,
      profile: { top_tags: [], outfit_tags: [], seeds_used: 0, vector_available: false },
    })
    mountModal()
    await flushPromises()

    expect(document.body.textContent).toContain('没有找到合适的候选')
    // 没有可加入项时按钮禁用
    const add = bodyButtons().find((b) => b.textContent?.includes('加入合集'))
    expect(add?.disabled).toBe(true)
  })

  it('全部已在合集内（added=0）时不显示撤销条', async () => {
    mocks.convert.mockResolvedValue({ ...COLLECTION, semi_manual: true })
    mocks.expand.mockResolvedValue({
      batch_id: null,
      added: 0,
      skipped: 2,
      not_found: 0,
      added_ids: [],
    })
    const wrapper = mountModal()
    await flushPromises()

    clickBodyButton('全选本页')
    await flushPromises()
    clickBodyButton('加入合集')
    await flushPromises()

    expect(mocks.expand).toHaveBeenCalled()
    expect(mocks.message.info).toHaveBeenCalledWith('所选素材都已在合集内，没有新增')
    expect(wrapper.emitted('applied')).toBeUndefined()
  })

  it('生成失败展示错误并可重试', async () => {
    mocks.suggest.mockRejectedValue({
      response: { data: { detail: '合集不可用' } },
    })
    mountModal()
    await flushPromises()

    expect(document.body.textContent).toContain('合集不可用')
    mocks.suggest.mockResolvedValue(SUGGEST_RESULT)
    clickBodyButton('重试')
    await flushPromises()
    expect(document.body.textContent).toContain('88%')
  })
})

describe('CollectionExpandModal 纯智能合集', () => {
  const SMART: CollectionOut = {
    ...COLLECTION,
    id: 6,
    name: '纯智能',
    kind: 'smart',
    semi_manual: false,
    query_json: { is_favorite: true },
  }

  it('加入时才转半智能：先调 to-semi 再扩充', async () => {
    const wrapper = mountModal(SMART)
    await flushPromises()

    expect(document.body.textContent).toContain('点「加入」会先转为半智能')
    expect(mocks.convert).not.toHaveBeenCalled()

    clickBodyButton('全选本页')
    await flushPromises()
    clickBodyButton('转为半智能并加入')
    await flushPromises()

    expect(mocks.convert).toHaveBeenCalledWith(6)
    expect(mocks.expand).toHaveBeenCalledWith(6, ['i1', 'i2'])
    const calls = mocks.convert.mock.invocationCallOrder[0]
    const expandCall = mocks.expand.mock.invocationCallOrder[0]
    expect(calls).toBeLessThan(expandCall)
    expect(wrapper.emitted('applied')).toBeTruthy()
  })

  it('仅打开/取消不会改变合集类型', async () => {
    mountModal(SMART)
    await flushPromises()
    clickBodyButton('取消')
    await flushPromises()
    expect(mocks.convert).not.toHaveBeenCalled()
  })
})
