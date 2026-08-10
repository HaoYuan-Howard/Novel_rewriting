/**
 * 假后端。
 *
 * 这是**整个前端里唯一 import src/mocks/*.json 的文件**，也是唯一碰 localStorage 的文件。
 * 它不是「fixture 加载器」，是一个真的会写盘（写 localStorage）、会跑作业、会拒绝
 * 「这一份不在样本里」的服务端替身 —— 所以屏与组件在 mock 模式下走的代码路径
 * 和接了真后端之后**完全一样**。
 *
 * 三件事故意做在这里而不是渲染层：
 *   1. 「样本只抓了一本书、第 3 章」这个知识 —— 它是数据的事实，不是界面的事实
 *   2. 命令行的拼装 —— U-09 要求确认框里那条命令由服务端下发
 *   3. 花不花钱、会不会写盘 —— 前端一行判定都不许有
 */
import type { Api } from '@/types/api'
import type { ActionRef, ApprovalDoc, GateKey, SeenKey } from '@/types/approvals'
import type { EnumsResponse } from '@/types/enums'
import type { Job, JobLogResponse, JobPreview, JobRequest, JobsResponse } from '@/types/jobs'
import type { NovelOverview, NovelsResponse } from '@/types/novels'
import type {
  CheckKind,
  CheckReport,
  InboxResponse,
  ReadingResponse,
  ReviewResponse,
  Stage0Extras,
  Stage1Chapters,
  Stage1HookCopy,
  Stage1Report,
  Stage2Global,
  Stage3Style,
  Stage4Response,
  Stage5Response,
  Stage6Response,
  Stage7Response,
} from '@/types/stages'
import { lookupAction } from '@/constants/actions'
import { ApiError } from './errors'

/* ------------------------------------------------------------ 样本的边界 */

/** 样本抓自这一本 8 章的书 */
export const SAMPLE_SID = '7615069877019300633'
/** 逐章响应只抓了第 3 章 */
export const SAMPLE_ORDINAL = 3

/* -------------------------------------------------------------- 20 份原样 */

const RAW = import.meta.glob('../mocks/*.json', { eager: true, import: 'default' }) as Record<
  string,
  unknown
>

function raw<T>(file: string): T {
  const hit = RAW['../mocks/' + file]
  if (hit === undefined) throw new ApiError('malformed', '/mock/' + file)
  // 一次性穿过手写类型。Vite 会把 JSON 推成巨型字面量类型，
  // 让它进类型系统会把 tsc 拖垮，也会让 types/ 失去「契约」的意义。
  return hit as T
}

/* ---------------------------------------------------------------- 延迟 */

const LATENCY = 120

function later<T>(v: T): Promise<T> {
  return new Promise((res) => setTimeout(() => res(v), LATENCY))
}

function fail(err: ApiError): Promise<never> {
  return new Promise((_res, rej) => setTimeout(() => rej(err), LATENCY))
}

/** 这本书不在样本里 */
function noBook(path: string): Promise<never> {
  return fail(new ApiError('nosample', path, { scope: 'book' }))
}

/** 这一章不在样本里 */
function noChapter(path: string): Promise<never> {
  return fail(new ApiError('nosample', path, { scope: 'chapter' }))
}

function bookOnly<T>(sid: string, path: string, load: () => T): Promise<T> {
  if (sid !== SAMPLE_SID) return noBook(path)
  return later(load())
}

function chapterOnly<T>(sid: string, o: number, path: string, load: () => T): Promise<T> {
  if (sid !== SAMPLE_SID) return noBook(path)
  if (o !== SAMPLE_ORDINAL) return noChapter(path)
  return later(load())
}

/* ------------------------------------------------------- localStorage 层 */

const LS_PREFIX = 'nrc.v1.'

function lsRead<T>(key: string): T | null {
  try {
    const s = localStorage.getItem(LS_PREFIX + key)
    return s ? (JSON.parse(s) as T) : null
  } catch {
    return null
  }
}

function lsWrite(key: string, v: unknown): void {
  try {
    localStorage.setItem(LS_PREFIX + key, JSON.stringify(v))
  } catch {
    /* 隐私模式下写不进去也不该崩 */
  }
}

/* ------------------------------------------------------------ 验收记录 */

