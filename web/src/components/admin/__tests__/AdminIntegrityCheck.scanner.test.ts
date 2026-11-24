/**
 * 数据完整性卡片：扫描器来源标签（Rust 加速 / Python 扫描）的边界与既有交互回归。
 *
 * 分支 feat/rust-media-worker 给 AdminIntegrityCheck 增加了可选 scanner prop，
 * 用来告诉管理员本次巡检是 Rust sidecar（storage-audit）还是 Python 回退：
 *
 * 1. 不传 / 传空串 → 不显示来源标签（旧后端响应没有 scanner 字段，前端保持原样，
 *    不能凭空显示「Python 扫描」）；
 * 2. 'rust' → 绿色「Rust 加速」；
 * 3. 其它任何值（'python'、未知值如 'legacy'）→ 灰色「Python 扫描」，
 *    这样后端未来新增扫描器时前端也不会显示英文原始值。
 *
 * 同时锁住缺失文件 / 孤立文件表格、loading、重新检查与清理孤立文件入口，
 * 防止新增标签时挤坏卡片头部的操作区。
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ArcoVue from '@arco-design/web-vue'

import type { MissingFile, OrphanFile } from '@/types/admin'
import AdminIntegrityCheck from '../AdminIntegrityCheck.vue'

const MISSING_FILES: MissingFile[] = [
  { file_path: 'storage/images/missing-a.jpg', inspiration_ids: ['ins-1', 'ins-2'] },
  { file_path: 'storage/images/missing-b.jpg', inspiration_ids: ['ins-3'] },
]

const ORPHAN_FILES: OrphanFile[] = [{ file_path: 'storage/images/orphan-a.jpg', size_bytes: 2048 }]

interface MountOptions {
  missingFiles?: MissingFile[]
  orphanFiles?: OrphanFile[]
  orphanBytes?: number
  checking?: boolean
  /** 是否显式传入 scanner（区分「不传」与「传空串」两种旧数据形态） */
  withScanner?: boolean
  scanner?: string
}

function mountCard(options: MountOptions = {}) {
  const props = {
    missingFiles: options.missingFiles ?? [],
    orphanFiles: options.orphanFiles ?? [],
    orphanBytes: options.orphanBytes ?? 0,
    checking: options.checking ?? false,
    ...(options.withScanner ? { scanner: options.scanner } : {}),
  }
  return mount(AdminIntegrityCheck, {
    props,
    global: { plugins: [ArcoVue] },
  })
}

type CardWrapper = ReturnType<typeof mountCard>

/** 卡片头部唯一可能出现的来源标签 */
function findScannerTag(wrapper: CardWrapper) {
  return wrapper.find('.arco-tag')
}

function findButtonByText(wrapper: CardWrapper, text: string) {
  return wrapper.findAll('button').find((button) => button.text().includes(text))
}

