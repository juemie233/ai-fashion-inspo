/**
 * 模特人脸特征注册（`useModelFaceRegister`）的测试。
 *
 * 人脸特征属于生物特征数据，注册会**覆盖旧特征**，所以行为必须可预期：
 * - 切换模特 id 时先清空状态再加载，防止把上一个模特的人脸状态显示在新模特页；
 * - 状态加载失败不阻塞详情页（静默）；
 * - 注册成功必须回填 `registered/model_id/updated_at`，并在提示里说明实际用了多少张照片、
 *   跳过了几条警告；注册失败要透出后端原因且 busy 复位。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, nextTick, ref } from 'vue'

const mocks = vi.hoisted(() => ({
  fetchFaceStatus: vi.fn(),
  registerFace: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/persons', () => ({
  modelsApi: {
    fetchFaceStatus: mocks.fetchFaceStatus,
    registerFace: mocks.registerFace,
  },
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useModelFaceRegister } from '../useModelFaceRegister'

type FaceApi = Parameters<typeof useModelFaceRegister>[0]['api']

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

async function flush() {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
}

function setup(initialId = 1) {
  const personId = ref(initialId)
  const api = computed(() => ({
    fetchFaceStatus: mocks.fetchFaceStatus,
    registerFace: mocks.registerFace,
  })) as unknown as FaceApi
  const reg = useModelFaceRegister({ personId, api })
  return { reg, personId }
}

describe('useModelFaceRegister', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchFaceStatus.mockResolvedValue({
      registered: true,
      model_id: 1,
      updated_at: '2026-11-01T10:00:00+00:00',
    })
    mocks.registerFace.mockResolvedValue({
      registered: true,
      model_id: 1,
      photos_used: 5,
      photos_total: 6,
      updated_at: '2026-11-02T09:00:00+00:00',
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('初始化即按当前模特 id 加载人脸状态', async () => {
    const { reg } = setup(7)

    expect(mocks.fetchFaceStatus).toHaveBeenCalledWith(7)
    await flush()

    expect(reg.modelFaceStatus.value).toEqual({
      registered: true,
      model_id: 1,
      updated_at: '2026-11-01T10:00:00+00:00',
    })
    expect(reg.modelFaceBusy.value).toBe(false)
  })

  it('状态加载失败静默：不提示、不阻塞，状态保持 null', async () => {
    mocks.fetchFaceStatus.mockRejectedValue(new Error('face service down'))
    const { reg } = setup()

    await flush()

    expect(reg.modelFaceStatus.value).toBeNull()
    expect(mocks.message.error).not.toHaveBeenCalled()
  })

  it('切换模特：先清空旧状态，再按新 id 重新加载', async () => {
    const pending = deferred<{ registered: boolean; model_id: number }>()
    mocks.fetchFaceStatus
      .mockResolvedValueOnce({ registered: true, model_id: 1, updated_at: null })
      .mockReturnValueOnce(pending.promise)
    const { reg, personId } = setup(1)
    await flush()
    expect(reg.modelFaceStatus.value?.model_id).toBe(1)

    personId.value = 2
    await nextTick()

    expect(reg.modelFaceStatus.value).toBeNull()
    expect(mocks.fetchFaceStatus).toHaveBeenLastCalledWith(2)

    pending.resolve({ registered: false, model_id: 2 })
    await flush()
    expect(reg.modelFaceStatus.value).toEqual({ registered: false, model_id: 2 })
  })

  it('注册成功：固定取 Top-5、回填状态、提示使用照片数', async () => {
    const pending = deferred<Record<string, unknown>>()
    mocks.registerFace.mockReturnValue(pending.promise)
    const { reg } = setup(3)
    await flush()

    const promise = reg.handleRegisterModelFace()

    expect(reg.modelFaceBusy.value).toBe(true)
    expect(mocks.registerFace).toHaveBeenCalledWith(3, 5)

    pending.resolve({
      registered: true,
      model_id: 9,
      photos_used: 5,
      photos_total: 7,
      updated_at: '2026-11-02T09:00:00+00:00',
    })
    await promise

    expect(reg.modelFaceStatus.value).toEqual({
      registered: true,
      model_id: 9,
      updated_at: '2026-11-02T09:00:00+00:00',
    })
    expect(mocks.message.success).toHaveBeenCalledWith('已从照片组注册人脸（使用 5 张高质量照片）')
    expect(reg.modelFaceBusy.value).toBe(false)
  })

  it('注册成功带照片跳过警告：提示里补上警告条数', async () => {
    mocks.registerFace.mockResolvedValue({
      registered: true,
      model_id: 9,
      photos_used: 4,
      photos_total: 6,
      warnings: ['照片 101 未检出人脸', '照片 102 人脸太小'],
      updated_at: null,
    })
    const { reg } = setup()
    await flush()

    await reg.handleRegisterModelFace()

    expect(mocks.message.success).toHaveBeenCalledWith(
      '已从照片组注册人脸（使用 4 张高质量照片，2 条照片跳过警告）',
    )
  })

  it('photos_used 缺失时提示按 0 张展示（不出现 undefined）', async () => {
    mocks.registerFace.mockResolvedValue({ registered: true, model_id: 9, updated_at: null })
    const { reg } = setup()
    await flush()

    await reg.handleRegisterModelFace()

    expect(mocks.message.success).toHaveBeenCalledWith('已从照片组注册人脸（使用 0 张高质量照片）')
  })

  it('注册失败：透出后端 detail、状态不变、busy 复位', async () => {
    mocks.registerFace.mockRejectedValue({
      response: { data: { detail: '照片组为空，无法注册人脸' } },
    })
    const { reg } = setup()
    await flush()

    await reg.handleRegisterModelFace()

    expect(mocks.message.error).toHaveBeenCalledWith('照片组为空，无法注册人脸')
    // 失败后保留原本已加载的状态，不能被清空或被失败结果覆盖
    expect(reg.modelFaceStatus.value?.model_id).toBe(1)
    expect(reg.modelFaceBusy.value).toBe(false)
  })

  it('注册失败且无后端文案时回退「注册失败」', async () => {
    mocks.registerFace.mockRejectedValue({})
    const { reg } = setup()
    await flush()

    await reg.handleRegisterModelFace()

    expect(mocks.message.error).toHaveBeenCalledWith('注册失败')
  })
})
