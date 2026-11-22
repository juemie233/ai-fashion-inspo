/**
 * API 封装层补测（批次 B）：inspirations / search / personBase / persons /
 * collections / generate。
 *
 * 这些模块是前端唯一与后端契约对齐的位置：方法（GET/POST/PATCH/PUT/DELETE）、
 * URL 路径段、query 参数名、请求体字段、FormData 的 multipart 头，任何一处写错
 * 都是线上 422/404，而组件测试几乎发现不了。本文件逐条锁定「方法 + URL + 参数/body」，
 * 并覆盖三类容易回归的边界：
 * 1. 响应解包与兜底（batchFavorite 返回 data.updated、fetchFavoriteHistory 只返回 items、
 *    fetchSuggestions 兼容 items 缺失）；
 * 2. 条件请求体（batchTrash/moveToTrash 只在有 reason 或非手动来源时才带 reason/source）；
 * 3. 下载类接口的文件名解析与回退（exportRuns 无 content-disposition 时用时间戳名）。
 *
 * 封装层不吞异常：错误统一交给调用方决定提示方式，这里只验证 reject 原样透传。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  warnItems: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  default: {
    get: mocks.get,
    post: mocks.post,
    put: mocks.put,
    patch: mocks.patch,
    delete: mocks.delete,
  },
}))

// inspirations.faceDetectInspiration 会用 warnItems 校验响应形状，这里只验证「有没有校验」，
// 形状判断本身由 utils/__tests__/apiGuard.test.ts 覆盖。
vi.mock('@/utils/apiGuard', () => ({ warnItems: mocks.warnItems }))

import * as searchApi from '@/api/search'
import * as collectionsApi from '@/api/collections'
import * as generateApi from '@/api/generate'
import * as inspirationsApi from '@/api/inspirations'
import { bloggersApi, modelsApi } from '@/api/persons'

type AnyMock = { mock: { calls: unknown[][] } }

/** 取某个 mock 最近一次调用的参数数组 */
function lastArgs(mockFn: AnyMock): unknown[] {
  return mockFn.mock.calls.at(-1) ?? []
}

