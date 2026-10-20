#!/usr/bin/env node
/** 前端格式检查（**只查本次改动文件**）。
 *
 * 为什么不再全库 `prettier --check src`：仓库里有 45 个历史文件没按 prettier 格式化，
 * 全库检查永远是红的（僵尸门禁），而 CLAUDE.md 明确禁止全库 `prettier --write`
 * （会产生数千行无关 diff 污染 git 历史）。提交时 lint-staged 已对**暂存**文件跑
 * `prettier --write`，所以真正需要检查的是「改了但还没提交 / 还没推」的文件。
 *
 * 用法：
 *   node scripts/check-web-format.mjs                 # 检查未提交改动 + 相对 origin/master 的改动
 *   node scripts/check-web-format.mjs origin/master   # 指定基线分支
 *   node scripts/check-web-format.mjs --files a.vue b.ts   # 只查指定文件（排查用）
 *
 * 退出码：0=通过或无文件；1=有文件未格式化（打印文件名与 prettier 建议）。
 */

import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'

const ROOT = path.resolve(import.meta.dirname, '..')
const PRETTIER = path.join(ROOT, 'web/node_modules/prettier/bin/prettier.cjs')
const EXT = new Set(['.ts', '.tsx', '.vue', '.js', '.mjs', '.cjs', '.css', '.json', '.md'])

const argv = process.argv.slice(2)
const filesFlag = argv.indexOf('--files')

function git(args) {
  return execFileSync('git', args, { cwd: ROOT, encoding: 'utf8' })
}

/** 收集「文件变更」候选：未提交（含暂存/未跟踪）+ 相对基线的已提交改动。 */
function changedFiles(base) {
  const out = new Set()
  const add = (text) =>
    text
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean)
      .forEach((l) => out.add(l))

  try {
    add(git(['diff', '--name-only', '--diff-filter=ACMR', 'HEAD']))
  } catch {
    /* 无 HEAD（首次提交）时忽略 */
  }
  try {
    add(git(['ls-files', '--others', '--exclude-standard']))
  } catch {
    /* 同上 */
  }
  if (base) {
    try {
      add(git(['diff', '--name-only', '--diff-filter=ACMR', `${base}...HEAD`]))
    } catch {
      console.log(`[skip] 基线 ${base} 不存在（或浅克隆），只检查未提交改动`)
    }
  }
  return [...out]
}

let candidates
if (filesFlag >= 0) {
  candidates = argv.slice(filesFlag + 1)
} else {
  const base = argv.find((a) => !a.startsWith('-')) || 'origin/master'
  candidates = changedFiles(base)
}

const target = candidates
  .map((f) => f.replaceAll('\\', '/'))
  // 只看 web/src：web/package.json 等配置文件不归 prettier 管，避免误报
  .filter((f) => f.startsWith('web/src/') && EXT.has(path.extname(f).toLowerCase()))
  .filter((f) => !f.includes('node_modules/') && !f.includes('/dist/'))
  .map((f) => path.join(ROOT, f))
  .filter((f) => existsSync(f))

if (target.length === 0) {
  console.log('[ok] 本次没有需要检查格式的前端文件')
  process.exit(0)
}

try {
  execFileSync(process.execPath, [PRETTIER, '--check', ...target], {
    cwd: ROOT,
    stdio: 'inherit',
  })
  console.log(`[ok] ${target.length} 个改动文件格式正确`)
} catch {
  console.error(
    `[fail] 上面列出的文件未按 prettier 格式化。` +
      `逐个修复：npx prettier --write <文件>（不要全库格式化）。`
  )
  process.exit(1)
}
