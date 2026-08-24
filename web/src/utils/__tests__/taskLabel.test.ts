/** 任务标签工具单测。 */

import { describe, expect, it } from 'vitest'
import {
  SCRAPER_PLATFORM_LABELS,
  TASK_TYPE_ICONS,
  TASK_TYPE_LABELS,
  formatDuration,
  normalizeTaskStatus,
  taskStatusType,
  taskTypeTagColor,
} from '../taskLabel'

describe('TASK_TYPE_LABELS', () => {
  it('全量任务类型映射为中文，vector_backfill 不得被错误归类', () => {
    expect(TASK_TYPE_LABELS.batch_analyze).toBe('批量 AI 分析')
    expect(TASK_TYPE_LABELS.quality_check).toBe('质量审核')
    expect(TASK_TYPE_LABELS.batch_delete).toBe('批量删除')
    expect(TASK_TYPE_LABELS.deduplicate).toBe('近似重复检测删除')
    expect(TASK_TYPE_LABELS.scraper).toBe('采集')
    expect(TASK_TYPE_LABELS.vector_backfill).toBe('向量回填')
    expect(TASK_TYPE_LABELS.face_scan).toBe('人脸库扫描')
    expect(TASK_TYPE_LABELS.face_match).toBe('人脸匹配')
    expect(TASK_TYPE_LABELS.enrich_blogger_profile).toBe('博主主页补全')
    expect(TASK_TYPE_LABELS.tag_health_scan).toBe('标签健康扫描')
    expect(TASK_TYPE_LABELS.tag_cluster_scan).toBe('标签聚类扫描')
    expect(TASK_TYPE_LABELS.tag_network_analyze).toBe('标签网络分析')
  })
})

describe('TASK_TYPE_ICONS', () => {
  it('全量任务类型均有图标映射，删除类任务图标互不相同', () => {
    for (const type of Object.keys(TASK_TYPE_LABELS)) {
      expect(TASK_TYPE_ICONS[type], `类型 ${type} 缺少图标映射`).toBeTruthy()
    }
    // 两类删除任务必须使用不同图标，保证列表中一眼可辨
    expect(TASK_TYPE_ICONS.batch_delete).not.toBe(TASK_TYPE_ICONS.deduplicate)
  })
})

describe('normalizeTaskStatus', () => {
  it('completed 归一化为 success', () => {
    expect(normalizeTaskStatus('completed')).toBe('success')
    expect(normalizeTaskStatus('running')).toBe('running')
  })
})

describe('taskStatusType', () => {
  it('状态到 Arco 预设色映射', () => {
    expect(taskStatusType('success')).toBe('green')
    expect(taskStatusType('failed')).toBe('red')
    expect(taskStatusType('running')).toBe('arcoblue')
    expect(taskStatusType('pending')).toBe('gray')
    expect(taskStatusType('unknown')).toBe('gray')
  })
})

describe('taskTypeTagColor', () => {
  it('类型到 Arco 预设色映射，未知回退 gray', () => {
    expect(taskTypeTagColor('batch_delete')).toBe('red')
    expect(taskTypeTagColor('batch_analyze')).toBe('arcoblue')
    expect(taskTypeTagColor('enrich_blogger_profile')).toBe('gold')
    expect(taskTypeTagColor('tag_cluster_scan')).toBe('cyan')
    expect(taskTypeTagColor('unknown_type')).toBe('gray')
  })
})

describe('SCRAPER_PLATFORM_LABELS', () => {
  it('各采集平台映射为中文（含浏览器插件）', () => {
    expect(SCRAPER_PLATFORM_LABELS.xiaohongshu).toBe('小红书')
    expect(SCRAPER_PLATFORM_LABELS.douyin).toBe('抖音')
    expect(SCRAPER_PLATFORM_LABELS.browser_extension).toBe('浏览器插件')
  })
})

describe('formatDuration', () => {
  it('各时长范围', () => {
    expect(formatDuration(5)).toBe('5 秒')
    expect(formatDuration(90)).toBe('2 分钟')
    expect(formatDuration(3600)).toBe('1 小时')
    expect(formatDuration(5400)).toBe('1 小时 30 分')
  })

  it('非法输入返回空串', () => {
    expect(formatDuration(0)).toBe('')
    expect(formatDuration(NaN)).toBe('')
    expect(formatDuration(-5)).toBe('')
  })
})
