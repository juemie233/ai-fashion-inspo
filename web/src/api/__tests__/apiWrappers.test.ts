/**
 * API 封装层单测（tags / tasks / keyframes / admin / aiQuality / tagAdvanced / faceScan）。
 *
 * 这些 `api/*.ts` 是前端唯一与后端契约对齐的位置：参数名写错（`tag_ids` 写成 `ids`）、
 * 方法用错（PATCH 写成 POST）、URL 拼错（路径段/复数），页面测试几乎发现不了，
 * 但线上就是 422/404。这里逐条锁定「方法 + URL + 请求体/params」，
 * 并验证封装层**不吞异常**（错误交给调用方决定提示方式）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  warnItems: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  default: {
    get: mocks.get,
    post: mocks.post,
    patch: mocks.patch,
    delete: mocks.delete,
  },
}))

vi.mock('@/utils/apiGuard', () => ({ warnItems: mocks.warnItems }))

import * as tagsApi from '@/api/tags'
import { fetchTaskRuns } from '@/api/tasks'
import { fetchKeyframes } from '@/api/keyframes'
import {
  exportInspirationsCsv,
  fetchAuditLogs,
  fetchBackupStatus,
  fetchInspirationTrend,
  fetchNearDuplicates,
  fetchPersonFrequency,
  fetchPromptQuality,
  startPhashBackfill,
} from '@/api/admin'
import { submitQualityCorrection } from '@/api/aiQuality'
import * as tagAdvanced from '@/api/tagAdvanced'
import * as faceScan from '@/api/faceScan'

function resolved(data: unknown) {
  return Promise.resolve({ data })
}

describe('API 封装层', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.get.mockResolvedValue({ data: {} })
    mocks.post.mockResolvedValue({ data: {} })
    mocks.patch.mockResolvedValue({ data: {} })
    mocks.delete.mockResolvedValue({ data: {} })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  describe('tags', () => {
    it('标签基础 CRUD 与批量操作走对方法与路径', async () => {
      await tagsApi.fetchTagsGrouped()
      expect(mocks.get).toHaveBeenCalledWith('/tags')

      await tagsApi.createTag('法式')
      expect(mocks.post).toHaveBeenCalledWith('/tags', { name: '法式', category: 'free' })

      await tagsApi.createTag('通勤', 'scene')
      expect(mocks.post).toHaveBeenLastCalledWith('/tags', { name: '通勤', category: 'scene' })

      await tagsApi.updateTag(5, { name: '新名', pinned: true })
      expect(mocks.patch).toHaveBeenCalledWith('/tags/5', { name: '新名', pinned: true })

      await tagsApi.reorderTags([{ id: 1, sort_order: 2 }])
      expect(mocks.post).toHaveBeenCalledWith('/tags/reorder', {
        items: [{ id: 1, sort_order: 2 }],
      })

      await tagsApi.mergeTags(3, 9)
      expect(mocks.post).toHaveBeenCalledWith('/tags/merge', {
        source_tag_id: 3,
        target_tag_id: 9,
      })

      await tagsApi.batchDeleteTags([1, 2])
      expect(mocks.post).toHaveBeenCalledWith('/tags/batch-delete', { tag_ids: [1, 2] })

      await tagsApi.deleteUnusedTags()
      expect(mocks.delete).toHaveBeenCalledWith('/tags/unused')

      await tagsApi.batchChangeCategory([1, 2], 'color')
      expect(mocks.patch).toHaveBeenCalledWith('/tags/batch-category', {
        tag_ids: [1, 2],
        category: 'color',
      })

      await tagsApi.batchRenameTags([1], '显瘦', '修身')
      expect(mocks.patch).toHaveBeenCalledWith('/tags/batch-rename', {
        tag_ids: [1],
        find: '显瘦',
        replace: '修身',
      })
    })

    it('相似标签建议：名称必须 URL 编码（含空格与斜杠）', async () => {
      await tagsApi.getSimilarSuggestions('春日 穿搭/裙')

      expect(mocks.get).toHaveBeenCalledWith(
        '/tags/suggestions/%E6%98%A5%E6%97%A5%20%E7%A9%BF%E6%90%AD%2F%E8%A3%99',
      )
    })

    it('统计 / 重复扫描 / 标签详情 / 导入导出 / 别名', async () => {
      await tagsApi.fetchTagStats()
      expect(mocks.get).toHaveBeenCalledWith('/tags/stats')

      await tagsApi.findDuplicates()
      expect(mocks.get).toHaveBeenLastCalledWith('/tags/duplicates', {
        params: { threshold: 0.75 },
      })
      await tagsApi.findDuplicates(0.9)
      expect(mocks.get).toHaveBeenLastCalledWith('/tags/duplicates', { params: { threshold: 0.9 } })

      await tagsApi.fetchTagInspirations(7, 2, 10, 'newest')
      expect(mocks.get).toHaveBeenLastCalledWith('/tags/7/inspirations', {
        params: { page: 2, size: 10, sort: 'newest' },
      })

      await tagsApi.batchRemoveTagInspirations(7, ['a', 'b'])
      expect(mocks.post).toHaveBeenCalledWith('/tags/7/inspirations/batch-remove', {
        inspiration_ids: ['a', 'b'],
      })

      await tagsApi.exportTags()
      expect(mocks.get).toHaveBeenCalledWith('/tags/export')

      await tagsApi.importTags([{ name: '法式', category: 'style' }])
      expect(mocks.post).toHaveBeenCalledWith('/tags/import', {
        tags: [{ name: '法式', category: 'style' }],
      })

      await tagsApi.fetchAliases()
      expect(mocks.get).toHaveBeenCalledWith('/tags/aliases')

      await tagsApi.createAlias(4, '法风')
      expect(mocks.post).toHaveBeenCalledWith('/tags/4/aliases', { alias: '法风' })

      await tagsApi.deleteAlias(9)
      expect(mocks.delete).toHaveBeenCalledWith('/tags/aliases/9')
    })

    it('共现网络 / 热门排行 / 使用趋势的参数名（min_count、granularity）', async () => {
      await tagsApi.fetchCooccurrenceNetwork(20, 3)
      expect(mocks.get).toHaveBeenCalledWith('/tags/cooccurrence-network', {
        params: { limit: 20, min_count: 3 },
      })

      await tagsApi.fetchTopTags(10)
      expect(mocks.get).toHaveBeenCalledWith('/tags/top', { params: { limit: 10 } })

      await tagsApi.fetchTagTrend(6, 'week')
      expect(mocks.get).toHaveBeenCalledWith('/tags/6/trend', {
        params: { granularity: 'week' },
      })
    })
  })

  describe('tasks / keyframes', () => {
    it('任务运行台账：按任务 id 拉取，非数组响应回退为空数组', async () => {
      mocks.get.mockResolvedValueOnce(resolved([{ id: 1 }]))
      expect(await fetchTaskRuns(7)).toEqual([{ id: 1 }])
      expect(mocks.get).toHaveBeenLastCalledWith('/tasks/7/runs')

      // 后端异常返回 null 时不能让调用方对 null 做 .map
      mocks.get.mockResolvedValueOnce(resolved(null))
      expect(await fetchTaskRuns(7)).toEqual([])
    })

    it('关键帧：GET /files/keyframes/{inspirationId}', async () => {
      mocks.get.mockResolvedValueOnce(resolved({ frames: ['f1.jpg'] }))
      expect(await fetchKeyframes('abc')).toEqual({ frames: ['f1.jpg'] })
      expect(mocks.get).toHaveBeenCalledWith('/files/keyframes/abc')
    })
  })

  describe('admin', () => {
    it('提示词质量 / 趋势 / 人物频次 / 审计日志的参数与返回值解包', async () => {
      await fetchPromptQuality()
      expect(mocks.get).toHaveBeenCalledWith('/ai/prompt-quality', {
        params: { days: 30, include_bare_rate: false },
      })
      await fetchPromptQuality(7, true)
      expect(mocks.get).toHaveBeenLastCalledWith('/ai/prompt-quality', {
        params: { days: 7, include_bare_rate: true },
      })

      mocks.get.mockResolvedValueOnce(
        resolved({ days: 30, trend: [{ day: '2026-11-01', count: 2 }] }),
      )
      expect(await fetchInspirationTrend(30)).toEqual([{ day: '2026-11-01', count: 2 }])
      expect(mocks.get).toHaveBeenLastCalledWith('/admin/trend', { params: { days: 30 } })

      await fetchPersonFrequency(5)
      expect(mocks.get).toHaveBeenLastCalledWith('/admin/person-frequency', {
        params: { limit: 5 },
      })

      const logs = [{ id: 1, action: 'trash', count: 1 }]
      mocks.get.mockResolvedValueOnce(resolved(logs))
      expect(await fetchAuditLogs()).toEqual(logs)
      expect(mocks.get).toHaveBeenLastCalledWith('/admin/audit-logs', { params: { limit: 50 } })
      // 审计日志要过字段校验（created_at 缺时区曾导致时间显示差 8 小时）
      expect(mocks.warnItems).toHaveBeenCalledWith(
        logs,
        expect.objectContaining({ action: 'string', created_at: 'string?' }),
        'audit-logs',
      )
    })

    it('近似重复扫描 POST body / phash 补齐的 force 分支 / 备份状态', async () => {
      await fetchNearDuplicates()
      expect(mocks.post).toHaveBeenCalledWith('/admin/near-duplicates', {
        limit: 0,
        threshold: 32,
      })

      await startPhashBackfill()
      expect(mocks.post).toHaveBeenLastCalledWith('/admin/phash-backfill', undefined, {
        params: undefined,
      })
      await startPhashBackfill(true)
      expect(mocks.post).toHaveBeenLastCalledWith('/admin/phash-backfill', undefined, {
        params: { force: true },
      })

      await fetchBackupStatus()
      expect(mocks.get).toHaveBeenLastCalledWith('/admin/backup/status')
    })

    it('导出 CSV：按 content-disposition 命名并触发浏览器下载', async () => {
      const blob = new Blob(['a,b'], { type: 'text/csv' })
      mocks.get.mockResolvedValueOnce({
        data: blob,
        headers: { 'content-disposition': 'attachment; filename="fashion_2026.csv"' },
      })
      const createObjectURL = vi.fn(() => 'blob:mock')
      const revokeObjectURL = vi.fn()
      vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
      const anchors: HTMLAnchorElement[] = []
      const realCreate = document.createElement.bind(document)
      const createSpy = vi.spyOn(document, 'createElement')
      createSpy.mockImplementation(((tag: string) => {
        const el = realCreate(tag)
        if (tag === 'a') anchors.push(el as HTMLAnchorElement)
        return el
      }) as never)
      const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

      await exportInspirationsCsv()

      expect(mocks.get).toHaveBeenCalledWith('/admin/export', { responseType: 'blob' })
      expect(createObjectURL).toHaveBeenCalledWith(blob)
      expect(anchors[0]?.download).toBe('fashion_2026.csv')
      expect(anchors[0]?.href).toBe('blob:mock')
      expect(click).toHaveBeenCalled()
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock')

      createSpy.mockRestore()
      click.mockRestore()
    })

    it('导出 CSV：没有 content-disposition 时回退带时间戳的文件名', async () => {
      mocks.get.mockResolvedValueOnce({ data: new Blob(['x']), headers: {} })
      vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:mock'), revokeObjectURL: vi.fn() })
      const anchors: HTMLAnchorElement[] = []
      const realCreate = document.createElement.bind(document)
      const createSpy = vi.spyOn(document, 'createElement')
      createSpy.mockImplementation(((tag: string) => {
        const el = realCreate(tag)
        if (tag === 'a') anchors.push(el as HTMLAnchorElement)
        return el
      }) as never)
      vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

      await exportInspirationsCsv()

      expect(anchors[0]?.download).toMatch(/^inspirations_\d+\.csv$/)
      createSpy.mockRestore()
    })
  })

  describe('aiQuality', () => {
    it('误判反馈：POST /ai/quality-corrections，body 原样透传', async () => {
      const correction = {
        inspiration_id: 'i1',
        corrected_status: 'approved' as const,
        reason_code: 'blurry',
        note: '艺术虚焦',
      }
      mocks.post.mockResolvedValueOnce(resolved({ id: 9 }))

      expect(await submitQualityCorrection(correction)).toEqual({ id: 9 })
      expect(mocks.post).toHaveBeenCalledWith('/ai/quality-corrections', correction)
    })
  })

  describe('tagAdvanced', () => {
    it('健康度 / 聚类 / 网络图 / 批量编辑的任务提交参数', async () => {
      await tagAdvanced.scanHealth()
      expect(mocks.post).toHaveBeenCalledWith('/tags/health/scan', { duplicate_threshold: 0.75 })

      await tagAdvanced.fetchHealthIssue('duplicate', 2, 10)
      expect(mocks.get).toHaveBeenLastCalledWith('/tags/health/duplicate', {
        params: { page: 2, size: 10 },
      })

      await tagAdvanced.scanClusters({ threshold: 0.8 })
      expect(mocks.post).toHaveBeenCalledWith('/tags/clusters/scan', { threshold: 0.8 })

      const clusterBody = { batch_id: 'b1', groups: [{ target_tag_id: 1, source_tag_ids: [2] }] }
      await tagAdvanced.applyClusters(clusterBody)
      expect(mocks.post).toHaveBeenCalledWith('/tags/clusters/apply', clusterBody)

      await tagAdvanced.analyzeNetwork({ limit: 10, with_communities: true })
      expect(mocks.post).toHaveBeenCalledWith('/tags/network/analyze', {
        limit: 10,
        with_communities: true,
      })

      const editBody = { dry_run: true, rules: [{ action: 'rename' }] } as never
      await tagAdvanced.batchEditTags(editBody)
      expect(mocks.post).toHaveBeenCalledWith('/tags/batch-edit', editBody)
    })

    it('层级树 parent_id=null 表示根 / 移动 / 历史 / 回滚', async () => {
      await tagAdvanced.fetchTree(null)
      expect(mocks.get).toHaveBeenCalledWith('/tags/tree', {
        params: { parent_id: null, page: 1, size: 200 },
      })
      await tagAdvanced.fetchTree(3, 2, 50)
      expect(mocks.get).toHaveBeenLastCalledWith('/tags/tree', {
        params: { parent_id: 3, page: 2, size: 50 },
      })

      await tagAdvanced.moveTags([{ tag_id: 1, parent_id: null }])
      expect(mocks.post).toHaveBeenCalledWith('/tags/move', {
        moves: [{ tag_id: 1, parent_id: null }],
      })

      await tagAdvanced.fetchHistory({ page: 2, operation: 'merge' })
      expect(mocks.get).toHaveBeenLastCalledWith('/tags/history', {
        params: { page: 2, operation: 'merge' },
      })

      await tagAdvanced.rollbackHistory(9)
      expect(mocks.post).toHaveBeenLastCalledWith('/tags/history/9/rollback')
    })

    it('效果分析：趋势 / 组合 / 覆盖度 / 来源分布', async () => {
      await tagAdvanced.fetchTrending(7, 5)
      expect(mocks.get).toHaveBeenLastCalledWith('/tags/effect/trending', {
        params: { days: 7, top: 5 },
      })

      await tagAdvanced.fetchCombinations(10, 3)
      expect(mocks.get).toHaveBeenLastCalledWith('/tags/effect/combinations', {
        params: { limit: 10, min_count: 3 },
      })

      await tagAdvanced.fetchCoverage()
      expect(mocks.get).toHaveBeenCalledWith('/tags/effect/coverage')

      await tagAdvanced.fetchSourceDist()
      expect(mocks.get).toHaveBeenCalledWith('/tags/effect/source_dist')
    })

    it('任务结果转型函数：null 返回 null，对象原样返回', () => {
      const result = { total: 1 }
      expect(tagAdvanced.asHealthResult(null)).toBeNull()
      expect(tagAdvanced.asHealthResult(result)).toBe(result)
      expect(tagAdvanced.asClusterResult(null)).toBeNull()
      expect(tagAdvanced.asNetworkResult(null)).toBeNull()
      expect(tagAdvanced.asNetworkResult(result)).toBe(result)
    })

    it('颜色剥离 / 观察名单 / 共现 / 别名建议 / 批量合并', async () => {
      await tagAdvanced.stripColorDryRun({ category: 'color' })
      expect(mocks.post).toHaveBeenCalledWith('/tags/color-strip/dry-run', {
        category: 'color',
      })

      await tagAdvanced.stripColorApply({ limit: 100 })
      expect(mocks.post).toHaveBeenCalledWith('/tags/color-strip/apply', { limit: 100 })

      await tagAdvanced.fetchWatchList({ days: 7, max_usage: 3 })
      expect(mocks.get).toHaveBeenLastCalledWith('/tags/watchlist', {
        params: { days: 7, max_usage: 3 },
      })

      await tagAdvanced.keepWatchTags([1, 2])
      expect(mocks.post).toHaveBeenCalledWith('/tags/watchlist/keep', { tag_ids: [1, 2] })

      await tagAdvanced.fetchTagCooccurrence(5, 3)
      expect(mocks.get).toHaveBeenLastCalledWith('/tags/5/cooccurrence', {
        params: { limit: 3 },
      })

      await tagAdvanced.fetchAliasSuggestions({ min_similarity: 0.5 })
      expect(mocks.get).toHaveBeenLastCalledWith('/tags/alias-suggestions', {
        params: { min_similarity: 0.5 },
        timeout: 90000,
      })

      await tagAdvanced.mergeTagsBatch([1, 2], 3)
      expect(mocks.post).toHaveBeenLastCalledWith('/tags/merge-batch', {
        source_ids: [1, 2],
        target_id: 3,
      })
    })
  })

  describe('faceScan', () => {
    it('扫描 / 匹配任务的提交参数（默认 scope=semi、auto_match=false）', async () => {
      await faceScan.startFaceScan()
      expect(mocks.post).toHaveBeenCalledWith('/face-scan/start', {
        scope: 'semi',
        auto_match: false,
      })
      await faceScan.startFaceScan('all', true)
      expect(mocks.post).toHaveBeenLastCalledWith('/face-scan/start', {
        scope: 'all',
        auto_match: true,
      })

      await faceScan.runFaceMatch({ scope: 'bloggers', threshold: 0.6 })
      expect(mocks.post).toHaveBeenLastCalledWith('/face-match/run', {
        scope: 'bloggers',
        threshold: 0.6,
      })

      await faceScan.runFaceCluster({ threshold: 0.5 })
      expect(mocks.post).toHaveBeenLastCalledWith('/face-scan/cluster/run', {
        threshold: 0.5,
      })
    })

    it('结果查询 / 审核确认 / 任务状态', async () => {
      await faceScan.fetchFaceScanTask()
      expect(mocks.get).toHaveBeenCalledWith('/face-scan/task')

      await faceScan.fetchFaceScanResults({ unmatched: true, page: 2 })
      expect(mocks.get).toHaveBeenLastCalledWith('/face-scan/results', {
        params: { unmatched: true, page: 2 },
      })

      const items = [{ detection_id: 1, person_type: 'blogger' as const, person_id: 3 }]
      await faceScan.confirmFaceScan('confirm', items)
      expect(mocks.post).toHaveBeenCalledWith('/face-scan/confirm', {
        action: 'confirm',
        items,
      })

      await faceScan.fetchFaceClusterTask()
      expect(mocks.get).toHaveBeenCalledWith('/face-scan/cluster/task')

      await faceScan.fetchFaceClusterGroups({ page: 2 })
      expect(mocks.get).toHaveBeenLastCalledWith('/face-scan/cluster/groups', {
        params: { page: 2 },
      })

      await faceScan.fetchFaceClusterDetections(8, { page: 1, size: 50 })
      expect(mocks.get).toHaveBeenLastCalledWith('/face-scan/cluster/groups/8/detections', {
        params: { page: 1, size: 50 },
      })

      await faceScan.fetchFaceServiceStatus()
      expect(mocks.get).toHaveBeenCalledWith('/face-scan/service-status')
    })

    it('批量注册计划/执行（长超时）与聚合组操作', async () => {
      await faceScan.fetchFaceRegisterPlan({ limit: 10 })
      expect(mocks.post).toHaveBeenCalledWith(
        '/face-scan/register-plan',
        { limit: 10 },
        { timeout: 120000 },
      )

      const items = [{ person_type: 'blogger' as const, person_id: 1, inspiration_ids: ['a'] }]
      await faceScan.registerFaceBatch(items)
      expect(mocks.post).toHaveBeenLastCalledWith(
        '/face-scan/register-batch',
        { items },
        { timeout: 120000 },
      )

      await faceScan.ignoreFaceClusterGroup(4)
      expect(mocks.post).toHaveBeenLastCalledWith('/face-scan/cluster/groups/4/ignore')

      const payload = { name: '小美', platform: 'douyin' as const }
      await faceScan.createFaceClusterPerson(4, payload)
      expect(mocks.post).toHaveBeenLastCalledWith(
        '/face-scan/cluster/groups/4/create-person',
        payload,
      )
    })
  })

  describe('错误透传', () => {
    it('各模块接口错误原样抛出，封装层不吞异常', async () => {
      const err = new Error('500 Internal Server Error')
      mocks.get.mockRejectedValueOnce(err)
      await expect(tagsApi.fetchTagStats()).rejects.toBe(err)

      mocks.post.mockRejectedValueOnce(err)
      await expect(tagAdvanced.scanHealth()).rejects.toBe(err)

      mocks.get.mockRejectedValueOnce(err)
      await expect(fetchKeyframes('abc')).rejects.toBe(err)

      mocks.post.mockRejectedValueOnce(err)
      await expect(faceScan.startFaceScan()).rejects.toBe(err)

      mocks.post.mockRejectedValueOnce(err)
      await expect(
        submitQualityCorrection({ inspiration_id: 'i1', corrected_status: 'approved' }),
      ).rejects.toBe(err)

      mocks.get.mockRejectedValueOnce(err)
      await expect(fetchTaskRuns(1)).rejects.toBe(err)
    })
  })
})