function freshDoc(sid: string): ApprovalDoc {
  return {
    v: 1,
    sid,
    gate: { s0: false, s1: false, s2: false, s3: false, s4: false, s5: false, s6: false, s7: false },
    seen: { read: false, global: false },
    deep: [1, 2, 3],
    writing: false,
    spawns: SPAWN_ON_APPROVE,
  }
}

/**
 * 样本那本书的初始状态：已经跑到 stage5，文案与成书还没确认。
 * 这样一打开就能看到一本走到一半的书 —— 全 false 的空壳看不出这个工具在干什么。
 */
function seededDoc(sid: string): ApprovalDoc {
  return {
    v: 1,
    sid,
    gate: { s0: true, s1: true, s2: true, s3: true, s4: true, s5: true, s6: false, s7: false },
    seen: { read: true, global: true },
    deep: [1, 2, 3],
    writing: true,
    spawns: SPAWN_ON_APPROVE,
  }
}

function loadDoc(sid: string): ApprovalDoc {
  const got = lsRead<ApprovalDoc>('approvals.' + sid)
  // spawns 是服务端的事，存盘的旧记录一律用当前这份覆盖
  if (got && got.v === 1) return { ...got, spawns: SPAWN_ON_APPROVE }
  const seed = sid === SAMPLE_SID ? seededDoc(sid) : freshDoc(sid)
  lsWrite('approvals.' + sid, seed)
  return seed
}

function saveDoc(doc: ApprovalDoc): ApprovalDoc {
  lsWrite('approvals.' + doc.sid, doc)
  return doc
}

/* ------------------------------------------------------------ 命令行拼装 */

/** 后端还没有的两个 kind。写进 DELIVERY.md 当作需要后端补的形状。 */
const EXTRA_KINDS: Record<string, { label: string; paid: boolean }> = {
  n0_extras: { label: 'stage0 剔除番外', paid: false },
}

function enums(): EnumsResponse {
  return raw<EnumsResponse>('enums.json')
}

/** 花不花钱由服务端的动作表说了算。这里是「假服务端」，所以查的是同一张表。 */
function paidOf(req: JobRequest): boolean {
  const hit = lookupAction(enums(), req)
  if (hit) return hit.paid
  return EXTRA_KINDS[req.kind]?.paid ?? false
}

function argvOf(req: JobRequest): string {
  const parts = ['python', '-m', 'novel_refactor.cli', req.kind, '--sid', req.sid]
  if (req.extra) parts.push(...req.extra)
  if (req.args) parts.push(...req.args)
  if ((req.kind === 'n5_write' || req.kind === 'n6_write') && req.ordinal) {
    parts.push('--chapters', String(req.ordinal))
  }
  return parts.join(' ')
}

/* -------------------------------------------------------------- 作业引擎 */

interface MockJob extends Job {
  log: string[]
}

let JOBS: MockJob[] = []
let SEQ = 0

/**
 * 服务重启后原本 running 的一律改判 unknown —— 不是 ok。
 * 页面刷新就是这里的「重启」：计时器没了，那一轮到底跑完没有，我们真的不知道。
 */
function bootJobs(): void {
  const saved = lsRead<MockJob[]>('jobs') ?? []
  JOBS = saved.map((j) =>
    j.state === 'running'
      ? {
          ...j,
          state: 'unknown' as const,
          log: [...j.log, '[--:--] 页面重载，这一轮的结局无从判断——不改判成 ok。'],
        }
      : j,
  )
  SEQ = JOBS.length
  lsWrite('jobs', JOBS)
}

bootJobs()

function persistJobs(): void {
  lsWrite('jobs', JOBS.slice(-20))
}

const STEPS: { at: number; line: string }[] = [
  { at: 1500, line: '[00:01] 载入上游产物 …' },
  { at: 3000, line: '[00:03] 正在跑 …' },
  { at: 4500, line: '[00:04] 写盘 …' },
  { at: 6000, line: '[00:06] 完成' },
]

function spawn(req: JobRequest, label?: string): Job {
  SEQ += 1
  const argv = argvOf(req)
  const job: MockJob = {
    id: 'job_' + SEQ + '_' + Date.now().toString(36),
    kind: req.kind,
    label: label ?? req.label,
    argv,
    state: 'running',
    paid: paidOf(req),
    started: Math.floor(Date.now() / 1000),
    finished: null,
    sid: req.sid,
    ordinal: req.ordinal ?? null,
    log: ['$ ' + argv],
  }
  JOBS = [...JOBS, job]
  persistJobs()

  STEPS.forEach((s) => {
    setTimeout(() => {
      const cur = JOBS.find((j) => j.id === job.id)
      if (!cur || cur.state !== 'running') return
      cur.log = [...cur.log, s.line]
      if (s.line.includes('完成')) {
        cur.state = 'ok'
        cur.finished = Math.floor(Date.now() / 1000)
      }
      JOBS = [...JOBS]
      persistJobs()
    }, s.at)
  })

  return stripLog(job)
}

