/**
 * 添加模特照片页（ModelPhotoUploadView）回归测试。
 *
 * 覆盖两处历史缺陷：
 * 1. 「请先选择人物」误报：a-select 曾用 v-model:value 绑定（Arco 只支持
 *    modelValue / update:modelValue），人物选择后 personId 从未回写，
 *    点击「开始导入」永远提示「请先选择人物」。
 * 2. 缩略图无法生成：懒加载观察器曾挂在 img 上，而 img 需要 thumbUrl 就绪
 *    才会渲染，形成「无缩略图 → 无 img → 无人触发生成」的死循环。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import ArcoVue, { Message } from '@arco-design/web-vue'
import ModelPhotoUploadView from '../ModelPhotoUploadView.vue'

const mocks = vi.hoisted(() => ({
  routerPush: vi.fn(),
  fetchList: vi.fn(),
  createModelPhotoSet: vi.fn(),
  uploadModelPhoto: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: mocks.routerPush }),
}))

vi.mock('@/api/persons', () => ({
  modelsApi: { fetchList: mocks.fetchList },
  bloggersApi: { fetchList: vi.fn(), create: vi.fn(), update: vi.fn() },
  createModelPhotoSet: mocks.createModelPhotoSet,
  uploadModelPhoto: mocks.uploadModelPhoto,
}))

/** IntersectionObserver 假实现：记录被观察目标，测试中可手动触发「进入视口」 */
class FakeIntersectionObserver {
  static instances: FakeIntersectionObserver[] = []
  readonly targets = new Set<Element>()
  private readonly callback: IntersectionObserverCallback

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback
    FakeIntersectionObserver.instances.push(this)
  }

  observe(target: Element) {
    this.targets.add(target)
  }

  unobserve(target: Element) {
    this.targets.delete(target)
  }

  disconnect() {
    this.targets.clear()
  }

  /** 模拟所有被观察目标同时进入视口（用于触发缩略图懒加载） */
  fireAll() {
    const entries = [...this.targets].map((target) => ({
      target,
      isIntersecting: true,
    })) as unknown as IntersectionObserverEntry[]
    this.callback(entries, this as unknown as IntersectionObserver)
  }
}

/** 造出带 webkitRelativePath 的「文件夹内图片」File */
function makeFolderFiles(names: string[], folder = '写真集'): File[] {
  return names.map((name) => {
    const file = new File(['fake-image'], name, { type: 'image/jpeg' })
    Object.defineProperty(file, 'webkitRelativePath', {
      configurable: true,
      value: `${folder}/${name}`,
    })
    return file
  })
}

/** 触发隐藏 file input 的 change（模拟选中一个文件夹） */
async function selectFolder(wrapper: VueWrapper, files: File[]) {
  const input = wrapper.find('input[type="file"]')
  const el = input.element as HTMLInputElement
  Object.defineProperty(el, 'files', { configurable: true, value: files })
  await input.trigger('change')
  await nextTick()
}

/** 模拟用户从 a-select 选中人物（Arco 通过 update:modelValue 回写 v-model） */
async function selectModel(wrapper: VueWrapper, id: number | undefined) {
  const select = findModelSelect(wrapper)
  await select.vm.$emit('update:modelValue', id)
  await nextTick()
}

function findModelSelect(wrapper: VueWrapper) {
  const byName = wrapper.findComponent({ name: 'Select' })
  if (byName.exists()) return byName
  return wrapper.findComponent({ name: 'ASelect' })
}

