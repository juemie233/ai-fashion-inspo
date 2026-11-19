/** services/api.ts 剩余请求函数的契约测试。
 *
 * 覆盖 api.m2/m3/m4 未打到的函数：任务详情与运行台账、文本向量检索、
 * 合集更新/删除/排序、出图批次与模型档位（含会话级缓存）、分享链接入库，
 * 以及各函数在「后端返回 null / 缺字段」时的兜底、上传 FormData 的 MIME 推断。
 *
 * 写法与既有 api.*.test.ts 一致：spy 掉 apiClient 的方法，只校准
 * 「打哪个路径、带什么参数、返回值怎么归一」，不发真实网络请求。
 */

import { apiClient } from '../services/api'
import {
  addTagsToInspiration,
  batchFavorite,
  batchTrash,
  candidateUrl,
  confirmGenerateCandidates,
  deleteCollection,
  discardGenerateRun,
  fetchCollectionInspirations,
  fetchGenerateModelLabels,
  fetchGenerateRun,
  fetchGenerateRuns,
  fetchPerson,
  fetchPersons,
  fetchTask,
  fetchTaskRuns,
  fetchTasks,
  importInspirationFromUrl,
  reorderCollections,
  updateCollection,
  uploadInspirationImage,
  vectorSearchByText,
} from '../services/api'

afterEach(() => {
  jest.restoreAllMocks()
})

describe('任务详情与运行台账', () => {
  it('fetchTask 按 id 取单个任务', async () => {
    const spy = jest.spyOn(apiClient, 'get').mockResolvedValue({
      data: { id: 42, status: 'running', progress: 0.5 },
    })

    const task = await fetchTask(42)

    expect(spy).toHaveBeenCalledWith('/tasks/42')
    expect(task).toMatchObject({ id: 42, status: 'running' })
  })

  it('fetchTaskRuns 带 limit（默认 50）；非数组响应退化为空数组', async () => {
    const spy = jest.spyOn(apiClient, 'get').mockResolvedValue({
      data: [{ id: 1, task_id: 7, attempt: 0, status: 'running' }],
    })

    const runs = await fetchTaskRuns(7)
    expect(spy).toHaveBeenCalledWith('/tasks/7/runs', { params: { limit: 50 } })
    expect(runs).toHaveLength(1)

    // 后端改坏返回结构时页面不能拿到 undefined 去 map
    spy.mockResolvedValue({ data: { unexpected: true } })
    expect(await fetchTaskRuns(7, 20)).toEqual([])
    expect(spy).toHaveBeenLastCalledWith('/tasks/7/runs', { params: { limit: 20 } })
  })

  it('fetchTasks 把状态与类型筛选拼进 query；空筛选不发空字段', async () => {
    const spy = jest.spyOn(apiClient, 'get').mockResolvedValue({ data: { items: [], total: 0 } })

    await fetchTasks(1, 20, { status: 'running', type: 'analysis' })
    expect(spy).toHaveBeenLastCalledWith('/tasks', {
      params: { page: 1, size: 20, status: 'running', type: 'analysis' },
    })

    await fetchTasks(2, 10)
    expect(spy).toHaveBeenLastCalledWith('/tasks', { params: { page: 2, size: 10 } })
  })
})

describe('文本向量检索', () => {
  it('以 multipart 提交 text / top_k，超时放宽到 45s', async () => {
    const post = jest
      .spyOn(apiClient, 'post')
      .mockResolvedValue({ data: { items: [{ score: 0.9 }] } })
    // 不同运行环境（RN polyfill / Node 内置）的 FormData 都走同一个 append 原型
    const append = jest.spyOn(FormData.prototype, 'append')

    const hits = await vectorSearchByText('法式白裙', 7)

    const [url, form, config] = post.mock.calls[0] as unknown as [
      string,
      FormData,
      { headers: Record<string, string>; timeout: number },
    ]
    expect(url).toBe('/search/vector')
    expect(form).toBeInstanceOf(FormData)
    expect(config.headers['Content-Type']).toBe('multipart/form-data')
    expect(config.timeout).toBe(45000)
    expect(append).toHaveBeenCalledWith('text', '法式白裙')
    expect(append).toHaveBeenCalledWith('top_k', '7')
    expect(hits).toEqual([{ score: 0.9 }])
  })

  it('后端没有 items 时返回空数组', async () => {
    jest.spyOn(apiClient, 'post').mockResolvedValue({ data: null })
    expect(await vectorSearchByText('白裙')).toEqual([])
  })
})

