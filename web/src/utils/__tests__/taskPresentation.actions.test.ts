/**
 * 任务行操作矩阵（`taskRowActions`）的测试。
 *
 * 这个函数决定任务列表「操作」列显示哪些按钮，和后端 `/api/tasks` 的白名单靠**人工对齐**
 * （跨语言无法共用常量）。错一位的实际后果：
 * - 界面显示了后端不允许的按钮 → 用户点了拿到 400，以为坏了；
 * - 界面隐藏了后端允许的按钮 → 用户没法取消/删除，只能等。
 *
 * 所以这里断言的是「前后端契约」，不只是函数返回值：
 * 后端 `_CANCELABLE_RUNNING_TYPES` / `_PAUSABLE_RUNNING_TYPES` / `delete_task` 的
 * 判定规则逐条对应（见 backend/app/routers/tasks.py）。
 */

import { describe, expect, it } from 'vitest'
import {
  CANCELABLE_TASK_TYPES,
  PAUSABLE_TASK_TYPES,
  GPU_EXCLUSIVE_TASK_TYPES,
  isCancelableTaskType,
  isGpuExclusiveTaskType,
  isPausableTaskType,
  taskRowActions,
} from '../taskPresentation'

const CANCELABLE = 'batch_analyze' // 可暂停 + 可运行中取消
const NOT_CANCELABLE = 'batch_delete' // 不可暂停、不可运行中取消
const IMAGE_GEN = 'image_gen' // 可取消但不可暂停（与后端一致）

describe('taskRowActions：前端按钮 ↔ 后端白名单', () => {
  it('运行中 + 可取消类型 → 可取消，不可删除', () => {
    expect(taskRowActions('running', CANCELABLE, 'queue')).toMatchObject({
      canCancel: true,
      canPause: true,
      canResume: false,
      canDelete: false,
    })
  })

  it('运行中 + 不在白名单 → 无取消/暂停（后端也会 400）', () => {
    expect(taskRowActions('running', NOT_CANCELABLE, 'queue')).toMatchObject({
      canCancel: false,
      canPause: false,
    })
  })

  it('已暂停 + 可暂停类型 → 可恢复、可取消', () => {
    expect(taskRowActions('paused', CANCELABLE, 'queue')).toMatchObject({
      canResume: true,
      canCancel: true,
      canPause: false,
    })
  })

  it('已暂停 + 不可暂停类型 → 不能恢复也不能取消', () => {
    expect(taskRowActions('paused', NOT_CANCELABLE, 'queue')).toMatchObject({
      canResume: false,
      canCancel: false,
    })
  })

  it('出图任务：可取消但不可暂停（image_gen 只在取消白名单里）', () => {
    expect(isCancelableTaskType(IMAGE_GEN)).toBe(true)
    expect(isPausableTaskType(IMAGE_GEN)).toBe(false)
    expect(taskRowActions('running', IMAGE_GEN, 'queue')).toMatchObject({
      canCancel: true,
      canPause: false,
    })
  })

  it('采集任务不可取消（走采集专用接口，不能在任务中心取消）', () => {
    expect(taskRowActions('running', 'scraper', 'scraper')).toMatchObject({
      canCancel: false,
    })
  })

  it('pending 队列任务 → canDelete 为真（该按钮实际发 cancel，对 pending 即物理删除）', () => {
    // 与后端一致：delete 接口拒绝 pending，但 cancel 对 pending 就是物理删除，
    // 所以界面在 pending 上给的是「删除」入口（emit 的是 cancel）。
    expect(taskRowActions('pending', CANCELABLE, 'queue')).toMatchObject({
      canDelete: true,
      canCancel: false,
      canPause: false,
    })
  })

  it('终态任务没有 canDelete 标记（组件另行渲染「删除任务」，覆盖 success/failed/cancelled/paused）', () => {
    // 注意：这里锁的是**当前实现的分工**——taskRowActions.canDelete 只标记 pending，
    // 终态/paused 的「删除任务」按钮由 TaskList 组件内联条件渲染（见 TaskList.vue）。
    // 若将来把两者合并，这条用例应随之更新。
    for (const status of ['success', 'failed', 'cancelled', 'paused']) {
      expect(taskRowActions(status, CANCELABLE, 'queue').canDelete).toBe(false)
    }
  })
})

describe('三份白名单的内容（与后端常量逐条对齐）', () => {
  it('可取消清单与后端 _CANCELABLE_RUNNING_TYPES 一致', () => {
    expect([...CANCELABLE_TASK_TYPES].sort()).toEqual(
      [
        'batch_analyze',
        'face_match',
        'face_scan',
        'f2_import',
        'image_gen',
        'multi_analyze',
        'quality_check',
        'tag_network_analyze',
        'vector_backfill',
      ].sort(),
    )
  })

  it('可暂停清单与后端 _PAUSABLE_RUNNING_TYPES 一致（不含 image_gen）', () => {
    expect([...PAUSABLE_TASK_TYPES].sort()).toEqual(
      [
        'batch_analyze',
        'f2_import',
        'multi_analyze',
        'quality_check',
        'tag_network_analyze',
        'vector_backfill',
      ].sort(),
    )
    expect(PAUSABLE_TASK_TYPES).not.toContain('image_gen')
  })

  it('重负载互斥组与后端 GPU_EXCLUSIVE_TASK_TYPES 一致', () => {
    expect([...GPU_EXCLUSIVE_TASK_TYPES].sort()).toEqual(
      [
        'batch_analyze',
        'face_match',
        'face_scan',
        'image_gen',
        'multi_analyze',
        'quality_check',
        'vector_backfill',
      ].sort(),
    )
  })

  it('可暂停集合必须是可取消集合的子集（否则暂停后无法取消，任务卡死）', () => {
    for (const type of PAUSABLE_TASK_TYPES) {
      expect(isCancelableTaskType(type), `${type} 可暂停但不可取消`).toBe(true)
    }
  })

  it('出图与打标同属重负载组（抢同一块显存）', () => {
    expect(isGpuExclusiveTaskType('image_gen')).toBe(true)
    expect(isGpuExclusiveTaskType('batch_analyze')).toBe(true)
    expect(isGpuExclusiveTaskType('batch_delete')).toBe(false)
  })
})
