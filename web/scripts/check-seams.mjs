#!/usr/bin/env node
/**
 * 机器可查的红线。这几条不是洁癖，每一条都对应一次真实的返工：
 *
 *  1. 屏 / 组件里不许 fetch —— 数据只有一条路：src/api/endpoints.ts
 *  2. 屏 / 组件里不许出现颜色字面量 —— 状态色只从 src/constants/vocabulary.ts 取，
 *     其余从 Tailwind @theme 令牌取。两份颜色表一定会漂移。
 *  3. src/mocks/*.json 只许 src/api/mock.ts 读 —— 组件里出现内联假数据就算不合格
 *  4. src/mocks/ 必须和交接包里的 20 份逐字节一致（冻结的证据基线，FE-06）
 *  5. src/fixtures/ 只许 src/api/mock.ts 读 —— backend_v1 是 mock 的现役数据源，
 *     不是屏幕的（屏幕连 fixture 长什么样都不该知道）
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const HANDOFF = join(ROOT, '..', 'design_handoff_novel_refactor_console', 'mocks')

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else out.push(p)
  }
  return out
}

const files = walk(join(ROOT, 'src')).filter((f) => /\.(ts|tsx)$/.test(f))
const problems = []

for (const f of files) {
  const rel = relative(ROOT, f).replace(/\\/g, '/')
  const src = readFileSync(f, 'utf8')
  const lines = src.split('\n')

  const isApi = rel.startsWith('src/api/')
  const isVocab = rel === 'src/constants/vocabulary.ts'
  const isScreenOrComponent = rel.startsWith('src/screens/') || rel.startsWith('src/components/')

  lines.forEach((line, i) => {
    const at = rel + ':' + (i + 1)
    const code = line.replace(/\/\*.*?\*\//g, '')
    const isComment = /^\s*(\/\/|\*|\/\*)/.test(code)

    if (!isApi && /\bfetch\s*\(/.test(code) && !isComment) {
      problems.push([at, '组件 / 屏里不许 fetch，数据只从 src/api/endpoints.ts 来'])
    }
    if (!isApi && rel !== 'src/api/mock.ts' && /from ['"].*mocks\//.test(code)) {
      problems.push([at, 'src/mocks/ 只许 src/api/mock.ts 读'])
    }
    if (rel !== 'src/api/mock.ts' && /['"][^'"]*fixtures\//.test(code) && !isComment) {
      problems.push([at, 'src/fixtures/ 只许 src/api/mock.ts 读（backend_v1 是 mock 的数据源）'])
    }
    if (isScreenOrComponent && !isVocab && !isComment) {
      // 颜色字面量：#rgb / #rrggbb / oklch(...) / rgb(...)
      if (/#[0-9a-fA-F]{3,8}\b/.test(code) || /\boklch\(/.test(code) || /\brgba?\(/.test(code)) {
        problems.push([at, '颜色字面量：状态色走 vocabulary.ts，其余走 @theme 令牌'])
      }
    }
  })
}

// mocks 逐字节比对
let mockDiff = []
try {
  const a = readdirSync(HANDOFF).filter((f) => f.endsWith('.json')).sort()
  const b = readdirSync(join(ROOT, 'src/mocks')).filter((f) => f.endsWith('.json')).sort()
  if (a.join(',') !== b.join(',')) mockDiff.push('文件清单不一致')
  for (const f of a) {
    if (!b.includes(f)) continue
    const x = readFileSync(join(HANDOFF, f))
    const y = readFileSync(join(ROOT, 'src/mocks', f))
    if (!x.equals(y)) mockDiff.push(f + ' 内容被改过')
  }
} catch (e) {
  mockDiff.push('比对不了：' + e.message)
}

if (problems.length === 0 && mockDiff.length === 0) {
  console.log('数据缝 / 设计缝检查通过（' + files.length + ' 个文件，mocks 20 份逐字节一致）')
  process.exit(0)
}

for (const [at, why] of problems) console.log(at + '  ' + why)
for (const m of mockDiff) console.log('mocks: ' + m)
console.log('\n' + (problems.length + mockDiff.length) + ' 处不合格')
process.exit(1)