describe('合集管理', () => {
  it('updateCollection 打 PATCH /collections/{id}，只提交 patch 字段', async () => {
    const spy = jest.spyOn(apiClient, 'patch').mockResolvedValue({ data: { id: 3, name: '新名' } })

    const updated = await updateCollection(3, { name: '新名' })

    expect(spy).toHaveBeenCalledWith('/collections/3', { name: '新名' })
    expect(updated).toEqual({ id: 3, name: '新名' })
  })

  it('deleteCollection 打 DELETE /collections/{id}（素材不动）', async () => {
    const spy = jest.spyOn(apiClient, 'delete').mockResolvedValue({ data: {} })
    await deleteCollection(3)
    expect(spy).toHaveBeenCalledWith('/collections/3')
  })

  it('reorderCollections：空数组不发请求；非空按 ordered_ids 提交', async () => {
    const spy = jest.spyOn(apiClient, 'patch').mockResolvedValue({ data: {} })

    await reorderCollections([])
    expect(spy).not.toHaveBeenCalled()

    await reorderCollections([3, 1, 2])
    expect(spy).toHaveBeenCalledWith('/collections/order', { ordered_ids: [3, 1, 2] })
  })

  it('fetchCollectionInspirations 后端返回 null 时给空页（page/page_size 参数名不变）', async () => {
    const spy = jest.spyOn(apiClient, 'get').mockResolvedValue({ data: null })

    const page = await fetchCollectionInspirations(9)

    expect(spy).toHaveBeenCalledWith('/collections/9/inspirations', {
      params: { page: 1, page_size: 30 },
    })
    expect(page).toEqual({ items: [], total: 0, page: 1, size: 30 })
  })
})

describe('人物接口兜底', () => {
  it('fetchPersons 列表响应为 null 时给空页而不是崩', async () => {
    jest.spyOn(apiClient, 'get').mockResolvedValue({ data: null })

    expect(await fetchPersons('model')).toEqual({ items: [], total: 0, page: 1, size: 20 })
  })

  it('fetchPerson 详情响应为 null 时返回 null（详情页据此显示空态）', async () => {
    jest.spyOn(apiClient, 'get').mockResolvedValue({ data: null })
    expect(await fetchPerson('blogger', 5)).toBeNull()
  })
})

describe('出图批次与候选入库', () => {
  it('confirmGenerateCandidates 打 confirm 接口，并透传入库/跳过明细', async () => {
    const post = jest.spyOn(apiClient, 'post').mockResolvedValue({
      data: {
        imported: 2,
        skipped: 1,
        ids: ['a', 'b'],
        skipped_items: [{ filename: '03.png', reason: '内容重复' }],
      },
    })

    const result = await confirmGenerateCandidates(12, ['01.png', '02.png'])

    expect(post).toHaveBeenCalledWith('/generate/runs/12/confirm', {
      filenames: ['01.png', '02.png'],
    })
    expect(result).toEqual({
      imported: 2,
      skipped: 1,
      ids: ['a', 'b'],
      skipped_items: [{ filename: '03.png', reason: '内容重复' }],
    })
  })

  it('confirmGenerateCandidates 返回 null 时全部给安全默认值', async () => {
    jest.spyOn(apiClient, 'post').mockResolvedValue({ data: null })

    expect(await confirmGenerateCandidates(12, [])).toEqual({
      imported: 0,
      skipped: 0,
      ids: [],
      skipped_items: [],
    })
  })

  it('discardGenerateRun 用 DELETE /generate/runs/{id}（不是 cancel）', async () => {
    const del = jest.spyOn(apiClient, 'delete').mockResolvedValue({ data: {} })
    await discardGenerateRun(12)
    expect(del).toHaveBeenCalledWith('/generate/runs/12')
  })

  it('fetchGenerateRuns 分页参数 + 空响应归一', async () => {
    const get = jest
      .spyOn(apiClient, 'get')
      .mockResolvedValue({ data: { items: [{ id: 1 }], total: 9 } })

    const page = await fetchGenerateRuns(2, 10)
    expect(get).toHaveBeenCalledWith('/generate/runs', { params: { page: 2, size: 10 } })
    expect(page).toMatchObject({ total: 9, page: 2, size: 10 })

    get.mockResolvedValue({ data: null })
    expect(await fetchGenerateRuns()).toEqual({ items: [], total: 0, page: 1, size: 20 })
  })

  it('fetchGenerateRun 把 {items, run} 归一成 {run, candidates}', async () => {
    const get = jest.spyOn(apiClient, 'get').mockResolvedValue({
      data: {
        run: { id: 12, prompt: '法式' },
        items: [{ filename: '01.png', index: 0, imported: false, url: '/api/x/01.png' }],
      },
    })

    const detail = await fetchGenerateRun(12)

    expect(get).toHaveBeenCalledWith('/generate/runs/12')
    expect(detail.run).toMatchObject({ id: 12, prompt: '法式' })
    expect(detail.candidates).toHaveLength(1)

    get.mockResolvedValue({ data: null })
    expect(await fetchGenerateRun(12)).toEqual({ run: {}, candidates: [] })
  })

  it('fetchGenerateModelLabels 去掉档位名后的补充说明，并在会话内缓存', async () => {
    const get = jest.spyOn(apiClient, 'get').mockResolvedValue({
      data: {
        items: [
          { key: 'fast', label: '快速档（快，约 6 秒）' },
          { key: 'slow', label: '精细档' },
        ],
      },
    })

    expect(await fetchGenerateModelLabels()).toEqual({ fast: '快速档', slow: '精细档' })

    // 第二次命中模块级缓存，不再请求 4.3MB 级别的接口
    await fetchGenerateModelLabels()
    expect(get).toHaveBeenCalledTimes(1)
  })

  it('fetchGenerateModelLabels 返回结构异常时给空表，不抛错', async () => {
    // 档位缓存是模块级的，只有换一个模块实例才能再测「首次拉取」分支
    jest.resetModules()
    const mod = require('../services/api') as typeof import('../services/api')
    const get = jest.spyOn(mod.apiClient, 'get').mockResolvedValue({ data: null })

    expect(await mod.fetchGenerateModelLabels()).toEqual({})
    expect(get).toHaveBeenCalledWith('/generate/models')
  })
})

