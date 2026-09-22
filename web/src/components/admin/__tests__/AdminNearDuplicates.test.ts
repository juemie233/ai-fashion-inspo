/**
 * AdminNearDuplicates 删除流程测试：对比完成后，已做的删除决定必须真的提交出去。
 *
 * 回归（用户反馈「近似重复素材对比后没有执行删除」）：
 * ① 逐组对比结束后点「确认提交删除」→ emit delete-selected（带待删 ID）；
 * ② **中途退出**（右上角 X / 「退出」）→ 已做的决定也要提交，不能白选一遍；
 * ③ 全部选「都保留（跳过本组）」→ 没有任何待删 ID，退出时不提交（避免空任务）。
 */

import { defineComponent, h } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'

const nearDup = vi.hoisted(() => ({
  fetchNearDuplicates: vi.fn(),
  startPhashBackfill: vi.fn(),
}))

vi.mock('@/api/admin', () => nearDup)
vi.mock('@/api/inspirations', () => ({ getFileUrl: (p: string) => `/${p}` }))

import AdminNearDuplicates from '../AdminNearDuplicates.vue'

function file(id: string) {
  return {
    id,
    file_path: `images/${id}.jpg`,
    thumbnail_path: null,
    is_favorite: false,
    created_at: '2026-09-20T10:00:00',
    size_bytes: 1000,
    score: 0,
    distance: 0,
  }
}

function group(ids: string[]) {
  return {
    rep_phash: 'x'.repeat(48),
    keeper_id: ids[0],
    wasted_bytes: 1000,
    files: ids.map(file),
  }
}

/** 渲染「全部插槽」（含 #extra 等具名插槽）的通用桩：Arco 未在测试环境全局注册 */
function slotStub(name: string) {
  return defineComponent({
    name,
    render() {
      const slots = this.$slots
      const named = Object.entries(slots)
        .filter(([key]) => key !== 'default')
        .map(([, render]) => render?.())
      return h('div', { class: name }, [slots.default?.(), ...named])
    },
  })
}

const ButtonStub = defineComponent({
  name: 'AButton',
  render() {
    return h('button', { class: 'a-button' }, this.$slots.default?.())
  },
})

const PopconfirmStub = defineComponent({
  name: 'APopconfirm',
  emits: ['ok'],
  render() {
    return h('span', [
      this.$slots.default?.(),
      h('button', { class: 'popconfirm-ok', onClick: () => this.$emit('ok') }, '确认'),
    ])
  },
})

const stubs = {
  'a-button': ButtonStub,
  'a-popconfirm': PopconfirmStub,
  'a-card': slotStub('ACard'),
  'a-modal': slotStub('AModal'),
  'a-tag': slotStub('ATag'),
  'a-result': slotStub('AResult'),
  'a-space': slotStub('ASpace'),
  'a-empty': slotStub('AEmpty'),
  'a-alert': slotStub('AAlert'),
  'a-progress': slotStub('AProgress'),
  'a-select': slotStub('ASelect'),
  'a-form': slotStub('AForm'),
  'a-form-item': slotStub('AFormItem'),
  'a-radio-group': slotStub('ARadioGroup'),
  'a-radio': slotStub('ARadio'),
  'a-input-number': slotStub('AInputNumber'),
  'a-switch': slotStub('ASwitch'),
  'a-typography-text': slotStub('ATypographyText'),
}

function button(wrapper: VueWrapper, text: string) {
  const hit = wrapper.findAll('button').find((b) => b.text().includes(text))
  if (!hit) {
    throw new Error(
      `找不到按钮「${text}」；当前按钮：${wrapper
        .findAll('button')
        .map((b) => b.text())
        .join(' | ')}`,
    )
  }
  return hit
}

/** 扫描出给定分组并打开对比弹窗 */
async function mountScanned(groups: ReturnType<typeof group>[]) {
  nearDup.fetchNearDuplicates.mockResolvedValue({
    total: groups.length * 2,
    scanned: groups.length * 2,
    truncated: false,
    missing: 0,
    cached_total: groups.length * 2,
    backfilled: 0,
    groups,
  })
  const wrapper = mount(AdminNearDuplicates, {
    global: { stubs },
    attachTo: document.body,
  })
  await flushPromises()
  await button(wrapper, '扫描近似重复').trigger('click')
  await flushPromises()
  return wrapper
}

