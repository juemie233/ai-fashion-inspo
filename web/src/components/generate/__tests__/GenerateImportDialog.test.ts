/**
 * 入库候选选择弹窗：默认全选、可取消勾选、已入库不可重复选、悬停放大组件在位。
 *
 * 用户约定：多张候选时弹窗默认全选；按「确认入库」后把选中的文件名回抛给历史表。
 * 弹窗用 stub 渲染（真实 Arco modal 走 teleport，jsdom 里不稳）。
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import GenerateImportDialog from '../GenerateImportDialog.vue'
import HoverImagePreview from '@/components/common/HoverImagePreview.vue'
import type { GenerationCandidate } from '@/api/generate'

const ModalStub = {
  name: 'AModalStub',
  props: ['visible', 'title', 'width', 'footer'],
  template: '<div class="modal-stub"><slot /></div>',
}

function candidate(overrides: Partial<GenerationCandidate> = {}): GenerationCandidate {
  return {
    filename: '01_seed-1.png',
    index: 1,
    seed: 1,
    size_bytes: 1024,
    imported: false,
    url: '/api/generate/runs/12/candidates/01_seed-1.png',
    ...overrides,
  }
}

function mountDialog(candidates: GenerationCandidate[]) {
  return mount(GenerateImportDialog, {
    props: { visible: true, runId: 12, candidates },
    global: { plugins: [ArcoVue], stubs: { 'a-modal': ModalStub } },
    attachTo: document.body,
  })
}

const CANDIDATES = [
  candidate({ filename: '01_seed-1.png', seed: 1 }),
  candidate({ filename: '02_seed-2.png', index: 2, seed: 2 }),
  candidate({ filename: '03_seed-3.png', index: 3, seed: 3, imported: true }),
]

describe('GenerateImportDialog', () => {
  it('默认全选所有未入库候选，已入库的不可选', () => {
    const wrapper = mountDialog(CANDIDATES)

    expect(wrapper.findAll('.gid-card')).toHaveLength(3)
    expect(wrapper.findAll('.gid-card.is-selected')).toHaveLength(2) // 只选未入库的两张
    expect(wrapper.findAll('.gid-card.is-imported')).toHaveLength(1)
    expect(wrapper.findAll('.arco-checkbox')).toHaveLength(2) // 已入库那张没有勾选框
    expect(wrapper.text()).toContain('已选 2 / 2 张')

    wrapper.unmount()
  })

  it('点卡片取消/恢复勾选，确认时回抛选中的文件名', async () => {
    const wrapper = mountDialog(CANDIDATES)
    const cards = wrapper.findAll('.gid-card')

    await cards[0].trigger('click') // 取消第一张
    expect(wrapper.text()).toContain('已选 1 / 2 张')

    await cards[0].trigger('click') // 再选回来
    expect(wrapper.text()).toContain('已选 2 / 2 张')

    const confirmBtn = wrapper.findAll('button').find((btn) => btn.text().includes('确认入库'))
    expect(confirmBtn).toBeTruthy()
    await confirmBtn!.trigger('click')

    expect(wrapper.emitted('confirm')?.[0]).toEqual([
      [
        { run_id: 12, filename: '01_seed-1.png' },
        { run_id: 12, filename: '02_seed-2.png' },
      ],
    ])
    expect(wrapper.emitted('update:visible')?.[0]).toEqual([false])

    wrapper.unmount()
  })

  it('清空后确认按钮禁用，全选可恢复', async () => {
    const wrapper = mountDialog(CANDIDATES)
    const buttonByText = (text: string) =>
      wrapper.findAll('button').find((btn) => btn.text() === text)!

    await buttonByText('清空').trigger('click')
    const confirmBtn = wrapper.findAll('button').find((btn) => btn.text().includes('确认入库'))!
    expect(confirmBtn.attributes('disabled')).toBeDefined()

    await buttonByText('全选').trigger('click')
    expect(wrapper.text()).toContain('已选 2 / 2 张')

    wrapper.unmount()
  })

  it('图片带悬停放大组件：卡片 384、预览 1024', () => {
    const wrapper = mountDialog(CANDIDATES)
    const previews = wrapper.findAllComponents(HoverImagePreview)

    expect(previews).toHaveLength(3)
    expect(previews[0].props('src')).toContain('01_seed-1.png?w=384')
    expect(previews[0].props('largeSrc')).toContain('01_seed-1.png?w=1024')

    wrapper.unmount()
  })

  it('多姿态合并候选：默认全选，确认时按底层批次回抛', async () => {
    const wrapper = mountDialog([
      {
        ...candidate({ filename: '01_seed-0.png' }),
        run_id: 20,
      },
      {
        ...candidate({ filename: '01_seed-0.png' }),
        run_id: 30,
      },
    ])

    expect(wrapper.text()).toContain('多姿态合并行会按底层批次分别入库')
    expect(wrapper.text()).toContain('已选 2 / 2 张')
    const confirmBtn = wrapper.findAll('button').find((btn) => btn.text().includes('确认入库'))
    await confirmBtn!.trigger('click')

    expect(wrapper.emitted('confirm')?.[0]).toEqual([
      [
        { run_id: 20, filename: '01_seed-0.png' },
        { run_id: 30, filename: '01_seed-0.png' },
      ],
    ])
    wrapper.unmount()
  })
})