describe('候选图 URL 拼接', () => {
  it('相对路径拼 host、绝对地址不重复拼；w>0 请求缩略图；空值返回空串', () => {
    expect(candidateUrl('http://h:18888', '')).toBe('')
    expect(candidateUrl('http://h:18888', '/api/generate/runs/1/candidates/01.png')).toBe(
      'http://h:18888/api/generate/runs/1/candidates/01.png',
    )
    expect(candidateUrl('http://h:18888', '/api/x.png', 320)).toBe(
      'http://h:18888/api/x.png?w=320',
    )
    expect(candidateUrl('http://h:18888', 'https://cdn.example.com/a.png')).toBe(
      'https://cdn.example.com/a.png',
    )
    expect(candidateUrl('http://h:18888', null as unknown as string)).toBe('')
  })
})

describe('上传 FormData 的 MIME 推断', () => {
  it('HEIC 按 jpeg 上报（后端不认 HEIC），并带 source_type', async () => {
    jest.spyOn(apiClient, 'post').mockResolvedValue({ data: { id: 'new-1' } })
    const append = jest.spyOn(FormData.prototype, 'append')

    await uploadInspirationImage('file:///tmp/cat.heic')

    expect(append).toHaveBeenCalledWith('file', {
      uri: 'file:///tmp/cat.heic',
      name: 'cat.heic',
      type: 'image/jpeg',
    })
    expect(append).toHaveBeenCalledWith('source_type', 'manual_upload')
  })

  it('常见扩展名各用对应 MIME；未知扩展名回退 image/jpeg', async () => {
    jest.spyOn(apiClient, 'post').mockResolvedValue({ data: { id: 'new-1' } })
    const append = jest.spyOn(FormData.prototype, 'append')

    const cases: Array<[string, string]> = [
      ['file:///tmp/a.png', 'image/png'],
      ['file:///tmp/a.webp', 'image/webp'],
      ['file:///tmp/a.gif', 'image/gif'],
      ['file:///tmp/a.bmp', 'image/jpeg'],
    ]
    for (const [uri, type] of cases) {
      append.mockClear()
      await uploadInspirationImage(uri)
      expect(append).toHaveBeenCalledWith('file', expect.objectContaining({ uri, type }))
    }
  })

  it('路径末尾没有文件名时用 photo.jpg 兜底', async () => {
    jest.spyOn(apiClient, 'post').mockResolvedValue({ data: { id: 'new-1' } })
    const append = jest.spyOn(FormData.prototype, 'append')

    await uploadInspirationImage('file:///tmp/')

    expect(append).toHaveBeenCalledWith('file', {
      uri: 'file:///tmp/',
      name: 'photo.jpg',
      type: 'image/jpeg',
    })
  })
})

describe('后端返回 null 时的安全兜底', () => {
  it('addTagsToInspiration / batchFavorite / batchTrash 不把 undefined 透传给调用方', async () => {
    const post = jest.spyOn(apiClient, 'post').mockResolvedValue({ data: null })

    expect(await addTagsToInspiration('i1', ['蕾丝'])).toEqual({})
    expect(await batchFavorite(['a'], true)).toEqual({})
    expect(await batchTrash(['a'])).toEqual({})
    expect(post).toHaveBeenCalledTimes(3)
  })

  it('importInspirationFromUrl 打 POST /inspirations/from-url 并提交 url', async () => {
    const post = jest.spyOn(apiClient, 'post').mockResolvedValue({ data: { id: 'i9' } })

    const created = await importInspirationFromUrl('https://example.com/p/1')

    expect(post).toHaveBeenCalledWith('/inspirations/from-url', {
      url: 'https://example.com/p/1',
    })
    expect(created).toEqual({ id: 'i9' })
  })
})
