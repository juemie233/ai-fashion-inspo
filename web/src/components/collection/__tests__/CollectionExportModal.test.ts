/**
 * 合集导出弹窗测试：四格式选择、生成结果信息、下载、失败重试。
 *
 * 导出是「显式生成」的耗时操作，且文件 24h 过期：
 * - 打开弹窗不能自动调接口；
 * - 生成成功要展示素材数/跳过数/体积/过期时间；
 * - 下载走 download_url；失败要有可读错误与重试。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  exportCollection: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/collections', () => ({
  exportCollection: mocks.exportCollection,
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import CollectionExportModal from '../CollectionExportModal.vue'

const RESULT = {
  token: 'tok-1',
  filename: '我的穿搭合集.zip',
  format: 'zip',
  item_count: 42,
  skipped_count: 3,
  size_bytes: 1536 * 1024,
  download_url: '/api/collections/exports/tok-1/download',
  expires_at: '2026-11-21T12:00:00',
}

function mountModal() {
  return mount(CollectionExportModal, {
    props: { visible: true, collectionId: 5, collectionName: '我的穿搭合集' },
    global: { plugins: [ArcoVue] },
    attachTo: document.body,
  })
}

/** Arco Modal teleport 到 body：按文案点按钮 */
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
  mocks.exportCollection.mockResolvedValue(RESULT)
})

describe('CollectionExportModal', () => {
  it('打开时展示四种格式与版权/体积提示，不自动调接口', async () => {
    mountModal()
    await flushPromises()

    const text = document.body.textContent ?? ''
    expect(text).toContain('PDF 画册')
    expect(text).toContain('ZIP 原图')
    expect(text).toContain('CSV 清单')
    expect(text).toContain('Markdown 清单')
    expect(text).toContain('版权提示')
    expect(text).toContain('最多导出 500 张')
    expect(mocks.exportCollection).not.toHaveBeenCalled()
  })

  it('选 ZIP 生成后展示素材数/跳过/体积/过期时间，并可下载', async () => {
    mountModal()
    await flushPromises()

    clickBodyButton('ZIP 原图')
    clickBodyButton('生成导出文件')
    await flushPromises()

    expect(mocks.exportCollection).toHaveBeenCalledWith(5, 'zip')

    const text = document.body.textContent ?? ''
    expect(text).toContain('42') // 素材数
    expect(text).toContain('3') // 跳过
    expect(text).toContain('1.5 MB')
    expect(text).toContain('24 小时后过期')
    expect(text).toContain(RESULT.filename)

    // 下载：<a download> 触发一次点击
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    clickBodyButton('下载文件')
    expect(clickSpy).toHaveBeenCalledTimes(1)
    clickSpy.mockRestore()
  })

  it('生成失败展示后端可读错误，可重试成功', async () => {
    mocks.exportCollection.mockRejectedValueOnce({
      response: { data: { detail: '素材缺失过多，无法生成' } },
    })
    mountModal()
    await flushPromises()

    clickBodyButton('生成导出文件')
    await flushPromises()
    expect(document.body.textContent).toContain('素材缺失过多，无法生成')

    clickBodyButton('重试')
    await flushPromises()
    expect(mocks.exportCollection).toHaveBeenCalledTimes(2)
    expect(document.body.textContent).toContain('下载文件')
  })

  it('换格式会作废旧结果，避免下载与当前选择不一致的文件', async () => {
    mountModal()
    await flushPromises()

    clickBodyButton('生成导出文件')
    await flushPromises()
    expect(document.body.textContent).toContain('下载文件')

    clickBodyButton('CSV 清单')
    await flushPromises()
    expect(document.body.textContent).not.toContain('下载文件')
    expect(document.body.textContent).toContain('生成导出文件')
  })
})

describe('CollectionExportModal 切换合集', () => {
  it('切换合集后旧生成请求的结果被丢弃，不会展示上一个合集的文件', async () => {
    let resolveFirst!: (value: unknown) => void
    mocks.exportCollection.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve
        }),
    )
    const wrapper = mountModal()
    await flushPromises()

    clickBodyButton('生成导出文件')
    await flushPromises()

    // 生成进行中切到另一个合集
    await wrapper.setProps({ collectionId: 6, collectionName: '另一个合集' })
    resolveFirst(RESULT)
    await flushPromises()

    const text = document.body.textContent ?? ''
    expect(text).not.toContain(RESULT.filename)
    expect(text).not.toContain('42')
  })
})
