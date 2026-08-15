#!/usr/bin/env node
/**
 * 机器可查的红线。这几条不是洁癖，每一条都对应一次真实的返工：
 *
 *  1. 屏 / 组件里不许 fetch —— 数据只有一条路：src/api/endpoints.ts
 *  2. 屏 / 组件里不许出现颜色字面量 —— 状态色只从 src/constants/vocabulary.ts 取，
 *     其余从 Tailwind @theme 令牌取。两份颜色表一定会漂移。
 *  3. src/mocks/*.json 只许 src/api/mock.ts 读 —— 组件里出现内联假数据就算不合格
 *  4. src/mocks/ 必须和统一契约登记的 20 份字节数 + SHA-256 一致（冻结证据基线）
 *  5. src/fixtures/ 只许 src/api/mock.ts 读 —— backend_v1 是 mock 的现役数据源，
 *     不是屏幕的（屏幕连 fixture 长什么样都不该知道）
 */
import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const BASELINE_DOC = join(ROOT, '..', 'docs', 'BACKEND_CONTRACT.md')
const BASELINE_START = '<!-- MOCK_BASELINE_V1_START -->'
const BASELINE_END = '<!-- MOCK_BASELINE_V1_END -->'

function mockBaseline() {
  const doc = readFileSync(BASELINE_DOC, 'utf8')
  const start = doc.indexOf(BASELINE_START)
  const end = doc.indexOf(BASELINE_END)
  if (start < 0 || end <= start) throw new Error('统一契约缺少完整的 mock 基线标记')
  const block = doc.slice(start + BASELINE_START.length, end)
  const rows = new Map()
  const row = /^\|\s*`([^`]+\.json)`\s*\|\s*(\d+)\s*\|\s*`([0-9a-f]{64})`\s*\|$/gm
  for (const m of block.matchAll(row)) {
    if (rows.has(m[1])) throw new Error('mock 基线文件名重复：' + m[1])
    rows.set(m[1], { bytes: Number(m[2]), sha256: m[3] })
  }
  if (rows.size !== 20) throw new Error('mock 基线应有 20 份，实际解析到 ' + rows.size)
  return rows
}

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

// mocks 与统一契约里的字节数 + SHA-256 比对。实际样本字节只保留 src/mocks/ 一份，
// 避免三份重复证据相互比较；manifest 是守卫输入，样本本身是可运行证据。
let mockDiff = []
try {
  const baseline = mockBaseline()
  const actual = readdirSync(join(ROOT, 'src/mocks')).filter((f) => f.endsWith('.json')).sort()
  const expected = [...baseline.keys()].sort()
  if (expected.join(',') !== actual.join(',')) mockDiff.push('文件清单与统一契约不一致')
  for (const f of expected) {
    if (!actual.includes(f)) continue
    const bytes = readFileSync(join(ROOT, 'src/mocks', f))
    const want = baseline.get(f)
    const sha256 = createHash('sha256').update(bytes).digest('hex')
    if (bytes.length !== want.bytes) mockDiff.push(f + ' 字节数变化')
    if (sha256 !== want.sha256) mockDiff.push(f + ' SHA-256 变化')
  }
} catch (e) {
  mockDiff.push('比对不了：' + e.message)
}

if (problems.length === 0 && mockDiff.length === 0) {
  console.log('数据缝 / 设计缝检查通过（' + files.length + ' 个文件，mocks 20 份基线匹配）')
  process.exit(0)
}

for (const [at, why] of problems) console.log(at + '  ' + why)
for (const m of mockDiff) console.log('mocks: ' + m)
console.log('\n' + (problems.length + mockDiff.length) + ' 处不合格')
process.exit(1)
