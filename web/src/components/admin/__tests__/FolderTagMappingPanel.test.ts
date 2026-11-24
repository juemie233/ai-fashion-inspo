/**
 * 抖音收藏夹 → 穿搭大标签映射面板测试。
 *
 * 关键约束：
 * - 三态展示（已映射 / 待确认 / 已跳过）与待确认提示；
 * - 同名类别冲突 → 三选一（复用改类别 / 另起名 / 跳过）；
 * - 重命名全局标签必须勾选影响确认后才提交。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  fetchMappings: vi.fn(),
  upsert: vi.fn(),
  apply: vi.fn(),
  applyAll: vi.fn(),
  del: vi.fn(),
  impact: vi.fn(),
  fetchTagsGrouped: vi.fn(),
  updateTag: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  modalConfirm: vi.fn(),
}))

vi.mock('@/api/folderTags', () => ({
  fetchFolderTagMappings: mocks.fetchMappings,
  upsertFolderTagMapping: mocks.upsert,
  applyFolderTagMapping: mocks.apply,
  applyAllFolderTags: mocks.applyAll,
  deleteFolderTagMapping: mocks.del,
  fetchFolderTagImpact: mocks.impact,
}))

vi.mock('@/api/tags', () => ({
  fetchTagsGrouped: mocks.fetchTagsGrouped,
  updateTag: mocks.updateTag,
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return {
    ...actual,
    Message: mocks.message,
    Modal: { ...actual.Modal, confirm: mocks.modalConfirm },
  }
})

import FolderTagMappingPanel from '../FolderTagMappingPanel.vue'

const MAPPED = {
  folder_name: '通勤',
  mode: 'tag',
  needs_confirm: false,
  tag_id: 3,
  tag_name: '通勤',
  tag_category: 'outfit',
  local_collection_id: 11,
  local_item_count: 42,
}

const PENDING = {
  folder_name: '度假',
  mode: 'tag',
  needs_confirm: true,
  tag_id: 4,
  tag_name: '度假',
  tag_category: 'outfit',
  local_collection_id: 12,
  local_item_count: 5,
}

const UNCONFIGURED = {
  folder_name: '仅扫描到',
  mode: 'pending',
  needs_confirm: false,
  tag_id: null,
  tag_name: null,
  tag_category: null,
  local_collection_id: null,
  local_item_count: null,
}

const SKIPPED = {
  folder_name: '不想要',
  mode: 'skip',
  needs_confirm: false,
  tag_id: null,
  tag_name: null,
  tag_category: null,
  local_collection_id: null,
  local_item_count: null,
}

function mountPanel() {
  return mount(FolderTagMappingPanel, {
    global: { plugins: [ArcoVue] },
    attachTo: document.body,
  })
}

function bodyButtons(): HTMLButtonElement[] {
  return [...document.body.querySelectorAll('button')] as HTMLButtonElement[]
}

function clickBodyButton(text: string, index = 0): void {
  const matches = bodyButtons().filter((b) => b.textContent?.includes(text))
  expect(matches.length, `按钮「${text}」应存在`).toBeGreaterThan(index)
  matches[index].dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

/** 单选/复选是 .arco-radio 里的 input，点 label 不一定触发，直接点 input */
function clickRadio(text: string): void {
  const labels = [...document.body.querySelectorAll<HTMLElement>('.arco-radio')]
  const hit = labels.find((node) => node.textContent?.includes(text))
  expect(hit, `选项「${text}」应存在`).toBeTruthy()
  const input = hit!.querySelector('input')
  expect(input, `选项「${text}」应有 input`).toBeTruthy()
  input!.click()
}

/** 多个弹窗同时存在时，按标题定位目标弹窗的「确定」按钮 */
function clickModalOk(titleText: string): void {
  const modals = [...document.body.querySelectorAll<HTMLElement>('.arco-modal')]
  const modal = modals.find((node) => node.textContent?.includes(titleText))
  expect(modal, `弹窗「${titleText}」应存在`).toBeTruthy()
  const ok = [...modal!.querySelectorAll('button')].find(
    (button) => button.textContent?.trim() === '确定',
  )
  expect(ok, '弹窗应有确定按钮').toBeTruthy()
  ok!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

beforeEach(() => {
  vi.clearAllMocks()
  document.body.innerHTML = ''
  mocks.fetchMappings.mockResolvedValue([MAPPED, PENDING, SKIPPED, UNCONFIGURED])
  mocks.fetchTagsGrouped.mockResolvedValue([
    { category: 'outfit', tags: [{ id: 9, name: '极简', usage_count: 10 }] },
  ])
  mocks.upsert.mockResolvedValue(MAPPED)
  mocks.impact.mockResolvedValue({
    tag_id: 3,
    name: '通勤',
    category: 'outfit',
    usage_count: 42,
    mapped_folders: ['通勤'],
  })
})

describe('FolderTagMappingPanel', () => {
  it('展示三态与待确认提示', async () => {
    mountPanel()
    await flushPromises()

    const text = document.body.textContent ?? ''
    expect(text).toContain('通勤')
    expect(text).toContain('已映射')
    expect(text).toContain('待确认')
    expect(text).toContain('已跳过')
    expect(text).toContain('未配置') // 仅扫描到、未建映射的夹不算「待确认」
    expect(text).toContain('有 1 个收藏夹待确认')
    expect(text).toContain('42 个素材')
  })

  it('设置标签为「暂不映射」时提交 skip', async () => {
    mountPanel()
    await flushPromises()

    clickBodyButton('设置标签') // 第一行「通勤」
    await flushPromises()
    clickRadio('暂不映射')
    await flushPromises()
    clickModalOk('设置「通勤」的大标签')
    await flushPromises()

    expect(mocks.upsert).toHaveBeenCalledWith({ folder_name: '通勤', mode: 'skip' })
  })

  it('名称冲突弹出三选一，选择复用后带 reuse_existing_category', async () => {
    const conflict = Object.assign(new Error('409'), {
      response: {
        status: 409,
        data: { detail: '名称「极简」已被「风格」类标签占用；请选择…' },
      },
    })
    mocks.upsert.mockRejectedValueOnce(conflict)

    mountPanel()
    await flushPromises()

    clickBodyButton('设置标签')
    await flushPromises()
    clickRadio('新建一个大标签')
    await flushPromises()
    const input = document.body.querySelector<HTMLInputElement>('input[placeholder="标签名称"]')
    expect(input).toBeTruthy()
    input!.value = '极简'
    input!.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()

    clickModalOk('设置「通勤」的大标签')
    await flushPromises()

    expect(document.body.textContent).toContain('标签名称冲突')
    clickBodyButton('复用并改为穿搭大标签')
    await flushPromises()

    expect(mocks.upsert).toHaveBeenLastCalledWith(
      expect.objectContaining({ reuse_existing_category: true }),
    )
  })

  it('重命名全局标签必须勾选影响确认', async () => {
    mountPanel()
    await flushPromises()

    clickBodyButton('改名')
    await flushPromises()
    expect(mocks.impact).toHaveBeenCalledWith(3)
    expect(document.body.textContent).toContain('42')

    const input = document.body.querySelector<HTMLInputElement>('input[placeholder="新的标签名称"]')
    expect(input).toBeTruthy()
    input!.value = '通勤穿搭'
    input!.dispatchEvent(new Event('input', { bubbles: true }))

    clickModalOk('重命名标签')
    await flushPromises()

    // 未勾选 → 不提交，只提示
    expect(mocks.updateTag).not.toHaveBeenCalled()
    expect(mocks.message.warning).toHaveBeenCalled()
  })
})