/** 日志不进 /api/jobs 的响应，它走 /jobs/:id/log */
function stripLog(j: MockJob): Job {
  return {
    id: j.id,
    kind: j.kind,
    label: j.label,
    argv: j.argv,
    state: j.state,
    paid: j.paid,
    started: j.started,
    finished: j.finished,
    sid: j.sid,
    ordinal: j.ordinal,
  }
}

function publicJobs(sid: string): JobsResponse {
  const mine = JOBS.filter((j) => j.sid === sid).map(stripLog)
  return { jobs: mine, running: mine.find((j) => j.state === 'running') ?? null }
}

/** 确认之后自动发起的下游。谁发起什么由服务端决定，不由界面决定。 */
const SPAWN_ON_APPROVE: Partial<Record<GateKey, ActionRef[]>> =
  {
    s1: [
      { kind: 'n2_global', label: 'stage2 全局抽取', extra: null },
      { kind: 'n2_blueprint', label: 'stage2 逐章蓝图', extra: null },
      { kind: 'n3_style', label: 'stage3 风格提取', extra: null },
    ],
    s2: [{ kind: 'n4_plan', label: 'stage4 重构计划', extra: ['--all-chapters'] }],
    s5: [{ kind: 'n8_hook', label: 'stage6 开篇文案（产三候选）', extra: null }],
    s6: [{ kind: 'n9_rename', label: 'stage7 人名替换（产新人物表）', extra: null }],
    s7: [{ kind: 'n9_rename', label: 'stage7 · 组装成书 txt', extra: ['--assemble'] }],
  }

/* ------------------------------------------------------------------ Api */

