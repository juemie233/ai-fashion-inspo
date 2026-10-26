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
})