/** 最近一次 POST 的 FormData */
function lastPostFormData(): FormData {
  return lastArgs(mocks.post)[1] as FormData
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.get.mockResolvedValue({ data: {} })
  mocks.post.mockResolvedValue({ data: {} })
  mocks.put.mockResolvedValue({ data: {} })
  mocks.patch.mockResolvedValue({ data: {} })
  mocks.delete.mockResolvedValue({ data: {} })
  mocks.warnItems.mockReturnValue(undefined)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('search API', () => {
  it('多维度搜索：GET /search，整个 query 对象原样作为 params', async () => {
    mocks.get.mockResolvedValueOnce({ data: { items: [], total: 0 } })
    const query = {
      include_tags: '白裙,通勤',
      exclude_tags: '睡衣',
      keyword: '法式',
      media_type: 'image',
      is_favorite: true,
      rating_min: 4,
      sort: 'newest',
      page: 2,
      size: 24,
    }

    await expect(searchApi.searchInspirations(query)).resolves.toEqual({ items: [], total: 0 })
    expect(mocks.get).toHaveBeenCalledWith('/search', { params: query })
  })

  it('搜索建议：GET /search/suggestions?q=；缺 items 字段时回退原始响应', async () => {
    const items = [{ name: '白裙', usage_count: 3 }]
    mocks.get.mockResolvedValueOnce({ data: { items } })
    await expect(searchApi.fetchSuggestions('白')).resolves.toEqual(items)
    expect(mocks.get).toHaveBeenLastCalledWith('/search/suggestions', { params: { q: '白' } })

    // 旧后端/异常响应直接返回数组：fetchSuggestions 应回退到 data 本身
    mocks.get.mockResolvedValueOnce({ data: items })
    await expect(searchApi.fetchSuggestions('裙')).resolves.toEqual(items)
  })

  it('标签共现：GET /search/tag-cooccurrence?tag_name=', async () => {
    mocks.get.mockResolvedValueOnce({ data: { tag: '白裙', related: [] } })

    await searchApi.fetchCooccurrence('白裙')

    expect(mocks.get).toHaveBeenCalledWith('/search/tag-cooccurrence', {
      params: { tag_name: '白裙' },
    })
  })

  it('文本语义搜索：POST /search/vector 的 multipart 字段 text/top_k（默认 20）', async () => {
    mocks.post.mockResolvedValueOnce({ data: { items: [], total: 0 } })
    await searchApi.vectorSearchText('通勤白衬衫')
    let form = lastPostFormData()
    expect(form.get('text')).toBe('通勤白衬衫')
    expect(form.get('top_k')).toBe('20')
    expect(lastArgs(mocks.post)[0]).toBe('/search/vector')
    expect(lastArgs(mocks.post)[2]).toEqual({
      headers: { 'Content-Type': 'multipart/form-data' },
    })

    await searchApi.vectorSearchText('度假风', 8)
    form = lastPostFormData()
    expect(form.get('top_k')).toBe('8')
  })

  it('以图搜图：POST /search/vector 上传 file 与 top_k', async () => {
    mocks.post.mockResolvedValueOnce({ data: { items: [], total: 0 } })
    const file = new File(['img'], 'look.jpg', { type: 'image/jpeg' })

    await searchApi.vectorSearchImage(file, 5)

    const form = lastPostFormData()
    expect(form.get('file')).toBeInstanceOf(File)
    expect(form.get('top_k')).toBe('5')
    expect(mocks.post).toHaveBeenCalledWith('/search/vector', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  })

  it('相似推荐：GET /search/similar/{id}?top_k=（默认 10）', async () => {
    await searchApi.fetchSimilar('ins-1')
    expect(mocks.get).toHaveBeenCalledWith('/search/similar/ins-1', { params: { top_k: 10 } })

    await searchApi.fetchSimilar('ins-2', 24)
    expect(mocks.get).toHaveBeenLastCalledWith('/search/similar/ins-2', {
      params: { top_k: 24 },
    })
  })
})

describe('personBase 共享工厂（经 bloggersApi / modelsApi 调用）', () => {
  it('列表/详情/统计：GET /{kind}、/{kind}/{id}、/{kind}/stats', async () => {
    await bloggersApi.fetchList()
    expect(mocks.get).toHaveBeenCalledWith('/bloggers', { params: {} })

    const params = { page: 2, size: 20, search: '小美', platform: 'xiaohongshu' }
    await bloggersApi.fetchList(params)
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers', { params })

    await bloggersApi.fetchDetail(7)
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/7')

    await bloggersApi.fetchStats()
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/stats')

    // 模特接口复用同一工厂，base 必须换成 /models
    await modelsApi.fetchList({ page: 1 })
    expect(mocks.get).toHaveBeenLastCalledWith('/models', { params: { page: 1 } })
    await modelsApi.fetchDetail(3)
    expect(mocks.get).toHaveBeenLastCalledWith('/models/3')
  })

  it('创建/更新/删除：POST、PATCH、DELETE（返回体解包与无返回）', async () => {
    mocks.post.mockResolvedValueOnce({ data: { id: 1, name: '新博主' } })
    await expect(bloggersApi.create({ name: '新博主' })).resolves.toEqual({
      id: 1,
      name: '新博主',
    })
    expect(mocks.post).toHaveBeenCalledWith('/bloggers', { name: '新博主' })

    await bloggersApi.update(1, { name: '改名' })
    expect(mocks.patch).toHaveBeenCalledWith('/bloggers/1', { name: '改名' })

    await expect(bloggersApi.remove(1)).resolves.toBeUndefined()
    expect(mocks.delete).toHaveBeenCalledWith('/bloggers/1')

    await modelsApi.remove(2)
    expect(mocks.delete).toHaveBeenLastCalledWith('/models/2')
  })

  it('素材列表：GET /{kind}/{id}/inspirations，默认分页 + 筛选原样透传', async () => {
    await bloggersApi.fetchInspirations(5)
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/5/inspirations', {
      params: { page: 1, size: 20, sort: undefined },
    })

    await bloggersApi.fetchInspirations(5, 3, 12, 'newest', { media_type: 'video' })
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/5/inspirations', {
      params: { page: 3, size: 12, sort: 'newest', media_type: 'video' },
    })
  })

  it('作品分组/随机/洞察/相似/人脸候选：子路径与默认参数', async () => {
    await bloggersApi.fetchWorks(5)
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/5/works', {
      params: { page: 1, size: 12 },
    })
    await bloggersApi.fetchWorks(5, 2, 30)
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/5/works', {
      params: { page: 2, size: 30 },
    })

    await bloggersApi.fetchRandom(5)
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/5/random', {
      params: { size: 1 },
    })
    await bloggersApi.fetchRandom(5, 6, { quality_status: 'approved' })
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/5/random', {
      params: { size: 6, quality_status: 'approved' },
    })

    await bloggersApi.fetchInsights(5)
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/5/insights')

    await bloggersApi.fetchSimilar(5)
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/5/similar', {
      params: { limit: 6 },
    })
    await bloggersApi.fetchSimilar(5, 12)
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/5/similar', {
      params: { limit: 12 },
    })

    await bloggersApi.fetchFaceCandidates(5)
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/5/face-candidates')
  })

  it('榜单/IP 统计/名称建议/生成简介：默认 limit 与参数名', async () => {
    await bloggersApi.fetchTop()
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/top', { params: { limit: 20 } })
    await bloggersApi.fetchTop(5)
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/top', { params: { limit: 5 } })

    await bloggersApi.fetchIpStats()
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/ip-stats', {
      params: { limit: 30 },
    })

    await bloggersApi.suggest('小美')
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/suggestions', {
      params: { name: '小美' },
    })

    mocks.post.mockResolvedValueOnce({ data: { bio: '简介' } })
    await expect(bloggersApi.generateBio(5)).resolves.toEqual({ bio: '简介' })
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/5/generate-bio')
  })

  it('素材-人物关联：POST /inspirations/{id}/{kind} 与 DELETE 单条', async () => {
    mocks.post.mockResolvedValueOnce({ data: { added: [], count: 1 } })
    await expect(bloggersApi.link('ins-1', [2, 3])).resolves.toEqual({
      added: [],
      count: 1,
    })
    expect(mocks.post).toHaveBeenLastCalledWith('/inspirations/ins-1/bloggers', {
      person_ids: [2, 3],
    })

    mocks.delete.mockResolvedValueOnce({ data: { removed: 1 } })
    await expect(bloggersApi.unlink('ins-1', 2)).resolves.toEqual({ removed: 1 })
    expect(mocks.delete).toHaveBeenLastCalledWith('/inspirations/ins-1/bloggers/2')
  })
})

