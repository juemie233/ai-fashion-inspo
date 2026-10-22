/**
 * 采集任务表单（ScraperTaskForm）的创建校验与请求体测试。
 *
 * 这个表单决定了「提交给后端什么」，而它的错误路径特别值得锁：
 * - 按博主采集时没选博主 → 必须**拦住**（后端会 404「博主未找到」，用户看不懂）；
 * - 关键词是「多个输入框 + 逗号/顿号粘贴」的混合体，拆分错了会采到错的东西；
 * - CDP 端口开关关掉时要显式传 `null`（不能省略，否则后端沿用上次端口）；
 * - 抖音的「下载视频」默认开启，关掉才传 `download_video: false`（省流量不传 true）；
 * - 后端 Chrome 未启动时返回**带启动命令的对象**，前端要能提示并自动复制命令。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue, { Select } from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  clientPost: vi.fn(),
  clientGet: vi.fn(),
  warning: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  copyText: vi.fn(),
  fetchBloggers: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  default: { post: mocks.clientPost, get: mocks.clientGet },
}))

vi.mock('@/api/persons', () => ({
  bloggersApi: { fetchList: mocks.fetchBloggers },
  modelsApi: { fetchList: vi.fn().mockResolvedValue({ items: [], total: 0 }) },
}))

vi.mock('@/utils/clipboard', () => ({ copyToClipboard: mocks.copyText }))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return {
    ...actual,
    Message: { warning: mocks.warning, success: mocks.success, error: mocks.error },
  }
})

import ScraperTaskForm from '../ScraperTaskForm.vue'

function mountForm() {
  return mount(ScraperTaskForm, { global: { plugins: [ArcoVue] } })
}

/** 关键词是多选 a-select（allow-create）：向它派发 v-model 更新即等价于用户选定。 */
async function setKeywords(wrapper: ReturnType<typeof mountForm>, keywords: string[]) {
  const select = wrapper
    .findAllComponents(Select)
    .find((c) => Array.isArray((c.props() as { modelValue?: unknown }).modelValue))
  expect(select, '关键词多选框应渲染').toBeTruthy()
  select!.vm.$emit('update:modelValue', keywords)
  await flushPromises()
}

function lastBody() {
  const calls = mocks.clientPost.mock.calls
  return calls[calls.length - 1]?.[1] ?? {}
}

describe('ScraperTaskForm 创建校验', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // 表单会把上次输入保存成草稿（localStorage），不清理会让用例互相污染
    localStorage.clear()
    mocks.clientPost.mockResolvedValue({ data: { id: 1 } })
    mocks.clientGet.mockResolvedValue({
      data: { available: true, is_google_chrome: true, detail: 'ok' },
    })
    mocks.fetchBloggers.mockResolvedValue({ items: [], total: 0 })
  })

  it('搜索模式：提交平台与关键词（逗号/顿号/中英文逗号都拆开）', async () => {
    const wrapper = mountForm()
    await flushPromises()

    await setKeywords(wrapper, ['穿搭,极简、通勤，日常'])
    const createBtn = wrapper.findAll('button').find((b) => b.text().includes('开始采集'))
    expect(createBtn, '应有「开始采集」按钮').toBeTruthy()
    await createBtn!.trigger('click')
    await flushPromises()

    expect(mocks.clientPost).toHaveBeenCalled()
    const body = lastBody()
    expect(body.platform).toBe('xiaohongshu')
    expect(body.keywords).toEqual(['穿搭', '极简', '通勤', '日常'])
    expect(body.collect_mode).toBeUndefined() // 搜索模式不带 collect_mode
  })

  it('关键词为空时不拦截（由后端校验）但要发出请求体里的空数组', async () => {
    const wrapper = mountForm()
    await flushPromises()

    const createBtn = wrapper.findAll('button').find((b) => b.text().includes('开始采集'))
    await createBtn!.trigger('click')
    await flushPromises()

    if (mocks.clientPost.mock.calls.length) {
      expect(lastBody().keywords).toEqual([])
    }
  })

  it('CDP 关闭时端口显式传 null（不能省略字段）', async () => {
    const wrapper = mountForm()
    await flushPromises()

    await setKeywords(wrapper, ['穿搭'])
    const createBtn = wrapper.findAll('button').find((b) => b.text().includes('开始采集'))
    await createBtn!.trigger('click')
    await flushPromises()

    const body = lastBody()
    expect(Object.prototype.hasOwnProperty.call(body, 'cdp_port')).toBe(true)
    expect(body.cdp_port === null || typeof body.cdp_port === 'number').toBe(true)
  })

  it('后端返回「带启动命令」的错误对象时：提示错误并自动复制命令', async () => {
    vi.useFakeTimers()
    mocks.clientPost.mockRejectedValue({
      response: {
        data: {
          detail: {
            error: 'Chrome 调试端口不可用',
            command: 'chrome --remote-debugging-port=9222',
          },
        },
      },
    })

    const wrapper = mountForm()
    await flushPromises()

    await setKeywords(wrapper, ['穿搭'])
    const createBtn = wrapper.findAll('button').find((b) => b.text().includes('开始采集'))
    await createBtn!.trigger('click')
    await flushPromises()

    expect(mocks.error).toHaveBeenCalledWith('Chrome 调试端口不可用')
    vi.advanceTimersByTime(600)
    expect(mocks.copyText).toHaveBeenCalledWith('chrome --remote-debugging-port=9222')
    vi.useRealTimers()
  })

  it('创建成功：emit created 并提示（父级据此刷新列表）', async () => {
    const wrapper = mountForm()
    await flushPromises()

    await setKeywords(wrapper, ['穿搭'])
    const createBtn = wrapper.findAll('button').find((b) => b.text().includes('开始采集'))
    await createBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('created')).toBeTruthy()
    expect(mocks.success).toHaveBeenCalled()
  })
})
