/**
 * 生成历史表：状态中文化、时间格式与「展开行看完整详情」。
 *
 * 背景（用户反馈）：
 * - 时间列显示的是 UTC 被当成本地时间（后端已改为带 Z 的 ISO 串）；
 * - 描述列单行省略且后端只返回前 120 字，展开行也看不到完整描述。
 * 这里锁住展开行的核心内容，防止以后又被截断。
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import GenerateHistoryTable from '../GenerateHistoryTable.vue'
import type { GenerateStatus, GenerationRunBrief } from '@/api/generate'
import { formatDateTime } from '@/utils/format'

const LONG_PROMPT =
  '法式复古碎花雪纺连衣裙，浅蓝色底配白色小碎花，收腰显瘦，适合春季通勤与周末约会，' +
  '搭配米色针织开衫与白色帆布鞋，自然光街拍，柔和背景虚化，突出面料垂坠感。'

const status: GenerateStatus = {
  ready: true,
  reason: null,
  comfyui: {},
  models: {},
  ollama_resident: [],
  models_meta: {
    qwen_2_1_i2i: {
      label: 'Qwen-Image 2.1（参考图改款）',
      kind: 'i2i',
      defaults: {},
    },
  },
}

function record(overrides: Partial<GenerationRunBrief> = {}): GenerationRunBrief {
  return {
    id: 12,
    kind: 'i2i',
    status: 'done',
    prompt: LONG_PROMPT,
    negative: '模糊，低质量，畸形手指',
    params: {
      width: 1024,
      height: 768,
      steps: 24,
      cfg: 4.5,
      sampler: 'euler',
      scheduler: 'normal',
      seed_base: 123456,
      count: 2,
      reference_ids: ['ref-a', 'ref-b'],
    },
    model_key: 'qwen_2_1_i2i',
    workflow_name: 'qwen_image_i2i',
    total: 2,
    done: 2,
    failed: 0,
    error: null,
    created_at: '2026-10-26T12:00:00Z',
    updated_at: '2026-10-26T12:03:00Z',
    ...overrides,
  }
}

function mountTable(row: GenerationRunBrief) {
  return mount(GenerateHistoryTable, {
    props: {
      history: [row],
      loading: false,
      submitting: false,
      deletingId: null,
      status,
    },
    global: { plugins: [ArcoVue] },
    attachTo: document.body,
  })
}

describe('生成历史表', () => {
  it('状态显示中文、时间显示为本地格式化结果', async () => {
    const row = record()
    const wrapper = mountTable(row)
    // 列通过 #columns 插槽注册，表体要等一次渲染后才出现
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('已完成')
    expect(wrapper.text()).not.toContain('done')
    expect(wrapper.text()).toContain(formatDateTime(row.created_at))
    expect(wrapper.text()).not.toContain('Invalid Date')

    wrapper.unmount()
  })

  it('展开行展示完整描述、负面词与全部生成参数', async () => {
    const wrapper = mountTable(record())

    const expandBtn = wrapper.find('.arco-table-expand-btn')
    expect(expandBtn.exists()).toBe(true)
    await expandBtn.trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    const detail = wrapper.find('[data-test="history-detail"]')
    expect(detail.exists(), '展开后应渲染完整详情').toBe(true)
    const text = detail.text()
    expect(text).toContain(LONG_PROMPT)
    expect(text).toContain('负面提示词')
    expect(text).toContain('模糊，低质量，畸形手指')
    expect(text).toContain('参考图改款')
    expect(text).toContain('尺寸 1024×768')
    expect(text).toContain('步数 24')
    expect(text).toContain('CFG 4.5')
    expect(text).toContain('采样器 euler')
    expect(text).toContain('调度器 normal')
    expect(text).toContain('seed 123456')
    expect(text).toContain('张数 2')
    expect(text).toContain('参考素材 2 张')
    expect(text).toContain('工作流 qwen_image_i2i')
    expect(text).toContain('复制描述')

    wrapper.unmount()
  })

  it('失败批次展开后能看到失败原因', async () => {
    const wrapper = mountTable(
      record({ status: 'failed', done: 0, error: 'ComfyUI 显存不足（需要 15.1 GiB）' }),
    )
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('失败')

    const expandBtn = wrapper.find('.arco-table-expand-btn')
    await expandBtn.trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    const detail = wrapper.find('[data-test="history-detail"]')
    expect(detail.text()).toContain('失败原因')
    expect(detail.text()).toContain('ComfyUI 显存不足（需要 15.1 GiB）')

    wrapper.unmount()
  })
})
