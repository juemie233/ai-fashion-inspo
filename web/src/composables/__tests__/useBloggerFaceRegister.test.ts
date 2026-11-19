/**
 * 博主人脸特征注册（`useBloggerFaceRegister`）的测试。
 *
 * 这是「上传照片 + 从已关联素材勾选」混合来源的注册表单，规则较多：
 * - 照片与素材**合计 1~5 张**，超限必须在本地拦下（不要等后端 422）；
 * - 注册成功要按 `photo_results` 逐张说明哪些被跳过、为什么，并把 `warnings` 附在提示里；
 * - 注册/注销成功都要清空选择、刷新人脸状态并通知父级（刷新素材瀑布流与统计）；
 * - 切换博主时重置全部选择状态，避免把上一个博主的勾选带过去。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, nextTick, ref } from 'vue'
import type { FileItem } from '@arco-design/web-vue'

const mocks = vi.hoisted(() => ({
  fetchFaceStatus: vi.fn(),
  registerFace: vi.fn(),
  unregisterFace: vi.fn(),
  fetchInspirations: vi.fn(),
  onChanged: vi.fn(),
  message: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/api/persons', () => ({
  bloggersApi: {
    fetchFaceStatus: mocks.fetchFaceStatus,
    registerFace: mocks.registerFace,
    unregisterFace: mocks.unregisterFace,
    fetchInspirations: mocks.fetchInspirations,
  },
}))

vi.mock('@arco-design/web-vue', async () => {
  const actual =
    await vi.importActual<typeof import('@arco-design/web-vue')>('@arco-design/web-vue')
  return { ...actual, Message: mocks.message }
})

import { useBloggerFaceRegister } from '../useBloggerFaceRegister'

type BloggerFaceApi = Parameters<typeof useBloggerFaceRegister>[0]['api']

async function flush() {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
}

function fileItem(name: string): FileItem {
  return { file: new File(['x'], name, { type: 'image/jpeg' }) } as FileItem
}

function inspiration(id: string) {
  return {
    inspiration_id: id,
    file_path: `images/${id}.webp`,
    thumbnail_path: `thumbnails/${id}.jpg`,
    media_type: 'image',
    confidence: 0.99,
    created_at: '2026-11-01T10:00:00+00:00',
  }
}

function setup(initialId = 1) {
  const personId = ref(initialId)
  const api = computed(() => ({
    fetchFaceStatus: mocks.fetchFaceStatus,
    registerFace: mocks.registerFace,
    unregisterFace: mocks.unregisterFace,
    fetchInspirations: mocks.fetchInspirations,
  })) as unknown as BloggerFaceApi
  const reg = useBloggerFaceRegister({ personId, api, onChanged: mocks.onChanged })
  return { reg, personId }
}

describe('useBloggerFaceRegister', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchFaceStatus.mockResolvedValue({
      registered: true,
      blogger_id: 1,
      updated_at: '2026-11-01T10:00:00+00:00',
    })
    mocks.registerFace.mockResolvedValue({
      registered: true,
      blogger_id: 1,
      photos_used: 4,
      photos_total: 5,
      photo_results: [],
      warnings: [],
      updated_at: '2026-11-02T09:00:00+00:00',
    })
    mocks.unregisterFace.mockResolvedValue({
      blogger_id: 1,
      face_detections_cleared: 2,
      inspirations_unlinked: 3,
    })
    mocks.fetchInspirations.mockResolvedValue({
      items: [inspiration('a1'), inspiration('a2')],
      total: 2,
    })
    mocks.onChanged.mockResolvedValue(undefined)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('初始化与素材选择', () => {
    it('初始化即加载人脸状态，默认停在上传照片 Tab（不加载素材）', async () => {
      const { reg } = setup(5)

      expect(mocks.fetchFaceStatus).toHaveBeenCalledWith(5)
      await flush()

      expect(reg.faceStatus.value?.registered).toBe(true)
      expect(reg.faceTab.value).toBe('upload')
      expect(mocks.fetchInspirations).not.toHaveBeenCalled()
    })

    it('切到「从素材选择」Tab 首次加载该博主素材；已有数据不重复加载', async () => {
      const { reg } = setup()
      await flush()

      reg.faceTab.value = 'inspiration'
      await nextTick()
      await flush()

      expect(mocks.fetchInspirations).toHaveBeenCalledWith(1, 1, 30)
      expect(reg.faceInspItems.value.map((i) => i.inspiration_id)).toEqual(['a1', 'a2'])
      expect(reg.faceInspTotal.value).toBe(2)
      expect(reg.faceInspPage.value).toBe(1)

      reg.faceTab.value = 'upload'
      await nextTick()
      reg.faceTab.value = 'inspiration'
      await nextTick()
      await flush()

      expect(mocks.fetchInspirations).toHaveBeenCalledTimes(1)
    })

    it('加载指定页码：页码与会话状态同步', async () => {
      const { reg } = setup()
      await flush()
      mocks.fetchInspirations.mockResolvedValueOnce({ items: [inspiration('b1')], total: 40 })

      await reg.loadFaceInspirations(2)

      expect(mocks.fetchInspirations).toHaveBeenLastCalledWith(1, 2, 30)
      expect(reg.faceInspPage.value).toBe(2)
      expect(reg.faceInspItems.value.map((i) => i.inspiration_id)).toEqual(['b1'])
    })

    it('加载素材失败：提示「加载素材失败」，loading 复位', async () => {
      mocks.fetchInspirations.mockRejectedValue(new Error('503'))
      const { reg } = setup()
      await flush()

      await reg.loadFaceInspirations(1)

      expect(mocks.message.error).toHaveBeenCalledWith('加载素材失败')
      expect(reg.faceInspLoading.value).toBe(false)
    })

    it('勾选/取消勾选素材（每次替换成新 Set，保证响应式）', async () => {
      const { reg } = setup()
      await flush()
      const before = reg.selectedFaceInspIds.value

      reg.toggleFaceInsp('a1')
      expect(reg.selectedFaceInspIds.value.has('a1')).toBe(true)
      expect(reg.selectedFaceInspIds.value).not.toBe(before)

      reg.toggleFaceInsp('a1')
      expect(reg.selectedFaceInspIds.value.has('a1')).toBe(false)
    })

    it('素材勾选上限 5 张，超出只警告不加入', async () => {
      const { reg } = setup()
      await flush()

      for (let i = 0; i < 5; i += 1) reg.toggleFaceInsp(`id-${i}`)
      expect(reg.selectedFaceInspIds.value.size).toBe(5)

      reg.toggleFaceInsp('id-5')

      expect(reg.selectedFaceInspIds.value.size).toBe(5)
      expect(mocks.message.warning).toHaveBeenCalledWith('照片与素材合计最多 5 张')
    })

    it('上限按「已上传照片 + 已勾选素材」合计计算', async () => {
      const { reg } = setup()
      await flush()
      reg.faceFileList.value = [fileItem('a.jpg'), fileItem('b.jpg')]

      reg.toggleFaceInsp('s1')
      reg.toggleFaceInsp('s2')
      reg.toggleFaceInsp('s3')
      expect(reg.selectedFaceInspIds.value.size).toBe(3) // 2 张照片 + 3 张素材 = 5

      reg.toggleFaceInsp('s4')

      expect(reg.selectedFaceInspIds.value.size).toBe(3)
      expect(mocks.message.warning).toHaveBeenCalledWith('照片与素材合计最多 5 张')
    })
  })

  describe('注册', () => {
    it('未选照片也没勾素材：警告且不发请求', async () => {
      const { reg } = setup()
      await flush()

      await reg.handleRegisterFace()

      expect(mocks.message.warning).toHaveBeenCalledWith('请选择照片或勾选素材（合计 1~5 张）')
      expect(mocks.registerFace).not.toHaveBeenCalled()
    })

    it('上传照片超过 5 张：本地拦下不发请求', async () => {
      const { reg } = setup()
      await flush()
      reg.faceFileList.value = Array.from({ length: 6 }, (_, i) => fileItem(`${i}.jpg`))

      await reg.handleRegisterFace()

      expect(mocks.message.warning).toHaveBeenCalledWith('照片与素材合计最多 5 张')
      expect(mocks.registerFace).not.toHaveBeenCalled()
    })

    it('照片 + 素材混合注册成功：提交文件与素材 ID、清空选择、刷新状态并通知父级', async () => {
      const { reg } = setup()
      await flush()
      reg.faceFileList.value = [fileItem('a.jpg'), fileItem('b.jpg')]
      reg.toggleFaceInsp('a1')
      reg.toggleFaceInsp('a2')

      await reg.handleRegisterFace()

      const [id, files, selectedIds] = mocks.registerFace.mock.calls[0] as [
        number,
        File[],
        string[],
      ]
      expect(id).toBe(1)
      expect(files).toHaveLength(2)
      expect(files[0]).toBeInstanceOf(File)
      expect(selectedIds).toEqual(['a1', 'a2'])

      expect(mocks.message.success).toHaveBeenCalledWith('人脸注册成功（4/5 张图片检出人脸）')
      expect(reg.faceFileList.value).toEqual([])
      expect(reg.selectedFaceInspIds.value.size).toBe(0)
      expect(mocks.fetchFaceStatus).toHaveBeenCalledTimes(2) // 初始化 + 注册后刷新
      expect(mocks.onChanged).toHaveBeenCalledTimes(1)
      expect(reg.faceUploading.value).toBe(false)
    })

    it('部分图片被跳过：用 warning 逐张说明原因与 warnings，展示 8 秒', async () => {
      mocks.registerFace.mockResolvedValue({
        registered: true,
        blogger_id: 1,
        photos_used: 3,
        photos_total: 5,
        photo_results: [
          { index: 1, source: 'inspiration', status: 'skipped', message: '素材文件缺失' },
          { index: 2, status: 'skipped', message: null },
          { index: 3, source: 'upload', status: 'used' },
        ],
        warnings: ['3 张图片未检出人脸'],
        updated_at: null,
      })
      const { reg } = setup()
      await flush()
      reg.toggleFaceInsp('a1')
      reg.toggleFaceInsp('a2')

      await reg.handleRegisterFace()

      expect(mocks.message.warning).toHaveBeenCalledWith({
        content:
          '注册成功（3/5 张图片检出人脸）；已跳过：第1张素材：素材文件缺失；第2张照片：未检出清晰人脸；3 张图片未检出人脸',
        duration: 8000,
      })
      expect(mocks.message.success).not.toHaveBeenCalled()
    })

    it('接口缺省 items/total 时按空列表处理，缺省 photos_* 时按 0 展示', async () => {
      mocks.fetchInspirations.mockResolvedValue({})
      mocks.registerFace.mockResolvedValue({ registered: true, blogger_id: 1 })
      const { reg } = setup()
      await flush()

      await reg.loadFaceInspirations(1)
      expect(reg.faceInspItems.value).toEqual([])
      expect(reg.faceInspTotal.value).toBe(0)

      reg.toggleFaceInsp('a1')
      await reg.handleRegisterFace()

      // photo_results / warnings / photos_used 都缺省 → 走成功分支，数字按 0
      expect(mocks.message.success).toHaveBeenCalledWith('人脸注册成功（0/0 张图片检出人脸）')
      expect(mocks.message.warning).not.toHaveBeenCalled()
    })

    it('注册失败：透出后端 detail，选择保留以便重试', async () => {
      mocks.registerFace.mockRejectedValue({ response: { data: { detail: '人脸服务不可用' } } })
      const { reg } = setup()
      await flush()
      reg.faceFileList.value = [fileItem('a.jpg')]

      await reg.handleRegisterFace()

      expect(mocks.message.error).toHaveBeenCalledWith('人脸服务不可用')
      expect(reg.faceFileList.value).toHaveLength(1)
      expect(reg.faceUploading.value).toBe(false)
      expect(mocks.onChanged).not.toHaveBeenCalled()
    })

    it('注册失败且无后端文案时回退「人脸注册失败」', async () => {
      mocks.registerFace.mockRejectedValue({})
      const { reg } = setup()
      await flush()
      reg.toggleFaceInsp('a1')

      await reg.handleRegisterFace()

      expect(mocks.message.error).toHaveBeenCalledWith('人脸注册失败')
    })
  })

  describe('注销', () => {
    it('注销成功：提示回退计数、清空选择、刷新状态并通知父级', async () => {
      const { reg } = setup()
      await flush()
      reg.faceFileList.value = [fileItem('a.jpg')]
      reg.toggleFaceInsp('a1')

      await reg.handleUnregisterFace()

      expect(mocks.unregisterFace).toHaveBeenCalledWith(1)
      expect(mocks.message.success).toHaveBeenCalledWith(
        '已注销人脸：回退人脸匹配 2 条、解除素材归属 3 条',
      )
      expect(reg.faceFileList.value).toEqual([])
      expect(reg.selectedFaceInspIds.value.size).toBe(0)
      expect(mocks.fetchFaceStatus).toHaveBeenCalledTimes(2)
      expect(mocks.onChanged).toHaveBeenCalledTimes(1)
      expect(reg.faceUnregistering.value).toBe(false)
    })

    it('注销失败：透出文案、busy 复位', async () => {
      mocks.unregisterFace.mockRejectedValue({ response: { data: { detail: '博主不存在' } } })
      const { reg } = setup()
      await flush()

      await reg.handleUnregisterFace()

      expect(mocks.message.error).toHaveBeenCalledWith('博主不存在')
      expect(reg.faceUnregistering.value).toBe(false)
    })
  })

  describe('切换博主', () => {
    it('切换博主：重置 Tab/照片/素材/勾选/页码，并按新 id 重新加载状态', async () => {
      const { reg, personId } = setup(1)
      await flush()
      reg.faceTab.value = 'inspiration'
      await nextTick()
      await flush()
      reg.faceFileList.value = [fileItem('a.jpg')]
      reg.toggleFaceInsp('a1')
      expect(reg.faceInspItems.value).toHaveLength(2)

      personId.value = 2
      await nextTick()

      expect(reg.faceTab.value).toBe('upload')
      expect(reg.faceFileList.value).toEqual([])
      expect(reg.faceInspItems.value).toEqual([])
      expect(reg.faceInspPage.value).toBe(1)
      expect(reg.selectedFaceInspIds.value.size).toBe(0)
      expect(mocks.fetchFaceStatus).toHaveBeenLastCalledWith(2)
    })
  })
})