describe('persons bloggersApi 扩展能力', () => {
  it('资料补全与抖音主页解析：POST 无 body，各自放宽超时', async () => {
    await bloggersApi.enrichProfile(3)
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/3/enrich-profile', undefined, {
      timeout: 30000,
    })

    await bloggersApi.resolveDouyinProfile(3)
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/3/resolve-douyin-profile', undefined, {
      timeout: 200000,
    })
  })

  it('人物组：绑定/解绑/设主账号/查询', async () => {
    const body = { blogger_id: 3, target_blogger_id: 9 }
    await bloggersApi.linkGroup(body)
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/groups/link', body)

    await bloggersApi.unlinkGroup(3)
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/groups/unlink', {
      blogger_id: 3,
    })

    await bloggersApi.setPrimaryGroup(8, 3)
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/groups/8/set-primary', {
      blogger_id: 3,
    })

    await bloggersApi.fetchGroupInfo(8)
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/groups/8')
  })

  it('纳入追踪与批量操作：promote / batch/promote / batch/delete', async () => {
    await bloggersApi.promote(4)
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/4/promote')

    await bloggersApi.batchPromote([1, 2])
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/batch/promote', { ids: [1, 2] })

    await bloggersApi.batchDelete([3, 4])
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/batch/delete', { ids: [3, 4] })
  })

  it('头像：覆盖率 / 批量清除（ids 缺省=全部）/ 同名建议', async () => {
    await bloggersApi.avatarCoverage()
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/avatars/coverage')

    await bloggersApi.clearAvatars([1, 2])
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/avatars/clear', { ids: [1, 2] })

    await bloggersApi.clearAvatars()
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/avatars/clear', {
      ids: undefined,
    })

    await bloggersApi.duplicateNames()
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/duplicate-names')
  })

  it('人脸注册：FormData 多文件 + 可选 inspiration_ids JSON + multipart 头', async () => {
    mocks.post.mockResolvedValueOnce({ data: { registered: true, blogger_id: 3 } })
    const files = [new File(['a'], 'a.jpg'), new File(['b'], 'b.jpg')]

    await bloggersApi.registerFace(3, files, ['ins-1', 'ins-2'])

    const form = lastPostFormData()
    expect(form.getAll('files')).toHaveLength(2)
    expect(form.get('inspiration_ids')).toBe('["ins-1","ins-2"]')
    expect(lastArgs(mocks.post)[0]).toBe('/bloggers/3/face')
    expect(lastArgs(mocks.post)[2]).toEqual({
      headers: { 'Content-Type': 'multipart/form-data' },
    })

    // 不传已关联素材时不应出现空 inspiration_ids 字段
    await bloggersApi.registerFace(3, files)
    expect(lastPostFormData().has('inspiration_ids')).toBe(false)
  })

  it('人脸状态/注销、头像设置（素材或上传二选一）/清除、绑定人脸', async () => {
    await bloggersApi.fetchFaceStatus(3)
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/3/face')

    await bloggersApi.unregisterFace(3)
    expect(mocks.delete).toHaveBeenLastCalledWith('/bloggers/3/face')

    await bloggersApi.setAvatar(3, { file: new File(['x'], 'avatar.jpg') })
    let form = lastPostFormData()
    expect(form.get('file')).toBeInstanceOf(File)
    expect(form.has('inspiration_id')).toBe(false)
    expect(lastArgs(mocks.post)[0]).toBe('/bloggers/3/avatar')

    await bloggersApi.setAvatar(3, { inspirationId: 'ins-9' })
    form = lastPostFormData()
    expect(form.get('inspiration_id')).toBe('ins-9')
    expect(form.has('file')).toBe(false)

    await bloggersApi.clearAvatar(3)
    expect(mocks.delete).toHaveBeenLastCalledWith('/bloggers/3/avatar')

    await bloggersApi.bindFace(3, 'ins-9')
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/3/bind-face/ins-9')
  })

  it('解绑素材：缺省传 null（清空全部），传数组只解绑指定素材', async () => {
    await bloggersApi.unbindInspirations(3)
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/3/unbind-inspirations', {
      inspiration_ids: null,
    })

    await bloggersApi.unbindInspirations(3, ['ins-1'])
    expect(mocks.post).toHaveBeenLastCalledWith('/bloggers/3/unbind-inspirations', {
      inspiration_ids: ['ins-1'],
    })
  })

  it('人物优先评分队列与随机未评分图：固定参数', async () => {
    await bloggersApi.fetchRatingPriority()
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/rating-priority', {
      params: { limit: 1000 },
    })
    await bloggersApi.fetchRatingPriority(50)
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/rating-priority', {
      params: { limit: 50 },
    })

    await bloggersApi.fetchRandomUnratedImage(7)
    expect(mocks.get).toHaveBeenLastCalledWith('/bloggers/7/random', {
      params: { unrated: true, media_type: 'image', size: 1 },
    })
  })
})