export const mockApi: Api = {
  getNovels: () => later(raw<NovelsResponse>('novels.json')),

  getNovel: (sid) => bookOnly(sid, `/novels/${sid}`, () => raw<NovelOverview>('novel_overview.json')),

  getEnums: () => later(enums()),

  getStage0: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage0/extras`, () => raw<Stage0Extras>('stage0_extras.json')),

  getStage1Report: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage1/report`, () => raw<Stage1Report>('stage1_report.json')),

  getStage1Chapters: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage1/chapters`, () =>
      raw<Stage1Chapters>('stage1_chapters.json'),
    ),

  getStage1HookCopy: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage1/hook_copy`, () =>
      raw<Stage1HookCopy>('stage1_hook_copy.json'),
    ),

  getReading: (sid, o) =>
    chapterOnly(sid, o, `/novels/${sid}/reading/${o}`, () => raw<ReadingResponse>('reading_ch3.json')),

  getGlobal: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage2/global`, () => raw<Stage2Global>('stage2_global.json')),

  getStyle: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage3/style`, () => raw<Stage3Style>('stage3_style.json')),

  getStage4: (sid, o) =>
    chapterOnly(sid, o, `/novels/${sid}/stage4/${o}`, () => raw<Stage4Response>('stage4_ch3.json')),

  getStage5: (sid, o) =>
    chapterOnly(sid, o, `/novels/${sid}/stage5/${o}`, () => raw<Stage5Response>('stage5_ch3.json')),

  getReview: (sid, o) =>
    chapterOnly(sid, o, `/novels/${sid}/review/${o}`, () => {
      const base = raw<ReviewResponse>('review_ch3.json')
      const edit = lsRead<{ text: string; at: number }>(`edits.${sid}.${o}`)
      if (!edit) return base
      // 人改过 → 落成新的一轮，旧的一轮进 rounds。形状和后端的语义一致。
      return {
        ...base,
        chapter: {
          ...base.chapter,
          text: edit.text,
          chars: [...edit.text].length,
          round: base.chapter.round + 1,
          origin: 'human',
          rounds: [...base.chapter.rounds, { round: base.chapter.round, origin: base.chapter.origin }],
        },
      }
    }),

  getInbox: (sid) => bookOnly(sid, `/novels/${sid}/inbox`, () => raw<InboxResponse>('inbox.json')),

  getStage6: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage6`, () => {
      const base = raw<Stage6Response>('stage6_hook.json')
      const edit = lsRead<{ index: number; text: string }>('hook.' + sid)
      if (!edit) return base
      const cand = base.candidates.find((c) => c.index === edit.index)
      return {
        ...base,
        approved: {
          approach: cand?.approach ?? base.approved?.approach ?? '',
          text: edit.text,
          origin: 'human',
          mech: cand?.mech ?? base.approved?.mech ?? {
            punct_fixed: 0,
            quotes_fixed: 0,
            quote_problems: [],
            paras_fixed: 0,
          },
          findings: cand?.findings ?? [],
        },
        candidates: base.candidates.map((c) => ({ ...c, approved: c.index === edit.index })),
      }
    }),

  getStage7: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage7`, () => raw<Stage7Response>('stage7_names.json')),

  getStage7Chapter: (sid, o) =>
    chapterOnly(sid, o, `/novels/${sid}/stage7/${o}`, () => raw<Stage7Response>('stage7_ch3.json')),

  getCheck: (sid, kind: CheckKind) => {
    const path = `/novels/${sid}/check/${kind}`
    if (sid !== SAMPLE_SID) return noBook(path)
    if (kind === 'stage2') return later(raw<CheckReport>('check_stage2.json'))
    if (kind === 'stage5_review') return later(raw<CheckReport>('check_stage5_review.json'))
    // 其余五份没抓在样本里。不是「通过」，也不是「没跑过」—— 是取不到。
    return noChapter(path)
  },

  getSampleCoverage: () => later({ sid: SAMPLE_SID, ordinals: [SAMPLE_ORDINAL] }),

  getJobs: (sid) => later(publicJobs(sid)),

  getJobLog: (id, offset) => {
    const j = JOBS.find((x) => x.id === id)
    if (!j) return fail(new ApiError('notfound', '/jobs/' + id + '/log'))
    const lines = j.log.slice(offset)
    return later<JobLogResponse>({
      lines,
      offset: j.log.length,
      done: j.state !== 'running',
    })
  },

  previewJob: (req) =>
    later<JobPreview>({
      argv: argvOf(req),
      paid: paidOf(req),
      label: req.label,
      kind: req.kind,
    }),

  startJob: (req) => later(spawn(req)),

  getApprovals: (sid) => later(loadDoc(sid)),

  approveGate: (sid, key: GateKey) => {
    const doc = loadDoc(sid)
    const next = saveDoc({ ...doc, gate: { ...doc.gate, [key]: true } })
    // 确认即发起下游 —— 在真产品里这也是服务端做的事
    for (const s of SPAWN_ON_APPROVE[key] ?? []) {
      spawn({ kind: s.kind, label: s.label, extra: s.extra, sid, ordinal: null })
    }
    return later(next)
  },

  markSeen: (sid, key: SeenKey) => {
    const doc = loadDoc(sid)
    if (doc.seen[key]) return later(doc)
    return later(saveDoc({ ...doc, seen: { ...doc.seen, [key]: true } }))
  },

  setDeep: (sid, deep) => {
    const doc = loadDoc(sid)
    return later(saveDoc({ ...doc, deep: [...deep].sort((a, b) => a - b) }))
  },

  startWriting: (sid) => {
    const doc = loadDoc(sid)
    return later(saveDoc({ ...doc, writing: true }))
  },

  resetBook: (sid) => later(saveDoc(freshDoc(sid))),

  saveDraftEdit: (sid, ordinal, text) => {
    lsWrite(`edits.${sid}.${ordinal}`, { text, at: Date.now() })
    return later({ ok: true as const })
  },

  approveHook: (sid, index, text) => {
    lsWrite('hook.' + sid, { index, text })
    return later({ ok: true as const })
  },

  approveNames: (sid) => {
    lsWrite('names.' + sid, { origin: 'human' })
    return later({ ok: true as const })
  },

  uploadCorpus: (file) => {
    // mock 里只有这一本真书。上传等于把它重置回第 0 步。
    saveDoc(freshDoc(SAMPLE_SID))
    spawn(
      {
        kind: 'n0_extras',
        label: 'stage0 番外扫描',
        extra: ['--scan', 'corpus/' + file.name],
        sid: SAMPLE_SID,
        ordinal: null,
      },
      'stage0 番外扫描',
    )
    return later({ sid: SAMPLE_SID })
  },
}