describe('AdminNearDuplicates 删除流程', () => {
  it('逐组对比后「确认提交删除」：emit 待删素材 ID', async () => {
    const wrapper = await mountScanned([group(['aaa', 'bbb'])])

    await button(wrapper, '保留左边').trigger('click') // 保留 aaa → 删 bbb
    await flushPromises()
    await button(wrapper, '确认提交删除').trigger('click')
    await wrapper.find('button.popconfirm-ok').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('delete-selected')?.[0]).toEqual([['bbb']])
  })

  it('中途退出（未走完队列）：已做的决定也要提交', async () => {
    const wrapper = await mountScanned([group(['aaa', 'bbb']), group(['ccc', 'ddd'])])

    await button(wrapper, '保留左边').trigger('click') // 第 1 组：删 bbb
    await flushPromises()
    await button(wrapper, '退出').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('delete-selected')?.[0]).toEqual([['bbb']])
    expect(wrapper.emitted('delete-selected')).toHaveLength(1)
  })

  it('全部「都保留（跳过本组）」：退出时不提交空删除', async () => {
    const wrapper = await mountScanned([group(['aaa', 'bbb'])])

    await button(wrapper, '都保留（跳过本组）').trigger('click')
    await flushPromises()
    await button(wrapper, '退出').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('delete-selected')).toBeUndefined()
  })

  it('可翻页回看并改主意：提交时按最后一次选择算（防误删）', async () => {
    const wrapper = await mountScanned([group(['aaa', 'bbb']), group(['ccc', 'ddd'])])

    // 第 1 组先误选「保留右边」（会删 aaa）→ 自动进入第 2 组
    await button(wrapper, '保留右边').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('第 2 / 2 组')

    // 翻回第 1 组改选「保留左边」（应删 bbb，撤销掉 aaa）
    await button(wrapper, '← 上一组').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('第 1 / 2 组')
    expect(wrapper.text()).toContain('本组已选：保留右边')

    await button(wrapper, '保留左边').trigger('click')
    await flushPromises()

    // 第 2 组保持未决定 → 直接去提交，只该删第 1 组按最后一次选择算出的 bbb
    await button(wrapper, '← 上一组').trigger('click')
    await flushPromises()
    await button(wrapper, '去提交').trigger('click')
    await flushPromises()
    await button(wrapper, '确认提交删除').trigger('click')
    await wrapper.find('button.popconfirm-ok').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('delete-selected')?.[0]).toEqual([['bbb']])
  })

  it('翻页边界：第 1 组不能上一组，最后一组不能下一组', async () => {
    const wrapper = await mountScanned([group(['aaa', 'bbb']), group(['ccc', 'ddd'])])

    expect(button(wrapper, '← 上一组').attributes('disabled')).toBeDefined()
    expect(button(wrapper, '下一组 →').attributes('disabled')).toBeUndefined()

    await button(wrapper, '下一组 →').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('第 2 / 2 组')
    expect(button(wrapper, '下一组 →').attributes('disabled')).toBeDefined()
    // 只翻页不决策：不应产生任何待删素材
    expect(wrapper.text()).toContain('已决定删除 0 个素材')
  })

  it('连点保护：决定后紧接着的第二次点击不会决定下一组', async () => {
    const wrapper = await mountScanned([
      group(['aaa', 'bbb']),
      group(['ccc', 'ddd']),
      group(['eee', 'fff']),
    ])

    // 手快连点两下「保留左边」：第二下必须被 300ms 保护挡掉，
    // 否则第 2 组会被同一个按钮决定掉（两张原本要留的图被删）
    await button(wrapper, '保留左边').trigger('click')
    await button(wrapper, '保留左边').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('第 2 / 3 组') // 只前进了一组
    expect(wrapper.text()).toContain('已处理 1 / 3 组')
    expect(wrapper.text()).toContain('已决定删除 1 个素材') // 只有第 1 组的 bbb
  })
})
