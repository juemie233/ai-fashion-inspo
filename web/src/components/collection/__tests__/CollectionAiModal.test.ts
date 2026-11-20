/**
 * 合集 AI 整理弹窗测试。
 *
 * 关键约束（用户已确认）：
 * - 打开不自动生成，必须手动点「开始生成」（本地模型很贵）；
 * - 候选名 / 总结描述都要用户点「采用」才写回合集；
 * - 收藏理由、主题标签只展示 + 复制，不自动保存；
 * - 分组缩略图可点进素材详情；失败可重试。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  aiSummarizeCollection: vi.fn(),
  updateCollection: vi.fn(),
  copyToClipboard: vi.fn(),
  openInspiration: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/collections', () => ({
  aiSummarizeCollection: mocks.aiSummarizeCollection,
  updateCollection: mocks.updateCollection,
}))

vi.mock('@/utils/clipboard', () => ({ copyToClipboard: mocks.copyToClipboard }))

vi.mock('@/utils/openInspiration', () => ({ openInspiration: mocks.openInspiration }))

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import CollectionAiModal from '../CollectionAiModal.vue'
import type { CollectionOut } from '@/api/collections'

const COLLECTION: CollectionOut = {
  id: 7,
  name: '待整理合集',
  description: null,
  kind: 'manual',
  position: 0,
  parent_id: null,
  auto_source: null,
  child_count: 0,
  cover_inspiration_id: null,
  cover_thumbnail_path: null,
  item_count: 4,
  query_json: null,
  created_at: '',
  updated_at: '',
}

const SUMMARY = {
  summary: '这一组是低饱和通勤穿搭，以衬衫和直筒裤为主。',
  name_suggestions: ['通勤极简', '低饱和日常', '衬衫直筒裤'],
  reason: '配色克制、单品基础，适合复用日常通勤。',
  theme_tags: ['通勤', '极简', '低饱和'],
  groups: [
    {
      tag: 'outfit',
      name: '通勤衬衫',
      count: 2,
      item_ids: ['i1', 'i2'],
      items: [
        { id: 'i1', thumbnail_path: 'thumbs/i1.jpg', source_author: '小葵' },
        { id: 'i2', thumbnail_path: 'thumbs/i2.jpg', source_author: '阿蓝' },
      ],
    },
    {
      tag: 'outfit',
      name: '直筒裤',
      count: 1,
      item_ids: ['i3'],
      items: [{ id: 'i3', thumbnail_path: null, source_author: null }],
    },
  ],
  model: 'qwen3-vl:8b',
  generated_at: '2026-11-20T12:00:00',
}

function mountModal() {
  return mount(CollectionAiModal, {
    props: { visible: true, collection: COLLECTION },
    global: { plugins: [ArcoVue] },
    attachTo: document.body,
  })
}

function clickBodyButton(text: string): void {
  const button = [...document.body.querySelectorAll('button')].find((b) =>
    b.textContent?.includes(text),
  )
  expect(button, `按钮「${text}」应存在`).toBeTruthy()
  button!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

beforeEach(() => {
  vi.clearAllMocks()
  document.body.innerHTML = ''
  mocks.aiSummarizeCollection.mockResolvedValue(SUMMARY)
  mocks.updateCollection.mockResolvedValue({ ...COLLECTION, name: '通勤极简' })
  mocks.copyToClipboard.mockResolvedValue(true)
})

describe('CollectionAiModal', () => {
  it('打开不自动生成；手动点「开始生成」后才调接口并渲染预览', async () => {
    mountModal()
    await flushPromises()
    expect(mocks.aiSummarizeCollection).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('开始生成')

    clickBodyButton('开始生成')
    await flushPromises()

    expect(mocks.aiSummarizeCollection).toHaveBeenCalledWith(7)
    const text = document.body.textContent ?? ''
    expect(text).toContain(SUMMARY.summary)
    expect(text).toContain('通勤极简')
    expect(text).toContain('低饱和日常')
    expect(text).toContain('衬衫直筒裤')
    expect(text).toContain(SUMMARY.reason)
    expect(text).toContain('通勤')
    expect(text).toContain('低饱和')
    expect(text).toContain('通勤衬衫')
    expect(text).toContain('2 个素材')
    expect(text).toContain('qwen3-vl:8b')
  })

  it('候选名「采用」才 updateCollection 改名，并通知父级刷新', async () => {
    const wrapper = mountModal()
    await flushPromises()
    clickBodyButton('开始生成')
    await flushPromises()

    const adoptButtons = [...document.body.querySelectorAll('button')].filter(
      (b) => b.textContent?.trim() === '采用',
    )
    expect(adoptButtons.length).toBe(3)
    adoptButtons[0].dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()

    expect(mocks.updateCollection).toHaveBeenCalledWith(7, { name: '通勤极简' })
    expect(wrapper.emitted('applied')).toBeTruthy()
  })

  it('「采用为合集描述」把风格总结写为 description', async () => {
    mountModal()
    await flushPromises()
    clickBodyButton('开始生成')
    await flushPromises()

    clickBodyButton('采用为合集描述')
    await flushPromises()

    expect(mocks.updateCollection).toHaveBeenCalledWith(7, { description: SUMMARY.summary })
  })

  it('复制收藏理由 / 主题标签，不自动保存', async () => {
    mountModal()
    await flushPromises()
    clickBodyButton('开始生成')
    await flushPromises()

    clickBodyButton('复制理由')
    expect(mocks.copyToClipboard).toHaveBeenCalledWith(SUMMARY.reason)
    clickBodyButton('复制标签')
    expect(mocks.copyToClipboard).toHaveBeenCalledWith('通勤、极简、低饱和')

    // 复制动作不会写回后端
    expect(mocks.updateCollection).not.toHaveBeenCalled()
  })

  it('分组缩略图可点进素材详情', async () => {
    mountModal()
    await flushPromises()
    clickBodyButton('开始生成')
    await flushPromises()

    const thumb = document.querySelector<HTMLButtonElement>('.group-thumb')
    expect(thumb).toBeTruthy()
    thumb!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(mocks.openInspiration).toHaveBeenCalledWith(expect.anything(), 'i1', {})
  })

  it('生成失败展示错误并可重试', async () => {
    mocks.aiSummarizeCollection.mockRejectedValueOnce({
      response: { data: { detail: '本地模型未启动' } },
    })
    mountModal()
    await flushPromises()

    clickBodyButton('开始生成')
    await flushPromises()
    expect(document.body.textContent).toContain('本地模型未启动')

    clickBodyButton('重试')
    await flushPromises()
    expect(mocks.aiSummarizeCollection).toHaveBeenCalledTimes(2)
    expect(document.body.textContent).toContain('风格总结')
  })
})
