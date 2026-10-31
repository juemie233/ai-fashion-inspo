#!/usr/bin/env node
/** 前端死代码检查（ts-prune + 基线比对，只拦新增）。
 *
 * 为什么不用 knip：本仓库的 npm 可选依赖装不上 native binding（`Cannot find native binding`），
 * 修复要删 node_modules + package-lock 重装，破坏性太大。ts-prune 无原生依赖，覆盖「未使用导出」。
 *
 * 路径归一化：ts-prune 会混用绝对路径与「相对 web/」两种形态，因此统一按**仓库目录名截断**
 * （`fashion-inspo/` 之后即仓库内相对路径），与基线生成脚本保持一致。
 */

import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '../..')
const mark = path.basename(root)
const cut = (p) => {
  const s = p.replaceAll('\\', '/')
  // 用 lastIndexOf 而不是 split：CI 的检出路径是 /home/runner/work/<repo>/<repo>/...，
  // 仓库名会出现**两次**，取第一个会把剩下的 `fashion-inspo/...` 当结果（实测返回空）。
  // 取最后一次出现，才能稳定拿到仓库内的相对路径。
  const idx = s.lastIndexOf(`${mark}/`)
  const inner = idx >= 0 ? s.slice(idx + mark.length + 1) : s
  return (
    inner
      // 相对 web/ 的形态（`../shared/types/x.ts`）→ 仓库根相对
      .replace(/^(\.\.\/)+/, '')
      .replace(/^\//, '')
      // 绝对路径截断后会带 `web/` 前缀；本地相对路径不会。统一去掉，与基线形态一致。
      .replace(/^web\//, '')
  )
}

const baseline = new Set(
  readFileSync(path.join(root, 'scripts/ci/tsprune-baseline.txt'), 'utf8')
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#')),
)

// 基线条数上限（棘轮）：基线只能降不能升。
// 只有「只拦新增」时，上一行「若确为刻意保留，请加入基线」就成了后门——每加一条死代码
// 都能用一句「刻意保留」把基线撑大。这里把上限钉死：真要加例外，必须同时删掉一条
// 已修好的旧条目（或在这个常量上显式改数字，让 review 看见）。
const MAX_BASELINE = 275

let raw = ''
try {
  // 钉版本：ts-prune 升级会改变报告格式/路径形态，导致基线整体失配（门禁瞬间报「新增 268 条」）
  raw = execFileSync('npx', ['-y', 'ts-prune@0.10.3', '-p', 'tsconfig.json'], {
    cwd: path.join(root, 'web'),
    encoding: 'utf8',
    shell: process.platform === 'win32',
  })
} catch (e) {
  raw = `${e.stdout ?? ''}${e.stderr ?? ''}`
}

const current = new Set()
for (const line of raw.split('\n')) {
  const s = line.trim()
  if (!s || s.startsWith('npm ')) continue
  const m = s.match(/^(.*):(\d+) - (.*)$/)
  if (!m) continue
  // 不含行号：插入代码会让后续行号整体位移，行号不能当标识
  current.add(`${cut(m[1])} - ${m[3]}`)
}

const added = [...current].filter((l) => !baseline.has(l)).sort()
if (added.length > 0) {
  console.error(`前端新增死代码 ${added.length} 条：`)
  for (const l of added.slice(0, 20)) console.error(`  ${l}`)
  if (added.length > 20) console.error(`  …另有 ${added.length - 20} 条`)
  console.error(
    '\n若确为刻意保留，请加入 scripts/ci/tsprune-baseline.txt 并注明原因；' +
      '但基线有上限（MAX_BASELINE），加一条就得先删一条已修好的。'
  )
  process.exit(1)
}

if (baseline.size > MAX_BASELINE) {
  console.error(
    `基线膨胀：${baseline.size} 条 > 上限 ${MAX_BASELINE} 条。` +
      '\n基线只能降不能升：请把已修好/已删除的条目从 scripts/ci/tsprune-baseline.txt 删掉。'
  )
  process.exit(1)
}

console.log(
  `前端死代码检查通过（基线 ${baseline.size}/${MAX_BASELINE} 条，无新增；当前报告 ${current.size} 条）`
)