async function mountView() {
  const wrapper = mount(ModelPhotoUploadView, {
    global: { plugins: [ArcoVue] },
  })
  await flushPromises() // 人物列表加载完成
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  FakeIntersectionObserver.instances = []
  mocks.fetchList.mockResolvedValue({
    items: [{ id: 3, name: '模特A', platform: 'other' }],
    total: 1,
    page: 1,
    size: 200,
  })
  mocks.createModelPhotoSet.mockResolvedValue({
    id: 9,
    name: '写真集',
    model_id: 3,
    photo_count: 2,
  })
  mocks.uploadModelPhoto.mockResolvedValue({
    id: 1,
    set_id: 9,
    file_path: '/storage/1.jpg',
  })

  vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver)
  // 强制走「缩略图生成失败 → 回退原图 objectURL」分支，便于断言 img 渲染
  vi.stubGlobal('createImageBitmap', vi.fn().mockRejectedValue(new Error('unsupported')))
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    writable: true,
    value: vi.fn(() => 'blob:test-thumb'),
  })
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    writable: true,
    value: vi.fn(),
  })
  // 测试环境兜底（happy-dom 若未实现则补桩）
  if (typeof globalThis.ResizeObserver === 'undefined') {
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    )
  }
  const cryptoObj = globalThis.crypto
  if (!cryptoObj || typeof cryptoObj.randomUUID !== 'function') {
    vi.stubGlobal('crypto', { randomUUID: () => `test-${Math.random().toString(36).slice(2, 10)}` })
  }
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ModelPhotoUploadView 添加模特照片页', () => {
  it('选择人物后点击「开始导入」应触发导入流程，不再误报「请先选择人物」', async () => {
    const warningSpy = vi.spyOn(Message, 'warning').mockImplementation(() => ({ close: () => {} }))
    const wrapper = await mountView()

    await selectModel(wrapper, 3)
    await selectFolder(wrapper, makeFolderFiles(['01.jpg', '02.jpg']))

    const importBtn = wrapper.findAll('button').find((b) => b.text().includes('开始导入'))
    expect(importBtn).toBeTruthy()
    if (importBtn) await importBtn.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(warningSpy).not.toHaveBeenCalledWith('请先选择人物')
    // 人物 id 正确传入照片组创建与照片上传
    expect(mocks.createModelPhotoSet).toHaveBeenCalledWith(3, '写真集')
    expect(mocks.uploadModelPhoto).toHaveBeenCalledTimes(2)
  })

  it('未选择人物时点击「开始导入」应提示「请先选择人物」', async () => {
    const warningSpy = vi.spyOn(Message, 'warning').mockImplementation(() => ({ close: () => {} }))
    const wrapper = await mountView()

    await selectFolder(wrapper, makeFolderFiles(['01.jpg']))
    const importBtn = wrapper.findAll('button').find((b) => b.text().includes('开始导入'))
    expect(importBtn).toBeTruthy()
    if (importBtn) await importBtn.trigger('click')
    await nextTick()

    expect(warningSpy).toHaveBeenCalledWith('请先选择人物')
    expect(mocks.createModelPhotoSet).not.toHaveBeenCalled()
  })

  it('选择文件夹后卡片进入视口才生成缩略图，生成后图片正常显示（不空白）', async () => {
    const wrapper = await mountView()
    await selectFolder(wrapper, makeFolderFiles(['01.jpg', '02.jpg', '03.jpg']))

    // 懒加载：进入视口前所有卡片均为占位，无 img
    expect(wrapper.findAll('.thumb-cell img').length).toBe(0)
    expect(wrapper.findAll('.thumb-cell .thumb-placeholder').length).toBe(3)

    // 第一个观察器即缩略图观察器：触发全部卡片进入视口
    const thumbObserver = FakeIntersectionObserver.instances[0]
    expect(thumbObserver).toBeTruthy()
    expect(thumbObserver.targets.size).toBe(3)
    thumbObserver.fireAll()
    await flushPromises()
    await nextTick()

    // 缩略图生成（回退 objectURL）后 img 渲染，src 指向 objectURL
    const imgs = wrapper.findAll('.thumb-cell img')
    expect(imgs.length).toBe(3)
    for (const img of imgs) {
      expect(img.attributes('src')).toBe('blob:test-thumb')
    }
  })

  it('取消人物选择后清空待导入列表（验收步骤 7）', async () => {
    const wrapper = await mountView()
    await selectModel(wrapper, 3)
    await selectFolder(wrapper, makeFolderFiles(['01.jpg', '02.jpg']))
    expect(wrapper.find('.preview-grid').exists()).toBe(true)

    // Arco allow-clear 清空时以 undefined 回写 modelValue
    await selectModel(wrapper, undefined)
    await nextTick()

    expect(wrapper.find('.preview-grid').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('已选 2 张照片')
  })
})