describe('AdminIntegrityCheck 扫描器来源标签', () => {
  it('不传 scanner 时不渲染任何来源标签', () => {
    const wrapper = mountCard()

    expect(findScannerTag(wrapper).exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Rust 加速')
    expect(wrapper.text()).not.toContain('Python 扫描')
  })

  it('scanner 为空串时不渲染标签（空串兜底属于视图的职责）', () => {
    const wrapper = mountCard({ withScanner: true, scanner: '' })

    expect(findScannerTag(wrapper).exists()).toBe(false)
  })

  it('scanner=rust 显示绿色的「Rust 加速」', () => {
    const wrapper = mountCard({ withScanner: true, scanner: 'rust' })
    const tag = findScannerTag(wrapper)

    expect(tag.exists()).toBe(true)
    expect(tag.text()).toBe('Rust 加速')
    expect(tag.classes()).toContain('arco-tag-green')
    expect(tag.classes()).not.toContain('arco-tag-gray')
  })

  it('scanner=python 显示灰色的「Python 扫描」', () => {
    const wrapper = mountCard({ withScanner: true, scanner: 'python' })
    const tag = findScannerTag(wrapper)

    expect(tag.text()).toBe('Python 扫描')
    expect(tag.classes()).toContain('arco-tag-gray')
    expect(tag.classes()).not.toContain('arco-tag-green')
  })

  it('scanner 为未知值（legacy）时走非 rust 分支，显示灰色「Python 扫描」', () => {
    const wrapper = mountCard({ withScanner: true, scanner: 'legacy' })
    const tag = findScannerTag(wrapper)

    expect(tag.text()).toBe('Python 扫描')
    expect(tag.classes()).toContain('arco-tag-gray')
    expect(wrapper.text()).not.toContain('Rust 加速')
  })
})

describe('AdminIntegrityCheck 既有展示不受来源标签影响', () => {
  it('无数据且不在检查中：显示空状态，且不渲染表格', () => {
    const wrapper = mountCard()

    expect(wrapper.text()).toContain('数据完整，未发现缺失或孤立文件')
    expect(wrapper.findAll('.arco-table-tr')).toHaveLength(0)
  })

  it('missingFiles 非空：渲染缺失文件标题、表格行与关联素材数', () => {
    const wrapper = mountCard({ missingFiles: MISSING_FILES })

    expect(wrapper.text()).toContain('缺失文件 (2)')
    expect(wrapper.text()).toContain('数据库有记录但文件不存在')
    expect(wrapper.text()).toContain('storage/images/missing-a.jpg')
    expect(wrapper.text()).toContain('storage/images/missing-b.jpg')
    // 关联素材数由 render 函数给出：2 与 1
    expect(wrapper.text()).toContain('关联素材数')
    expect(wrapper.findAll('tbody .arco-table-tr')).toHaveLength(2)
    expect(wrapper.text()).not.toContain('数据完整，未发现缺失或孤立文件')
  })

  it('orphanFiles 非空：渲染孤立文件标题、表格行、大小与清理入口', () => {
    const wrapper = mountCard({ orphanFiles: ORPHAN_FILES, orphanBytes: 2048 })

    expect(wrapper.text()).toContain('孤立文件 (1)')
    expect(wrapper.text()).toContain('磁盘有文件但数据库无记录')
    expect(wrapper.text()).toContain('storage/images/orphan-a.jpg')
    // 表格 render 用 formatSize(2048) = '2 KB'；说明文字用 formatBytes(2048) = '2.0 KB'
    expect(wrapper.text()).toContain('2 KB')
    expect(wrapper.text()).toContain('2.0 KB')
    expect(wrapper.findAll('tbody .arco-table-tr')).toHaveLength(1)

    const cleanButton = findButtonByText(wrapper, '清理孤立文件 (1)')
    expect(cleanButton).toBeDefined()
    // 孤立文件非空时才有 Popconfirm（防止误点直接清空）
    expect(wrapper.findComponent({ name: 'Popconfirm' }).exists()).toBe(true)
    expect(wrapper.text()).not.toContain('数据完整，未发现缺失或孤立文件')
  })

  it('checking=true 时「重新检查」按钮进入 loading，且不显示空状态', async () => {
    const wrapper = mountCard({ checking: true })

    expect(findButtonByText(wrapper, '重新检查')?.classes()).toContain('arco-btn-loading')
    expect(wrapper.text()).not.toContain('数据完整，未发现缺失或孤立文件')

    await wrapper.setProps({ checking: false })
    expect(findButtonByText(wrapper, '重新检查')?.classes()).not.toContain('arco-btn-loading')
  })

  it('点击「重新检查」只 emit recheck，不触发清理', async () => {
    const wrapper = mountCard()

    const recheckButton = findButtonByText(wrapper, '重新检查')
    expect(recheckButton).toBeDefined()
    await recheckButton!.trigger('click')

    expect(wrapper.emitted('recheck')).toHaveLength(1)
    expect(wrapper.emitted('cleanOrphans')).toBeUndefined()
  })

  it('确认清理孤立文件时 emit cleanOrphans', async () => {
    const wrapper = mountCard({ orphanFiles: ORPHAN_FILES, orphanBytes: 2048 })

    // Popconfirm 的确认按钮渲染在弹出层里（Teleport 到 body），这里直接触发确认事件，
    // 验证卡片「确认后才清理」的接线，避免依赖弹出层的 DOM 结构。
    const popconfirm = wrapper.findComponent({ name: 'Popconfirm' })
    expect(popconfirm.exists()).toBe(true)
    popconfirm.vm.$emit('ok')
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('cleanOrphans')).toHaveLength(1)
    expect(wrapper.emitted('recheck')).toBeUndefined()
  })
})