describe('collections API', () => {
  it('合集列表：GET /collections；创建 POST /collections', async () => {
    mocks.get.mockResolvedValueOnce({ data: [{ id: 1 }] })
    await expect(collectionsApi.fetchCollections()).resolves.toEqual([{ id: 1 }])
    expect(mocks.get).toHaveBeenCalledWith('/collections')

    const payload = {
      name: '通勤合集',
      description: null,
      parent_id: 1,
      query_json: { keyword: '通勤' },
    }
    await collectionsApi.createCollection(payload)
    expect(mocks.post).toHaveBeenCalledWith('/collections', payload)
  })

  it('更新/删除：PATCH、DELETE /collections/{id}', async () => {
    await collectionsApi.updateCollection(5, { name: '新名', cover_inspiration_id: 'ins-1' })
    expect(mocks.patch).toHaveBeenCalledWith('/collections/5', {
      name: '新名',
      cover_inspiration_id: 'ins-1',
    })

    await expect(collectionsApi.deleteCollection(5)).resolves.toBeUndefined()
    expect(mocks.delete).toHaveBeenCalledWith('/collections/5')
  })

  it('合集内容：GET /collections/{id}/inspirations，params 原样透传', async () => {
    await collectionsApi.fetchCollectionInspirations(5)
    expect(mocks.get).toHaveBeenLastCalledWith('/collections/5/inspirations', {
      params: {},
    })

    const params = { page: 2, page_size: 50, q: '白裙', media_type: 'image' as const }
    await collectionsApi.fetchCollectionInspirations(5, params)
    expect(mocks.get).toHaveBeenLastCalledWith('/collections/5/inspirations', { params })
  })

  it('加入/移出素材：POST body 与 DELETE data 里的 inspiration_ids', async () => {
    mocks.post.mockResolvedValueOnce({ data: { added: 2 } })
    await expect(collectionsApi.addToCollection(5, ['a', 'b'])).resolves.toEqual({ added: 2 })
    expect(mocks.post).toHaveBeenCalledWith('/collections/5/inspirations', {
      inspiration_ids: ['a', 'b'],
    })

    mocks.delete.mockResolvedValueOnce({ data: { removed: 1 } })
    await expect(collectionsApi.removeFromCollection(5, ['a'])).resolves.toEqual({
      removed: 1,
    })
    expect(mocks.delete).toHaveBeenCalledWith('/collections/5/inspirations', {
      data: { inspiration_ids: ['a'] },
    })
  })

  it('合集内排序与合集排序：PATCH 的 ordered_ids', async () => {
    await expect(collectionsApi.reorderCollectionItems(5, ['a', 'b'])).resolves.toBeUndefined()
    expect(mocks.patch).toHaveBeenCalledWith('/collections/5/items/order', {
      ordered_ids: ['a', 'b'],
    })

    await expect(collectionsApi.reorderCollections([3, 1])).resolves.toBeUndefined()
    expect(mocks.patch).toHaveBeenLastCalledWith('/collections/order', {
      ordered_ids: [3, 1],
    })
  })

  it('智能合集转手动：POST /collections/{id}/solidify', async () => {
    mocks.post.mockResolvedValueOnce({ data: { id: 5, kind: 'manual' } })
    await expect(collectionsApi.solidifyCollection(5)).resolves.toEqual({
      id: 5,
      kind: 'manual',
    })
    expect(mocks.post).toHaveBeenCalledWith('/collections/5/solidify')
  })

  it('批量整理：移动/复制与合并的 body 字段', async () => {
    await collectionsApi.transferCollectionItems(3, ['a', 'b'], [7, 9], 'move')
    expect(mocks.post).toHaveBeenLastCalledWith('/collections/3/inspirations/transfer', {
      ids: ['a', 'b'],
      target_ids: [7, 9],
      mode: 'move',
    })

    await collectionsApi.mergeCollections(9, [3, 4])
    expect(mocks.post).toHaveBeenLastCalledWith('/collections/9/merge', {
      source_ids: [3, 4],
    })
  })

  it('导出与 AI 整理：POST 放宽超时（打包/本地模型远慢于默认 30s）', async () => {
    await collectionsApi.exportCollection(5, 'zip')
    expect(mocks.post).toHaveBeenLastCalledWith(
      '/collections/5/exports',
      { format: 'zip' },
      { timeout: 600000 },
    )

    await collectionsApi.aiSummarizeCollection(5)
    expect(mocks.post).toHaveBeenLastCalledWith('/collections/5/ai-summary', undefined, {
      timeout: 180000,
    })
  })
})

