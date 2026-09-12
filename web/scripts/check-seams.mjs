#!/usr/bin/env node
/**
 * 机器可查的红线。这几条不是洁癖，每一条都对应一次真实的返工：
 *
 *  1. 屏 / 组件里不许 fetch —— 数据只有一条路：src/api/endpoints.ts
 *  2. 屏 / 组件里不许出现颜色字面量 —— 状态色只从 src/constants/vocabulary.ts 取，
 *     其余从 Tailwind @theme 令牌取。两份颜色表一定会漂移。
 *  3. src/mocks/*.json 只许 src/api/mock.ts 读 —— 组件里出现内联假数据就算不合格
 *  4. src/mocks/ 必须和统一契约登记的 20 份字节数 + SHA-256 一致（冻结证据基线）
 *  5. src/fixtures/ 只许 src/api/mock.ts 读 —— plot_v1 是完整剧情链的现役数据源，
 *     不是屏幕的（屏幕连 fixture 长什么样都不该知道）
 *  6. 现役 TypeScript 不保留原子事实、hinge 或逐事实落点契约。
 */
import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const BASELINE_JSON = join(ROOT, 'scripts', 'mock-baseline.json')

/**
 * 冻结基线的字节真值。
 *
 * 这份数据此前写在 docs/BACKEND_CONTRACT.md §八 的 markdown 表格里，由本脚本正则解析。
 * 换成 JSON 不是为了好看：那张表**同时**承担人读的散文和机器读的数据两种职责，
 * 于是它既不能自由排版（改一下对齐就可能解析不到），坏了又只表现为"解析到 N 行"——
 * 而 N 恰好等于 20 的那种坏法不会被发现。现在文档只讲来历与纪律，字节真值只有这一份。
 */
function mockBaseline() {
  const raw = JSON.parse(readFileSync(BASELINE_JSON, 'utf8'))
  const rows = new Map()
  for (const r of raw.files ?? []) {
    if (!/^[0-9a-f]{64}$/.test(r.sha256 ?? '')) {
      throw new Error('mock 基线 sha256 形状不对：' + r.file)
    }
    if (!Number.isInteger(r.bytes)) throw new Error('mock 基线 bytes 不是整数：' + r.file)
    if (rows.has(r.file)) throw new Error('mock 基线文件名重复：' + r.file)
    rows.set(r.file, { bytes: r.bytes, sha256: r.sha256 })
  }
  // count 是**声明的**份数，rows.size 是**实际读到的**。两者都对上 20 才算数据完整：
  // 只查后者的话，manifest 少一行同时 count 也少一行，就会静默通过。
  if (rows.size !== raw.count) {
    throw new Error(`mock 基线 count=${raw.count} 与实际 ${rows.size} 行不符`)
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
      problems.push([at, 'src/fixtures/ 只许 src/api/mock.ts 读（fixture 不是屏幕数据层）'])
    }
    if (!isComment && /\b(?:fact_atoms?|land_fact_refs|must_land_fact_refs|hinges|hinge_kinds|hinge_groups|fact_atom_kinds|fact_atom_obligations)\b/.test(code)) {
      problems.push([at, '现役前端不能消费已退役的原子事实、hinge 或逐事实落点契约'])
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
