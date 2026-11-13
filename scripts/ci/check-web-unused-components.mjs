#!/usr/bin/env node
/**
 * Vue 组件零引用检查。
 *
 * 背景：`check-web-dead-code.mjs` 使用 ts-prune，只分析 TS/JS 导出，不解析 `.vue`
 * 模板里的组件使用，因此「无人 import 的 Vue 组件」不会被现有门禁拦住。本脚本补这一层：
 *
 * - 扫描 `web/src` 下所有 `.ts/.js/.vue` 源码（跳过 `__tests__` 与 `*.test.*`，测试引用
 *   不算生产使用）；
 * - 收集所有 `from '...vue'` / `import('...vue')` 的引用并解析成绝对路径；
 * - 与 `web/src/components/**\/*.vue` 对账，零引用组件报错；
 * - 动态 `<component :is>` 等无法静态解析的少量组件可登记到
 *   `scripts/ci/web-component-allowlist.txt`（一行一个仓库相对路径）。
 *
 * 为什么不用第三方工具：与仓库现状一致，零原生依赖、可直接在 Windows 本地跑；
 * 规则只做「组件文件是否被任何生产源码引用」这一件事，保持低维护成本。
 */

import { readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '../..')
const webSrc = path.join(root, 'web/src')
const componentsDir = path.join(webSrc, 'components')
const allowlistPath = path.join(import.meta.dirname, 'web-component-allowlist.txt')

/** 递归收集指定后缀的普通文件（跳过符号链接与 __tests__ 目录） */
function walk(dir, exts, files = []) {
  for (const name of readdirSync(dir)) {
    const full = path.join(dir, name)
    const stat = statSync(full)
    if (stat.isDirectory()) {
      if (name === 'node_modules' || name === '__tests__') continue
      walk(full, exts, files)
    } else if (exts.has(path.extname(name))) {
      files.push(full)
    }
  }
  return files
}

/** 仓库相对路径统一使用 /，便于跨平台输出与白名单比对 */
function rel(file) {
  return path.relative(root, file).replaceAll('\\', '/')
}

/** 判断测试文件：测试里 import 组件只是为了让组件被单测，不能算生产引用 */
function isTestFile(file) {
  return /(?:^|[\\/])__tests__[\\/]|\.(?:test|spec)\.[^.]+$/.test(file)
}

const allowlist = new Set(
  (() => {
    try {
      return readFileSync(allowlistPath, 'utf8')
        .split('\n')
        .map((line) => line.trim())
        .filter((line) => line && !line.startsWith('#'))
    } catch {
      return []
    }
  })(),
)

const sourceFiles = walk(webSrc, new Set(['.ts', '.js', '.mjs', '.vue'])).filter(
  (file) => !isTestFile(file),
)
const allComponents = walk(componentsDir, new Set(['.vue']))

/** 解析 import specifier：只处理 @/ 别名与相对路径，其余（npm 包）忽略 */
function resolveSpecifier(spec, fromFile) {
  if (spec.startsWith('@/')) return path.join(webSrc, spec.slice(2))
  if (spec.startsWith('.')) return path.resolve(path.dirname(fromFile), spec)
  return null
}

const referenced = new Set()
const importRe = /(?:from\s+|import\s*\(\s*)['"]([^'"]+\.vue)['"]/g
for (const file of sourceFiles) {
  const text = readFileSync(file, 'utf8')
  for (const match of text.matchAll(importRe)) {
    const target = resolveSpecifier(match[1], file)
    if (target) referenced.add(path.normalize(target))
  }
}

const unused = allComponents
  .filter((file) => !referenced.has(path.normalize(file)) && !allowlist.has(rel(file)))
  .map(rel)
  .sort()

if (unused.length > 0) {
  console.error(`发现 ${unused.length} 个零引用 Vue 组件：`)
  for (const file of unused) console.error(`  - ${file}`)
  console.error(
    '\n若组件确实通过动态 <component :is> 等方式使用，请登记到 ' +
      'scripts/ci/web-component-allowlist.txt 并注明原因；不要直接删白名单里的旧条目。',
  )
  process.exit(1)
}

console.log(`Vue 组件引用检查通过（${allComponents.length} 个组件均有生产引用）`)
