#!/usr/bin/env node
/**
 * 提交信息格式校验（与 CLAUDE.md「Git 提交格式」约定一致）：
 *   <类型>: <简短描述>
 *
 * 类型：feat / fix / refactor / style / docs / chore / ci / perf / test / revert
 * 放行：Merge / Revert 等 Git 自动生成的提交。
 */

import { readFileSync } from 'node:fs'

const file = process.argv[2]
if (!file) process.exit(0) // 非 git 调用（无参数）直接放行

const msg = readFileSync(file, 'utf8').replace(/\r\n/g, '\n')
// 忽略空行与 git 的 # 注释行（COMMIT_EDITMSG 的模板说明）
const subject = (
  msg.split('\n').find((l) => !l.trim().startsWith('#') && l.trim() !== '') ?? ''
).trim()

// 放行 Git 自动生成的合并/回滚提交
if (/^(Merge|Revert)\b/.test(subject)) process.exit(0)

const valid = /^(feat|fix|refactor|style|docs|chore|ci|perf|test|revert)(\(.+\))?: .+$/.test(
  subject,
)
if (!valid) {
  console.error(
    `❌ 提交信息格式不符合项目约定（见 CLAUDE.md）：
  期望: <类型>: <简短描述>      例如: fix: 修复 xxx
  允许类型: feat / fix / refactor / style / docs / chore / ci / perf / test / revert
  当前: ${subject || '(空)'}`,
  )
  process.exit(1)
}
console.log(`✅ 提交信息格式正确: ${subject}`)
