/**
 * 候选区的「对比」入口：必须是**独立**的一条路径。
 *
 * 候选区里「点图片」= 看大图（灯箱），「对比」= 两栏比参考图。两者混在一起会让
 * 「我只是想放大看细节」变成「弹出一个对比窗」，所以这里锁死两条 emit 互不干扰。
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import GenerateCandidatesPanel from '../GenerateCandidatesPanel.vue'
import type { GenerationCandidate, GenerationRun } from '@/api/generate'

const candidates: GenerationCandidate[] = [
  { filename: '0.png', index: 0, seed: 100, size_bytes: 1000, imported: false, url: '/g/0.png' },
  { filename: '1.png', index: 1, seed: 101, size_bytes: 1000, imported: false, url: '/g/1.png' },
]

function run(kind: 't2i' | 'i2i'): GenerationRun {
  return {
    id: 7,
    kind,
    status: 'done',
    prompt: 'p',
    negative: null,
    params: { reference_ids: kind === 'i2i' ? ['ref-1'] : [] },
    model_key: kind === 'i2i' ? 'qwen_2_1_i2i' : 'z_image_int8',
    workflow_name: 'w',
    workflow_hash: 'h',
    total: 2,
    done: 2,
    failed: 0,
    error: null,
    task_id: null,
    batch_dir: 'dir',
    created_at: null,
  }
}

function mountPanel(kind: 't2i' | 'i2i') {
  return mount(GenerateCandidatesPanel, {
    props: {
      run: run(kind),
      candidates,
      selected: new Set<string>(),
      stageText: '完成',
      lastSeed: 100,
      submitting: false,
    },
    global: { plugins: [ArcoVue] },
  })
}

describe('候选区对比入口', () => {
  it('图生图批次有「对比参考图」按钮，点了从第 1 张开始比', async () => {
    const wrapper = mountPanel('i2i')
    const btn = wrapper.findAll('button').find((b) => b.text().includes('对比参考图'))
    expect(btn, '图生图批次应提供对比入口').toBeTruthy()

    await btn!.trigger('click')
    expect(wrapper.emitted('compare')?.[0]).toEqual([0])
  })

  it('文生图批次不显示该按钮（没有参考图可比）', () => {
    const wrapper = mountPanel('t2i')
    expect(wrapper.findAll('button').some((b) => b.text().includes('对比参考图'))).toBe(false)
  })

  it('每张候选都有「对比」，且不影响「点图看大图」', async () => {
    const wrapper = mountPanel('i2i')

    // 第 2 张的「对比」→ compare(1)，不触发 preview / regenerate
    const secondItemBtns = wrapper
      .findAll('.gen-item')
      .slice(1)
      .flatMap((el) => el.findAll('.gen-item-actions button'))
    const compareBtn = secondItemBtns.find((b) => b.text().includes('对比'))!
    await compareBtn.trigger('click')
    expect(wrapper.emitted('compare')?.[0]).toEqual([1])
    expect(wrapper.emitted('preview')).toBeUndefined()
    expect(wrapper.emitted('regenerate')).toBeUndefined()

    // 点图片本身仍是「看大图」
    await wrapper.findAll('.gen-item img')[0].trigger('click')
    expect(wrapper.emitted('preview')?.[0]).toEqual([0])
  })

  it('云端批次（无 seed）：不展示 seed 0，按钮文案改为「重新生成」', () => {
    const wrapper = mount(GenerateCandidatesPanel, {
      props: {
        run: run('i2i'),
        candidates: [{ ...candidates[0], seed: 0 }],
        selected: new Set<string>(),
        stageText: '完成',
        lastSeed: null,
        seedSupported: false,
        submitting: false,
      },
      global: { plugins: [ArcoVue] },
    })

    expect(wrapper.text()).toContain('云端 · 无 seed')
    expect(wrapper.text()).not.toContain('seed 0')
    const regenerateBtn = wrapper
      .findAll('.gen-item-actions button')
      .find((b) => b.text().includes('重新生成'))
    expect(regenerateBtn).toBeTruthy()
  })

  it('多姿态合并候选：同名文件用批次区分选择键，并提示合并姿态数', async () => {
    const groupCandidates: GenerationCandidate[] = [
      { ...candidates[0], seed: 0, run_id: 20, select_key: '20:0.png' },
      { ...candidates[0], seed: 0, run_id: 30, select_key: '30:0.png' },
    ]
    const wrapper = mount(GenerateCandidatesPanel, {
      props: {
        run: run('i2i'),
        candidates: groupCandidates,
        selected: new Set<string>(['20:0.png']),
        stageText: '完成',
        lastSeed: null,
        submitting: false,
      },
      global: { plugins: [ArcoVue] },
    })

    expect(wrapper.text()).toContain('本次合并 2 个姿态')
    // 第一张已选中，第二张未选中：选择状态按 select_key 生效（不会两张一起亮）
    expect(wrapper.findAll('.gen-item.is-selected')).toHaveLength(1)

    const secondCheckbox = wrapper.findAll('.gen-item .arco-checkbox input')[1]
    await secondCheckbox.setValue(true)
    expect(wrapper.emitted('toggle')?.[0]).toEqual(['30:0.png'])
  })
})