describe('generate API', () => {
  it('状态/批次详情/取消/丢弃：方法与路径', async () => {
    await generateApi.fetchStatus()
    expect(mocks.get).toHaveBeenLastCalledWith('/generate/status')

    await generateApi.fetchRun(9)
    expect(mocks.get).toHaveBeenLastCalledWith('/generate/runs/9')

    // 取消接口没有 body，也不能多传 config（后端按 POST + 路径识别）
    await expect(generateApi.cancelRun(9)).resolves.toBeUndefined()
    expect(lastArgs(mocks.post)).toEqual(['/generate/runs/9/cancel'])

    mocks.delete.mockResolvedValueOnce({ data: { removed_files: 3 } })
    await expect(generateApi.discardRun(9)).resolves.toEqual({ removed_files: 3 })
    expect(mocks.delete).toHaveBeenLastCalledWith('/generate/runs/9')
  })

  it('创建批次 / 确认入库 / 提示词预览：body 字段', async () => {
    const payload = { prompt: '通勤白衬衫', kind: 't2i' as const, count: 4 }
    mocks.post.mockResolvedValueOnce({ data: { run_id: 1, task_id: 2 } })
    await expect(generateApi.createRun(payload)).resolves.toEqual({ run_id: 1, task_id: 2 })
    expect(mocks.post).toHaveBeenLastCalledWith('/generate/runs', payload)

    mocks.post.mockResolvedValueOnce({ data: { imported: 2, skipped: 1, ids: ['a', 'b'] } })
    await generateApi.confirmImport(9, ['a.jpg', 'b.jpg'])
    expect(mocks.post).toHaveBeenLastCalledWith('/generate/runs/9/confirm', {
      filenames: ['a.jpg', 'b.jpg'],
    })

    await generateApi.previewPrompt(['ins-1', 'ins-2'])
    expect(mocks.post).toHaveBeenLastCalledWith('/generate/prompt/preview', {
      inspiration_ids: ['ins-1', 'ins-2'],
    })
  })

  it('生成历史：数字参数按旧 limit 兼容，对象参数原样作为 params', async () => {
    await generateApi.listRuns()
    expect(mocks.get).toHaveBeenLastCalledWith('/generate/runs', { params: { limit: 20 } })

    await generateApi.listRuns(50)
    expect(mocks.get).toHaveBeenLastCalledWith('/generate/runs', { params: { limit: 50 } })

    const query = { page: 2, size: 10, q: '白裙', favorite: true }
    await generateApi.listRuns(query)
    expect(mocks.get).toHaveBeenLastCalledWith('/generate/runs', { params: query })
  })

  it('候选图地址：文件名逐段编码，width>0 才拼服务端缩略图参数', () => {
    const name = 'basename with space#1.jpg'
    const encoded = encodeURIComponent(name)

    expect(generateApi.candidateUrl(9, name)).toBe(`/api/generate/runs/9/candidates/${encoded}`)
    expect(generateApi.candidateUrl(9, name, 384)).toBe(
      `/api/generate/runs/9/candidates/${encoded}?w=384`,
    )
    // width=0 视同缺省（原图）
    expect(generateApi.candidateUrl(9, name, 0)).toBe(`/api/generate/runs/9/candidates/${encoded}`)
  })

  it('批次标注/统计/重试/换 seed 重出：方法、body 与 params', async () => {
    const patch = { favorite: true, title: '通勤' }
    await generateApi.updateRunMeta(9, patch)
    expect(mocks.patch).toHaveBeenLastCalledWith('/generate/runs/9', patch)

    await generateApi.fetchRunStats()
    expect(mocks.get).toHaveBeenLastCalledWith('/generate/runs/stats', {
      params: { days: 30 },
    })
    await generateApi.fetchRunStats(7)
    expect(mocks.get).toHaveBeenLastCalledWith('/generate/runs/stats', {
      params: { days: 7 },
    })

    await generateApi.retryRun(9)
    expect(lastArgs(mocks.post)).toEqual([
      '/generate/runs/9/retry',
      null,
      {
        params: { seed: 0 },
      },
    ])
    await generateApi.retryRun(9, 12345)
    expect(lastArgs(mocks.post)).toEqual([
      '/generate/runs/9/retry',
      null,
      {
        params: { seed: 12345 },
      },
    ])

    await generateApi.regenerateCandidate(9, 'a.jpg', 42)
    expect(lastArgs(mocks.post)).toEqual([
      '/generate/runs/9/regenerate',
      null,
      {
        params: { filename: 'a.jpg', seed: 42 },
      },
    ])
  })

  it('自动起标题：POST /generate/runs/{id}/auto-title，返回建议与来源', async () => {
    mocks.post.mockResolvedValueOnce({ data: { title: '通勤白衬衫', source: 'ai' } })

    await expect(generateApi.autoTitleRun(9)).resolves.toEqual({
      title: '通勤白衬衫',
      source: 'ai',
    })
    expect(mocks.post).toHaveBeenLastCalledWith('/generate/runs/9/auto-title')
  })

  it('导出历史：无 content-disposition 时回退带时间戳的文件名，并触发下载', async () => {
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:mock'),
      revokeObjectURL: vi.fn(),
    })
    const anchors: HTMLAnchorElement[] = []
    const realCreate = document.createElement.bind(document)
    const createSpy = vi.spyOn(document, 'createElement')
    createSpy.mockImplementation(((tag: string) => {
      const el = realCreate(tag)
      if (tag === 'a') anchors.push(el as HTMLAnchorElement)
      return el
    }) as never)
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    mocks.get.mockResolvedValueOnce({ data: new Blob(['x']), headers: {} })

    await generateApi.exportRuns('json', { q: '白裙', page: 2, size: 20 })

    expect(lastArgs(mocks.get)[0]).toBe('/generate/runs/export')
    expect(lastArgs(mocks.get)[1]).toEqual({
      params: { fmt: 'json', q: '白裙' },
      responseType: 'blob',
    })
    expect(anchors[0]?.download).toMatch(/^generate-history-\d+\.json$/)
    expect(click).toHaveBeenCalled()

    createSpy.mockRestore()
    click.mockRestore()
  })
})

describe('inspirations API', () => {
  it('文件 URL：逐段编码，兼容空格/#/?/中文', () => {
    expect(inspirationsApi.getFileUrl('storage/images/a.jpg')).toBe(
      '/api/files/storage/images/a.jpg',
    )
    expect(inspirationsApi.getFileUrl('storage/white dress#1.png')).toBe(
      '/api/files/storage/white%20dress%231.png',
    )
    expect(inspirationsApi.getFileUrl('storage/中文/图?.jpg')).toBe(
      `/api/files/storage/${encodeURIComponent('中文')}/${encodeURIComponent('图?.jpg')}`,
    )
  })

  it('手动打标/批量打标/纠错反馈：body 字段与默认值', async () => {
    mocks.post.mockResolvedValueOnce({ data: { added: [], count: 0 } })
    await inspirationsApi.addTagsToInspiration('i1', ['白裙'])
    expect(mocks.post).toHaveBeenLastCalledWith('/inspirations/i1/tags', {
      names: ['白裙'],
      category: 'outfit',
      source: 'manual',
      strict_category: false,
    })

    await inspirationsApi.addTagsToInspiration('i1', ['通勤'], 'scene', 'ai', true)
    expect(mocks.post).toHaveBeenLastCalledWith('/inspirations/i1/tags', {
      names: ['通勤'],
      category: 'scene',
      source: 'ai',
      strict_category: true,
    })

    await inspirationsApi.batchAddTagsToInspirations(['i1', 'i2'], ['白裙'])
    expect(mocks.post).toHaveBeenLastCalledWith('/inspirations/batch-tags', {
      inspiration_ids: ['i1', 'i2'],
      names: ['白裙'],
      category: 'outfit',
      source: 'manual',
    })

    const correction = { tag_name: '白裙', reason: 'multi' as const, note: '标错了' }
    await inspirationsApi.recordTagCorrection('i1', correction)
    expect(mocks.post).toHaveBeenLastCalledWith('/inspirations/i1/tag-corrections', correction)
  })

  it('批量关联博主：body + 放宽 120s 超时', async () => {
    await inspirationsApi.batchLinkBloggers(['i1'], [3])
    expect(mocks.post).toHaveBeenLastCalledWith(
      '/inspirations/batch-bloggers',
      { inspiration_ids: ['i1'], person_ids: [3] },
      { timeout: 120000 },
    )
  })

  it('批量收藏/批量编辑：返回 data.updated 而不是整个响应', async () => {
    mocks.post.mockResolvedValueOnce({ data: { updated: 3 } })
    await expect(inspirationsApi.batchFavorite(['i1', 'i2'], true)).resolves.toBe(3)
    expect(mocks.post).toHaveBeenLastCalledWith('/inspirations/batch-favorite', {
      ids: ['i1', 'i2'],
      is_favorite: true,
    })

    mocks.post.mockResolvedValueOnce({ data: { updated: 2 } })
    await expect(
      inspirationsApi.batchUpdateInspirations(['i1'], { rating: 5, is_favorite: true }),
    ).resolves.toBe(2)
    expect(mocks.post).toHaveBeenLastCalledWith('/inspirations/batch-update', {
      ids: ['i1'],
      rating: 5,
      is_favorite: true,
    })
  })

  it('批量移入垃圾桶：默认只带 ids；有 reason 或非手动来源才带 reason/source', async () => {
    await inspirationsApi.batchTrash(['i1'])
    let body = lastArgs(mocks.post)[1] as Record<string, unknown>
    expect(Object.keys(body)).toEqual(['ids'])

    await inspirationsApi.batchTrash(['i1'], '质量差')
    body = lastArgs(mocks.post)[1] as Record<string, unknown>
    expect(body).toEqual({ ids: ['i1'], reason: '质量差', source: 'manual' })

    await inspirationsApi.batchTrash(['i1'], undefined, 'auto')
    body = lastArgs(mocks.post)[1] as Record<string, unknown>
    expect(Object.keys(body)).toEqual(['ids', 'reason', 'source'])
    expect(body.source).toBe('auto')
  })

  it('单条素材：收藏/评分/裁剪/审核状态/元数据的 PATCH 与 POST', async () => {
    await inspirationsApi.toggleFavorite('i1', true)
    expect(mocks.patch).toHaveBeenLastCalledWith('/inspirations/i1', { is_favorite: true })

    await inspirationsApi.updateRating('i1', 0)
    expect(mocks.patch).toHaveBeenLastCalledWith('/inspirations/i1', { rating: 0 })

    await inspirationsApi.cropInspirationRegion('i1', 0.1, 0.9)
    expect(mocks.post).toHaveBeenLastCalledWith('/inspirations/i1/crop', {
      y1_ratio: 0.1,
      y2_ratio: 0.9,
    })

    await inspirationsApi.updateQualityStatus('i1', 'approved')
    expect(mocks.patch).toHaveBeenLastCalledWith('/inspirations/i1', {
      quality_status: 'approved',
    })
  })

  it('列表/详情/垃圾桶/清空垃圾桶：params 与 DELETE config', async () => {
    await inspirationsApi.fetchInspirations()
    expect(mocks.get).toHaveBeenLastCalledWith('/inspirations', { params: {} })

    await inspirationsApi.fetchInspirations({ page: 2, unrated: true })
    expect(mocks.get).toHaveBeenLastCalledWith('/inspirations', {
      params: { page: 2, unrated: true },
    })

    await inspirationsApi.fetchInspiration('i1')
    expect(mocks.get).toHaveBeenLastCalledWith('/inspirations/i1')

    await inspirationsApi.fetchTrash()
    expect(mocks.get).toHaveBeenLastCalledWith('/inspirations/trash', { params: {} })
    await inspirationsApi.fetchTrash({ reason: '隐私' })
    expect(mocks.get).toHaveBeenLastCalledWith('/inspirations/trash', {
      params: { reason: '隐私' },
    })

    mocks.delete.mockResolvedValueOnce({ data: { deleted: 2, freed_bytes: 10 } })
    await expect(inspirationsApi.emptyTrash()).resolves.toEqual({
      deleted: 2,
      freed_bytes: 10,
    })
    expect(mocks.delete).toHaveBeenLastCalledWith('/inspirations/trash', {
      params: { only_expired: false },
    })
    await inspirationsApi.emptyTrash(true)
    expect(mocks.delete).toHaveBeenLastCalledWith('/inspirations/trash', {
      params: { only_expired: true },
    })
  })

  it('上传素材：FormData + multipart 头 + 进度回调/取消信号透传', async () => {
    const form = new FormData()
    form.append('file', new File(['x'], 'a.jpg'))
    const onProgress = vi.fn()
    const controller = new AbortController()

    await inspirationsApi.uploadInspiration(form, onProgress, controller.signal)

    expect(mocks.post).toHaveBeenCalledWith('/inspirations', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: onProgress,
      signal: controller.signal,
    })
  })

  it('收藏流水只返回 items；移入垃圾桶/恢复/彻底删除的方法与条件 body', async () => {
    const items = [{ id: 1, action: 'favorite' }]
    mocks.get.mockResolvedValueOnce({ data: { items, total: 1 } })
    await expect(inspirationsApi.fetchFavoriteHistory('i1')).resolves.toEqual(items)
    expect(mocks.get).toHaveBeenLastCalledWith('/inspirations/i1/favorite-history', {
      params: { limit: 20 },
    })
    await inspirationsApi.fetchFavoriteHistory('i1', 5)
    expect(mocks.get).toHaveBeenLastCalledWith('/inspirations/i1/favorite-history', {
      params: { limit: 5 },
    })

    await inspirationsApi.moveToTrash('i1')
    let body = lastArgs(mocks.post)[1] as Record<string, unknown>
    expect(Object.keys(body)).toEqual([])

    await inspirationsApi.moveToTrash('i1', '重复')
    body = lastArgs(mocks.post)[1] as Record<string, unknown>
    expect(body).toEqual({ reason: '重复', source: 'manual' })

    await inspirationsApi.restoreInspiration('i1')
    expect(mocks.post).toHaveBeenLastCalledWith('/inspirations/i1/restore')

    await expect(inspirationsApi.deleteInspiration('i1')).resolves.toBeUndefined()
    expect(mocks.delete).toHaveBeenLastCalledWith('/inspirations/i1')
  })

  it('解绑标签 / AI 建议穿搭标签 / 主色调统计：方法、params 与 DELETE 路径', async () => {
    mocks.delete.mockResolvedValueOnce({ data: { removed: true } })
    await expect(inspirationsApi.removeTagFromInspiration('i1', 7)).resolves.toEqual({
      removed: true,
    })
    expect(mocks.delete).toHaveBeenLastCalledWith('/inspirations/i1/tags/7')

    mocks.post.mockResolvedValueOnce({ data: { tags: ['白裙'] } })
    await inspirationsApi.suggestOutfitTags('i1')
    expect(lastArgs(mocks.post)).toEqual([
      '/ai/outfit-tags/suggest',
      null,
      {
        params: { inspiration_id: 'i1' },
      },
    ])

    mocks.get.mockResolvedValueOnce({ data: [{ color: '白', count: 3 }] })
    await expect(inspirationsApi.fetchDominantColors()).resolves.toEqual([
      { color: '白', count: 3 },
    ])
    expect(mocks.get).toHaveBeenLastCalledWith('/inspirations/dominant-colors', {
      params: { limit: 30 },
    })

    await inspirationsApi.fetchDominantColors(5)
    expect(mocks.get).toHaveBeenLastCalledWith('/inspirations/dominant-colors', {
      params: { limit: 5 },
    })
  })

  it('AI 分析入队 / 人脸检测（带形状校验）/ 人脸结果查询', async () => {
    mocks.post.mockResolvedValueOnce({ data: { task_id: 3, status: 'pending' } })
    await inspirationsApi.analyzeInspiration('i1')
    expect(mocks.post).toHaveBeenLastCalledWith('/ai/analyze/i1')

    const detections = [
      {
        id: 1,
        face_index: 0,
        det_score: 0.9,
        matched_blogger_id: null,
        matched_blogger_name: null,
        matched_model_id: null,
        matched_model_name: null,
        confidence: null,
      },
    ]
    const detected = { inspiration_id: 'i1', face_count: 1, detections }
    mocks.post.mockResolvedValueOnce({ data: detected })
    await expect(inspirationsApi.faceDetectInspiration('i1')).resolves.toEqual(detected)
    expect(mocks.post).toHaveBeenLastCalledWith('/inspirations/i1/face-detect')
    expect(mocks.warnItems).toHaveBeenCalledWith(
      detections,
      expect.objectContaining({
        id: 'number',
        face_index: 'number',
        matched_blogger_name: 'string?',
        matched_model_id: 'number?',
      }),
      'face-detect detections',
    )

    await inspirationsApi.fetchFaceDetections('i1')
    expect(mocks.get).toHaveBeenLastCalledWith('/inspirations/i1/face-detections')
  })

  it('人脸检测人工修正：PUT 的 person_type/person_id（解除时 person_type 置 undefined）', async () => {
    await inspirationsApi.updateFaceDetection('i1', 9, 3)
    expect(mocks.put).toHaveBeenLastCalledWith('/inspirations/i1/face-detections/9', {
      person_type: 'blogger',
      person_id: 3,
    })

    await inspirationsApi.updateFaceDetection('i1', 9, 3, 'model')
    expect(mocks.put).toHaveBeenLastCalledWith('/inspirations/i1/face-detections/9', {
      person_type: 'model',
      person_id: 3,
    })

    await inspirationsApi.updateFaceDetection('i1', 9, null)
    expect(mocks.put).toHaveBeenLastCalledWith('/inspirations/i1/face-detections/9', {
      person_type: undefined,
      person_id: null,
    })

    await inspirationsApi.deleteFaceDetection('i1', 9)
    expect(mocks.delete).toHaveBeenLastCalledWith('/inspirations/i1/face-detections/9')
  })

  it('批量质量审核 / 批量取消 AI 标记 / 清理已拒绝素材', async () => {
    await inspirationsApi.batchQualityCheck()
    expect(lastArgs(mocks.post)).toEqual([
      '/ai/quality-check',
      null,
      {
        params: { limit: undefined },
      },
    ])
    await inspirationsApi.batchQualityCheck(10)
    expect(lastArgs(mocks.post)).toEqual([
      '/ai/quality-check',
      null,
      {
        params: { limit: 10 },
      },
    ])

    mocks.post.mockResolvedValueOnce({ data: { updated: 2 } })
    await expect(inspirationsApi.batchUnmarkAi(['i1', 'i2'])).resolves.toEqual({
      updated: 2,
    })
    expect(mocks.post).toHaveBeenLastCalledWith('/admin/batch-unmark-ai', {
      ids: ['i1', 'i2'],
    })

    mocks.delete.mockResolvedValueOnce({ data: { trashed: 5 } })
    await inspirationsApi.deleteRejectedInspirations()
    expect(mocks.delete).toHaveBeenLastCalledWith('/inspirations/quality-rejected')
  })
})

describe('错误不吞：请求失败原样 reject，由调用方提示', () => {
  it('搜索 / 人物 / 出图 / 素材四个模块都向上抛错', async () => {
    mocks.get.mockRejectedValueOnce(new Error('search 500'))
    await expect(searchApi.searchInspirations({})).rejects.toThrow('search 500')

    mocks.get.mockRejectedValueOnce(new Error('person 404'))
    await expect(bloggersApi.fetchDetail(1)).rejects.toThrow('person 404')

    mocks.get.mockRejectedValueOnce(new Error('generate down'))
    await expect(generateApi.fetchStatus()).rejects.toThrow('generate down')

    mocks.delete.mockRejectedValueOnce(new Error('delete 403'))
    await expect(inspirationsApi.deleteInspiration('i1')).rejects.toThrow('delete 403')
  })
})
