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
import type {
  AgentQueueView,
  AgentRunLogChunk,
  AgentRunPreview,
  AgentRunRecord,
  AgentRunRequest,
  AgentRunsResponse,
} from '@/types/agent'
import {
  GATE_KEYS,
  type ActionRef,
  type ApprovalDoc,
  type GateKey,
  type SeenKey,
  type StructureApprovalDecision,
  type StructureApprovalRequest,
} from '@/types/approvals'
import type { EnumsResponse } from '@/types/enums'
import type { Job, JobLogResponse, JobPreview, JobRequest, JobsResponse } from '@/types/jobs'
import type { Artifact, NovelOverview, NovelsResponse } from '@/types/novels'
import type { RunProgress } from '@/types/run'
import type {
  CheckKind,
  CheckReport,
  GlobalReviewDecisionSet,
  GlobalReviewResponse,
  InboxResponse,
  ReadingResponse,
  ReviewResponse,
  Stage0EditRequest,
  Stage0Extras,
  Stage0Report,
  Stage0Segments,
  Stage1View,
  Stage2Global,
  Stage4Response,
  Stage5Response,
  Stage6Response,
  Stage7Response,
} from '@/types/stages'
import { lookupAction } from '@/constants/actions'
import { ApiError } from './errors'
import { projectGlobalSpoilers } from './spoilers'

/* ------------------------------------------------------------ 样本的边界 */

/** 样本抓自这一本 8 章的书 */
export const SAMPLE_SID = '7615069877019300633'
/** 逐章响应只抓了第 3 章 */
export const SAMPLE_ORDINAL = 3

/* ------------------------------------------ 冻结原样（盘上 20 份，运行时排除 2 份） */

const RAW = import.meta.glob(
  [
    '../mocks/*.json',
    // 两份旧 Stage5 抓包继续留作冻结证据，但现役 mock 已改读 backend_v1；
    // 不把其中的重复正文与退役字段继续打进 eager bundle。
    '!../mocks/stage5_ch3.json',
    '!../mocks/review_ch3.json',
  ],
  { eager: true, import: 'default' },
) as Record<string, unknown>

function raw<T>(file: string): T {
  const hit = RAW['../mocks/' + file]
  if (hit === undefined) throw new ApiError('malformed', '/mock/' + file)
  // 一次性穿过手写类型。Vite 会把 JSON 推成巨型字面量类型，
  // 让它进类型系统会把 tsc 拖垮，也会让 types/ 失去「契约」的意义。
  return hit as T
}

type LegacyArtifact = Omit<Artifact, 'display_freshness' | 'condition'>
type LegacyNovel = Omit<
  NovelOverview,
  'stages' | 'stage_done' | 'stage_reviewable' | 'chapters_done' | 'summary'
> & {
  stages: Record<string, LegacyArtifact[]>
  stage_done?: Record<string, boolean | null>
  chapters_done?: Record<string, number[] | Record<string, number[]>>
  /** 只用于解码冻结 mock；绝不出现在当前 NovelOverview。 */
  stage5_modes?: string[]
}

const PIPELINE_STAGES = [
  'stage0', 'stage1', 'stage2', 'stage4', 'stage5', 'stage6', 'stage7',
]

/** Stage5 fixture 已按现役 HTTP 契约收成明确的 legacy 终态。 */
function currentMockStage5(): Stage5Response {
  return fx<Stage5Response>('stage5_ch3.json')
}

function currentMockReview(): ReviewResponse {
  return fx<ReviewResponse>('review_ch3.json')
}

/** 冻结 mock 的旧业务推导只准留在这个假后端；屏幕与真实数据缝不兼容旧形状。 */
function currentMockNovel(legacy: LegacyNovel): NovelOverview {
  const stages: Record<string, Artifact[]> = {}
  const stageDone: Record<string, boolean | null> = {}
  const stageReviewable: Record<string, boolean> = {}
  const legacyModes = (legacy.stage5_modes ?? []).filter((mode) => !mode.startsWith('_'))

  for (const stage of PIPELINE_STAGES) {
    const artifacts = (legacy.stages[stage] ?? []).map<Artifact>((artifact) => ({
      ...artifact,
      display_freshness: artifact.exists ? artifact.freshness : 'missing',
      condition: artifact.invalid
        ? 'invalid'
        : !artifact.exists
          ? 'missing'
          : artifact.readable === false
            ? 'unreadable'
            : 'ready',
    }))
    stages[stage] = artifacts
    const declared = legacy.stage_done?.[stage]
    const done =
      declared !== undefined
        ? declared
        : stage === 'stage5'
          ? false
          : artifacts.length
            ? artifacts.every(
                (artifact) =>
                  artifact.condition === 'ready' && artifact.display_freshness !== 'stale',
              )
            : false
    stageDone[stage] = done
    stageReviewable[stage] =
      done === true ||
      (stage === 'stage5'
        ? legacyModes.length > 0
        : artifacts.some((artifact) => artifact.exists && artifact.readable !== false))
  }

  const all = Object.values(stages).flat()
  const broken = all.filter(
    (artifact) => artifact.condition === 'invalid' || artifact.condition === 'unreadable',
  )
  const stale = all.filter((artifact) => artifact.display_freshness === 'stale')
  const unknown = all.filter(
    (artifact) => artifact.exists && artifact.display_freshness === 'unknown',
  )
  const missing = all.filter((artifact) => artifact.display_freshness === 'missing')
  const pending = PIPELINE_STAGES.find((stage) => stageDone[stage] === false)
  const unknownStages = PIPELINE_STAGES.filter((stage) => stageDone[stage] === null)
  const existing = all.filter((artifact) => artifact.exists)

  let verdict: NovelOverview['summary']['verdict']
  let text: string
  let detail: string
  if (broken.length) {
    ;[verdict, text, detail] = [
      'broken',
      broken.length + ' 份产物不可用',
      '盘上存在，但当前版本读不回来或已被隔离',
    ]
  } else if (legacyModes.length) {
    ;[verdict, text, detail] = [
      'legacy_incompatible',
      '发现旧 Stage5 契约产物',
      'deep/shallow 只读保留，不会迁移或自动付费重跑',
    ]
  } else if (stale.length) {
    ;[verdict, text, detail] = ['stale', stale.length + ' 份过期', '上游变了，下游要重跑']
  } else if (pending) {
    ;[verdict, text, detail] = ['pending', '待继续', '下一步：' + pending]
  } else if (unknown.length) {
    ;[verdict, text, detail] = [
      'unknown',
      unknown.length + ' 份无从判断',
      missing.length ? missing.length + ' 份尚未落盘' : '其余产物新鲜',
    ]
  } else if (unknownStages.length) {
    ;[verdict, text, detail] = [
      'unknown',
      unknownStages.length + ' 层完成度判不了',
      '不是没跑，需要打开书逐层确认',
    ]
  } else {
    ;[verdict, text, detail] = [
      'complete',
      '全链已有产物',
      existing.length + ' 份产物可读',
    ]
  }

  const chaptersDone = Object.fromEntries(
    PIPELINE_STAGES.map((stage) => {
      const value = legacy.chapters_done?.[stage]
      return [stage, Array.isArray(value) ? value : []]
    }),
  ) as Record<string, number[]>
  const {
    stage5_modes: _retiredStage5Modes,
    chapters_done: _legacyChaptersDone,
    ...current
  } = legacy
  void _retiredStage5Modes
  void _legacyChaptersDone

  return {
    ...current,
    stages,
    stage_done: stageDone,
    stage_reviewable: stageReviewable,
    chapters_done: chaptersDone,
    summary: {
      verdict,
      text,
      detail,
      artifact_count: existing.length,
      latest_mtime: Math.max(0, ...existing.map((artifact) => artifact.mtime ?? 0)) || null,
    },
  }
}

function mockNovels(): NovelsResponse {
  const legacy = raw<{ base: string; novels: LegacyNovel[] }>('novels.json')
  return { base: legacy.base, novels: legacy.novels.map(currentMockNovel) }
}

/* ------------------------------------------------- backend_v1（现役数据源） */

/**
 * 新后端真实响应的 fixture（FE-06 的「有版本含义的目录」）。
 * `src/mocks/` 那 20 份是**冻结的证据基线**，从此不再被新形状的端点消费；
 * mock 现役读的是这一批 —— 与真后端同一形状，屏幕在两种模式下走同一条代码路径。
 */
const RAW_V1 = import.meta.glob('../fixtures/backend_v1/*.json', {
  eager: true,
  import: 'default',
}) as Record<string, unknown>

function fx<T>(file: string): T {
  const hit = RAW_V1['../fixtures/backend_v1/' + file]
  if (hit === undefined) throw new ApiError('malformed', '/fixtures/' + file)
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

const MOCK_STRUCTURE_SUBJECT = 'f'.repeat(64)

function enumAt(values: string[], index: number, label: string): string {
  const value = values[index]
  if (!value) throw new ApiError('malformed', '/fixtures/enums.json', {
    message: label + ' 取值面缺少第 ' + (index + 1) + ' 项',
  })
  return value
}

function seededStructureDecision(): StructureApprovalDecision {
  return {
    subject_sha256: MOCK_STRUCTURE_SUBJECT,
    actor: 'mock-reviewer',
    reason: 'mock 样本已核对全书结构',
    chapters: [{
      output_ordinal: SAMPLE_ORDINAL,
      decision: enumAt(enums().structure_approval_decisions, 0, '结构审批决定'),
      reason: 'mock 第 3 章结构已核对',
    }],
    decided_at: '2026-08-25T00:00:00+00:00',
  }
}

function freshDoc(sid: string): ApprovalDoc {
  return {
    v: 4,
    sid,
    gate: { s0: false, s1: false, s2: false, s4: false, s5: false, s6: false, s7: false },
    seen: { read: false, global: false },
    writing: false,
    structure: {
      state: enumAt(enums().structure_approval_states, 0, '结构审批状态'),
      current_subject_sha256: null,
      current_decision: null,
      history: [],
    },
    spawns: {},
  }
}

/**
 * 样本那本书的初始状态：已经跑到 stage5，文案与成书还没确认。
 * 这样一打开就能看到一本走到一半的书 —— 全 false 的空壳看不出这个工具在干什么。
 *
 */
function seededDoc(sid: string): ApprovalDoc {
  const decision = seededStructureDecision()
  return {
    v: 4,
    sid,
    gate: { s0: true, s1: true, s2: true, s4: true, s5: true, s6: false, s7: false },
    seen: { read: true, global: true },
    writing: true,
    structure: {
      state: enumAt(enums().structure_approval_states, 2, '结构审批状态'),
      current_subject_sha256: MOCK_STRUCTURE_SUBJECT,
      current_decision: decision,
      history: [decision],
    },
    spawns: {},
  }
}

function loadDoc(sid: string): ApprovalDoc {
  const got = lsRead<unknown>('approvals.' + sid)
  // spawns 是服务端的事；只接受当前公开 v4，旧本地记录按当前种子重建。
  if (got && typeof got === 'object' && (got as { v?: unknown }).v === 4) {
    return withMockSpawns(got as ApprovalDoc)
  }
  const seed = sid === SAMPLE_SID ? seededDoc(sid) : freshDoc(sid)
  const projected = withMockSpawns(seed)
  lsWrite('approvals.' + sid, projected)
  return projected
}

function saveDoc(doc: ApprovalDoc): ApprovalDoc {
  const projected = withMockSpawns(doc)
  lsWrite('approvals.' + doc.sid, projected)
  return projected
}

/* ------------------------------------------------------ Stage2 全局复核 */

const MOCK_SHA = {
  source: 'a'.repeat(64),
  characters: 'd'.repeat(64),
  relations: 'e'.repeat(64),
  rules: 'b'.repeat(64),
  secrets: 'c'.repeat(64),
}

function freshGlobalReview(): GlobalReviewResponse {
  const decided = {
    state: 'PASS' as const,
    evidence: [],
    actor: 'mock-reviewer',
    decided_at: '2026-08-16T00:00:00+00:00',
    reason: 'mock 样本已经逐项核对',
  }
  return {
    source_sha256: MOCK_SHA.source,
    current_subject_sha256: MOCK_SHA.source,
    current: true,
    approved: true,
    items: [
      {
        item_id: 'character_set_complete:entities.characters:' + MOCK_SHA.characters.slice(0, 12),
        kind: 'character_set_complete',
        subject_path: 'entities.characters',
        subject_sha256: MOCK_SHA.characters,
        ...decided,
      },
      {
        item_id: 'relation_set_complete:entities.relations:' + MOCK_SHA.relations.slice(0, 12),
        kind: 'relation_set_complete',
        subject_path: 'entities.relations',
        subject_sha256: MOCK_SHA.relations,
        ...decided,
      },
      {
        item_id: 'world_rule_set_complete:canon.world_rules:' + MOCK_SHA.rules.slice(0, 12),
        kind: 'world_rule_set_complete',
        subject_path: 'canon.world_rules',
        subject_sha256: MOCK_SHA.rules,
        ...decided,
      },
      {
        item_id: 'secret_set_complete:canon.secrets:' + MOCK_SHA.secrets.slice(0, 12),
        kind: 'secret_set_complete',
        subject_path: 'canon.secrets',
        subject_sha256: MOCK_SHA.secrets,
        ...decided,
      },
    ],
  }
}

function loadGlobalReview(sid: string): GlobalReviewResponse {
  return lsRead<GlobalReviewResponse>('global-review.' + sid) ?? freshGlobalReview()
}

function saveGlobalReview(sid: string, review: GlobalReviewResponse): GlobalReviewResponse {
  lsWrite('global-review.' + sid, review)
  return review
}

/* ------------------------------------------------------------ 命令行拼装 */

/** 取值面直接透传新后端抓包（82 条真实动作，真实节点 id）——mock 不再打补丁。 */
function enums(): EnumsResponse {
  const frozen = fx<EnumsResponse>('enums.json')
  if (frozen.spoiler_disclosure) return frozen
  return {
    ...frozen,
    spoiler_disclosure: {
      mode: 'display_only',
      security_boundary: false,
      server_sends_full_data: true,
      hidden_paths: ['characters[].fate', 'secrets', 'arc.climax', 'arc.ending'],
      known_leakage_surfaces: [
        'HTTP response body',
        'browser network/devtools/cache',
        'role, relations and world rule descriptions may imply the same facts',
      ],
      consumers: ['api.projectGlobalSpoilers', 'GlobalScreen'],
      notice:
        '这是显示辅助，不是安全边界；服务端仍下发完整数据。关闭时只隐藏 fate、secrets、'
        + 'climax、ending 四处核心字段，role、关系时点、规则正文与网络响应仍可能剧透。',
    },
  }
}

/** 花不花钱由服务端的动作表说了算。这里是「假服务端」，所以查的是同一张表。 */
function paidOf(req: JobRequest): boolean {
  const hit = lookupAction(enums(), req)
  return hit ? hit.paid : false
}

function argvOf(req: JobRequest): string {
  const parts = ['python', '-m', 'novel_refactor.cli', req.kind, '--sid', req.sid]
  if (req.extra) parts.push(...req.extra)
  if (req.args) parts.push(...req.args)
  if (req.kind === 'stage5.write' && req.ordinal) {
    parts.push('--chapters', String(req.ordinal))
  }
  return parts.join(' ')
}

function previewRequest(req: JobRequest): JobRequest {
  const action = lookupAction(enums(), req)
  const inputs = req.required_inputs ?? action?.required_inputs ?? []
  const present = [...(req.extra ?? []), ...(req.args ?? [])]
  const placeholders = inputs
    .filter((input) => !present.includes(input.flag))
    .flatMap((input) => [input.flag, '待填·' + input.flag.replace(/^--/, '')])
  return placeholders.length
    ? { ...req, args: [...(req.args ?? []), ...placeholders] }
    : req
}

function mockJobPreview(req: JobRequest): JobPreview {
  const planned = previewRequest(req)
  return {
    argv: argvOf(planned),
    paid: paidOf(req),
    label: req.label,
    kind: req.kind,
    steps: previewStepsOf(planned),
  }
}

function previewStepsOf(req: JobRequest): JobPreview['steps'] {
  const first = {
    label: req.label,
    paid: paidOf(req),
    writes: 'mock 产物',
    argv: argvOf(req),
  }
  const checkArgv = (kind: string) =>
    ['python', '-m', 'novel_refactor.cli', kind, '--sid', req.sid, '--check-only'].join(' ')
  if (req.kind === 'stage5.write') {
    return [
      first,
      {
        label: 'stage5 成稿 · 追跑全量校验',
        paid: false,
        writes: '',
        argv: checkArgv('stage5.write'),
      },
    ]
  }
  return [first]
}

function plainMockReport(report: CheckReport): CheckReport {
  const plain = (value: string | null) => value?.replace(/\*\*|~~|`/g, '') ?? null
  return {
    ...report,
    rules: report.rules.map((line) => ({
      ...line,
      why: plain(line.why),
      scanned: plain(line.scanned),
      baseline: plain(line.baseline),
      note: plain(line.note),
      findings: line.findings.map((finding) => ({
        ...finding,
        detail: plain(finding.detail) ?? '',
      })),
    })),
  }
}

function mockCheck(kind: CheckKind): CheckReport {
  const report = plainMockReport(fx<CheckReport>(`check_${kind}.json`))
  if (kind !== 'stage7') return report
  // stage7 视图在 fake server 中把成书端点设为可达；同一 fake server 的
  // 校验响应也要同步这件事实，不能一边说“在盘上”一边保留历史抓包的 missing 红行。
  return {
    ...report,
    counts: {
      ...report.counts,
      ok: report.counts.ok + 1,
      hit: report.counts.hit - 1,
    },
    rules: report.rules.map((line) =>
      line.rule === 'rn_txt_stale'
        ? { ...line, status: 'ok' as const, findings: [], why: null }
        : line,
    ),
  }
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

/** Stage 0–1 的真实接口是同步子进程；mock 也返回已经结束的作业，页面才能走同一条链。 */
function finish(req: JobRequest, label?: string): Job {
  const started = spawn(req, label)
  const stored = JOBS.find((job) => job.id === started.id)
  if (!stored) return started
  stored.state = 'ok'
  stored.finished = Math.floor(Date.now() / 1000)
  stored.log = [...stored.log, '[mock] 完成']
  JOBS = [...JOBS]
  persistJobs()
  return stripLog(stored)
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
function withMockSpawns(doc: ApprovalDoc): ApprovalDoc {
  const s1: ActionRef[] = !doc.seen.global
    ? [{
        kind: 'stage2.global',
        label: '生成 stage2 全局底座；完成后逐项人工复核',
        extra: null,
      }]
    : !doc.seen.read
      ? [{
          kind: 'stage2.blueprint',
          label: '生成 stage2 逐章骨架；可与全局人工复核并行',
          extra: null,
        }]
      : []
  const spawns: Partial<Record<GateKey, ActionRef[]>> = {
    s1,
    s2: [{ kind: 'stage4.plan', label: 'stage4 重构计划', extra: null }],
    s5: [{ kind: 'stage6.hook', label: 'stage6 开篇文案（产三候选）', extra: null }],
    s6: [{ kind: 'stage7.rename', label: 'stage7 人名替换（产新人物表）', extra: null }],
    s7: [{ kind: 'stage7.assemble', label: 'stage7 · 组装成书 txt', extra: null }],
  }
  return { ...doc, spawns }
}

function codePoints(text: string): number {
  return [...text].length
}

function mockStage0Segments(sid: string): Stage0Segments {
  const data = raw<Stage0Extras>('stage0_extras.json')
  const saved = lsRead<Stage0EditRequest>('stage0.selection.' + sid)
  const selected = new Set(saved?.removed_segment_ids)
  const pieces = [
    ...data.blocks.map((block, index) => ({
      id: 'keep-' + index,
      text: block.label + '\n' + block.preview + '\n\n',
      machine: false,
      marker: block.label,
      removedKind: 'extra',
    })),
    ...data.candidates.map((candidate, index) => ({
      id: 'remove-' + index,
      text: candidate.marker + '\n' + candidate.head + '\n' + candidate.tail + '\n\n',
      machine: true,
      marker: candidate.marker,
      removedKind: candidate.kind,
    })),
  ]
  let offset = 0
  const segments = pieces.map((piece) => {
    const initial = saved ? selected.has(piece.id) : piece.machine
    const chars = codePoints(piece.text)
    const start = offset
    offset += chars
    return {
      id: piece.id,
      kind: initial ? 'remove' as const : 'keep' as const,
      start,
      end: offset,
      chars,
      flagged: initial,
      text: piece.text,
      origin: initial ? (piece.machine ? 'machine' as const : 'human' as const) : null,
      removed_kind: initial ? piece.removedKind : null,
      marker_line: initial ? piece.marker : null,
    }
  })
  return { available: true, reason: null, segments }
}

function mockStage0Report(sid: string): Stage0Report {
  const data = raw<Stage0Extras>('stage0_extras.json')
  const approved = loadDoc(sid).gate.s0
  return {
    original_filename: data.original_filename,
    total_code_points: data.total_code_points,
    warnings: [],
    executable: approved,
    revision_id: (lsRead<Stage0EditRequest>('stage0.selection.' + sid) ? 'b' : 'a').repeat(64),
    revision_origin: lsRead<Stage0EditRequest>('stage0.selection.' + sid)
      ? ('human_edit' as const)
      : ('machine' as const),
  }
}

function mockStage1View(): Stage1View {
  return fx<Stage1View>('stage1.json')
}

/* ---------------------------------------------------------- 跑书进度样例 */

/** mock 的静态镜像；真实响应由后端直接从 NODES 派生。 */
const PIPELINE_STEPS: { node: string; label: string; paid: boolean }[] = [
  { node: 'stage0.remove', label: 'stage0 非正文剔除', paid: false },
  { node: 'stage0.approve', label: 'stage0 人工批准', paid: false },
  { node: 'stage0.edit', label: 'stage0 人工编辑', paid: false },
  { node: 'stage1.partition', label: 'stage1 分章', paid: false },
  { node: 'stage2.global', label: 'stage2 全书级抽取', paid: true },
  { node: 'stage2.blueprint', label: 'stage2 逐章抽取', paid: true },
  { node: 'stage4.plan', label: 'stage4 重构计划', paid: true },
  { node: 'stage4.context', label: 'stage4 写作上下文组装', paid: false },
  { node: 'stage5.write', label: 'stage5 逐章写作', paid: true },
  { node: 'stage5.review', label: 'stage5 全书验收', paid: false },
  { node: 'stage6.hook', label: 'stage6 开篇文案', paid: true },
  { node: 'stage6.semantic', label: 'stage6 文案语义审判', paid: true },
  { node: 'stage6.approve', label: 'stage6 文案定稿', paid: false },
  { node: 'stage7.rename', label: 'stage7 人名表', paid: true },
  { node: 'stage7.approve', label: 'stage7 人名表定稿', paid: false },
  { node: 'stage7.assemble', label: 'stage7 替换与成书', paid: false },
]

/** 静态样例：全节点 pending + 空事件流 + 全 null 的 stage_done。 */
function mockRunProgress(sid: string): RunProgress {
  return {
    sid,
    steps: PIPELINE_STEPS.map((s, i) => ({
      seq: i + 1,
      node: s.node,
      label: s.label,
      paid: s.paid,
      beacon: 'pending',
      last_exit: null,
      last_ts: null,
      elapsed: null,
    })),
    stage_done: {
      stage0: null,
      stage1: null,
      stage2: null,
      stage4: null,
      stage5: null,
      stage6: null,
      stage7: null,
    },
    events_tail: [],
  }
}

/* ---------------------------------------------------------- agent 代跑样例 */

/** 真 argv / prompt 由后端同一构造函数产出；这里是形状样例（拼装只许住在数据层）。 */
function mockAgentRunPreview(req: AgentRunRequest): AgentRunPreview {
  return {
    argv: ['agent-cli', '--sid', req.sid, '--base', '/mock/base'],
    prompt:
      '按 novel-pipeline-operator 操作规程把这本书跑到成书（sid ' + req.sid + '）。\n' +
      (req.authorized
        ? '付费授权已授予：付费命令不再逐条停。'
        : '付费授权未授予：到第一条付费命令前停下。') +
      '\n' +
      (req.egress_authorized
        ? '正文外发授权已授予：允许发送正文及派生上下文。'
        : '正文外发授权未授予：不得发送正文及派生上下文。'),
    authorized: req.authorized,
    egress_authorized: req.egress_authorized,
  }
}

function mockAgentRunRecord(req: AgentRunRequest): AgentRunRecord {
  const preview = mockAgentRunPreview(req)
  return {
    run_id: 'agentrun_mock',
    start: {
      sid: req.sid,
      argv: preview.argv,
      prompt: preview.prompt,
      authorized: preview.authorized,
      egress_authorized: preview.egress_authorized,
      code_version: 'mock-code-version',
    },
    started: Math.floor(Date.now() / 1000),
    ended: null,
    exit_code: null,
    state: 'running',
    note: null,
    outcome: null,
  }
}

function mockInbox(): InboxResponse {
  const legacy = fx<InboxResponse>('inbox.json')
  const items = legacy.items.map((item) => ({
    ...item,
    attention: item.attention ?? ('pending' as const),
    delivery_effect:
      item.delivery_effect ??
      (item.kind === 'human_only' || (item.kind === 'verification' && item.stage === 'stage6')
        ? ('disclosure' as const)
        : ('blocking' as const)),
    decision: item.decision ?? null,
  }))
  return {
    ...legacy,
    items,
    attention_counts: {
      pending: items.filter((item) => item.attention === 'pending').length,
      reviewed_retained: items.filter((item) => item.attention === 'reviewed_retained').length,
    },
    delivery_counts: {
      blocking: items.filter((item) => item.delivery_effect === 'blocking').length,
      disclosure: items.filter((item) => item.delivery_effect === 'disclosure').length,
    },
  }
}

/* ------------------------------------------------------------------ Api */

export const mockApi: Api = {
  getNovels: () => later(mockNovels()),

  getNovel: (sid) =>
    bookOnly(sid, `/novels/${sid}`, () =>
      currentMockNovel(raw<LegacyNovel>('novel_overview.json')),
    ),

  // 进度屏在 stage0.remove 前就要能开（书目录可能还没建）——任何 sid 都给静态样例，不走 bookOnly
  getRunProgress: (sid) => later(mockRunProgress(sid)),

  getEnums: () => later(enums()),

  getStage0Report: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage0/report`, () => mockStage0Report(sid)),

  getStage0Segments: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage0/segments`, () => mockStage0Segments(sid)),

  editStage0: (sid, request) => {
    lsWrite('stage0.selection.' + sid, request)
    return later(finish({
      kind: 'stage0.edit',
      label: 'stage0 人工编辑',
      extra: null,
      sid,
      ordinal: null,
    }))
  },

  approveStage0: (sid, request) => {
    lsWrite('stage0.approval.' + sid, request)
    const doc = loadDoc(sid)
    saveDoc({ ...doc, gate: { ...doc.gate, s0: true } })
    return later(finish({
      kind: 'stage0.approve',
      label: 'stage0 人工批准',
      extra: null,
      sid,
      ordinal: null,
    }))
  },

  getStage0Candidate: () => later(null),

  adoptStage0Candidate: (sid, request) => {
    lsWrite('stage0.candidate.adopt.' + sid, request)
    return later(finish({
      kind: 'stage0.candidate.adopt',
      label: 'stage0 采纳机器新版',
      extra: null,
      sid,
      ordinal: null,
    }))
  },

  dismissStage0Candidate: (sid, request) => {
    lsWrite('stage0.candidate.dismiss.' + sid, request)
    return later(finish({
      kind: 'stage0.candidate.dismiss',
      label: 'stage0 保留当前版',
      extra: null,
      sid,
      ordinal: null,
    }))
  },

  partitionStage1: (sid) =>
    later(finish({
      kind: 'stage1.partition',
      label: 'stage1 分章',
      extra: null,
      sid,
      ordinal: null,
    })),

  getStage1: (sid) => bookOnly(sid, `/novels/${sid}/stage1`, mockStage1View),

  getReading: (sid, o) =>
    chapterOnly(sid, o, `/novels/${sid}/reading/${o}`, () => fx<ReadingResponse>('reading_ch3.json')),

  getGlobal: (sid, revealSpoilers = false) =>
    bookOnly(sid, `/novels/${sid}/stage2/global`, () =>
      projectGlobalSpoilers(fx<Stage2Global>('global.json'), revealSpoilers),
    ),

  getGlobalReview: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage2/global-review`, () => loadGlobalReview(sid)),

  decideGlobalReview: (sid, request: GlobalReviewDecisionSet) => {
    const path = `/novels/${sid}/stage2/global-review`
    if (sid !== SAMPLE_SID) return noBook(path)
    if (!request.actor.trim() || request.decisions.some((row) => !row.reason.trim())) {
      return fail(new ApiError('server', path, {
        status: 422,
        message: 'actor/reason 不得只有空白',
        backendKind: 'RequestValidationError',
      }))
    }
    const review = loadGlobalReview(sid)
    if (!review.current) {
      return fail(new ApiError('server', path, {
        status: 409,
        message: 'stage2 global review is stale or omits required subjects',
        backendKind: 'HumanEditProtected',
      }))
    }
    const decisionIds = request.decisions.map((row) => row.item_id)
    if (new Set(decisionIds).size !== decisionIds.length) {
      return fail(new ApiError('server', path, {
        status: 422,
        message: 'each global-review item may be decided at most once per request',
        backendKind: 'RequestValidationError',
      }))
    }
    const byId = new Map(review.items.map((item) => [item.item_id, item]))
    for (const row of request.decisions) {
      const item = byId.get(row.item_id)
      if (!item || item.subject_sha256 !== row.expected_subject_sha256) {
        return fail(new ApiError('server', path, {
          status: 409,
          message: 'global-review subject changed; stale decision rejected',
          backendKind: 'HumanEditProtected',
        }))
      }
    }
    const timestamp = new Date().toISOString()
    const decisions = new Map(request.decisions.map((row) => [row.item_id, row]))
    const items = review.items.map((item) => {
      const row = decisions.get(item.item_id)
      return row
        ? {
            ...item,
            state: row.state,
            actor: request.actor.trim(),
            decided_at: timestamp,
            reason: row.reason.trim(),
            evidence: row.evidence,
          }
        : item
    })
    const approved = review.current && items.length > 0 && items.every((item) => item.state === 'PASS')
    saveGlobalReview(sid, { ...review, items, approved })
    const job = finish({
      kind: 'stage2.global',
      label: 'stage2 全局复核决策',
      extra: ['--review-decisions'],
      sid,
      ordinal: null,
    })
    if (!approved) {
      const stored = JOBS.find((row) => row.id === job.id)
      if (stored) {
        stored.state = 'landed_red'
        stored.log = [...stored.log, '[mock] 仍有非 PASS 项，s2 gate 保持关闭']
        persistJobs()
        return later(stripLog(stored))
      }
    }
    return later(job)
  },

  getStage4: (sid, o) =>
    chapterOnly(sid, o, `/novels/${sid}/stage4/${o}`, () => fx<Stage4Response>('stage4_ch3.json')),

  getStage5: (sid, o) =>
    chapterOnly(sid, o, `/novels/${sid}/stage5/${o}`, currentMockStage5),

  getReview: (sid, o) =>
    chapterOnly(sid, o, `/novels/${sid}/review/${o}`, currentMockReview),

  getInbox: (sid) => bookOnly(sid, `/novels/${sid}/inbox`, mockInbox),

  getStage6: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage6`, () => {
      const base = fx<Stage6Response>('stage6.json')
      const edit = lsRead<{
        index: number
        actor: string
        reason: string
        subject_sha256: string
      }>('hook.' + sid)
      if (!edit) return base
      const cand = base.candidates.find((c) => c.index === edit.index)
      if (!cand) return base
      // 定稿 = 候选那一份的拷贝（--pick 的语义；改字不在浏览器，走 CLI）。
      return {
        ...base,
        approved: {
          // 定稿记的是「从哪条候选来」（位置身份），策略名由服务端现算后一并下发。
          slot: 'c' + edit.index,
          approach: cand.approach,
          chars: cand.chars,
          text: cand.text,
          origin: 'human',
          mech: cand.mech,
          findings: cand.findings,
          resolution: {
            actor: edit.actor,
            reason: edit.reason,
            subject_sha256: edit.subject_sha256,
            basis: 'human_verification' as const,
            semantic_review_seq: cand.semantic_state === 'unreviewed' ? null : 1,
          },
        },
        candidates: base.candidates.map((c) => ({ ...c, approved: c.index === edit.index })),
      }
    }),

  getStage7: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage7`, () => {
      const base = fx<Stage7Response>('stage7.json')
      const approved = lsRead<NonNullable<Stage7Response['approved']>>('names.' + sid)
      return {
        ...base,
        approved: approved ?? base.approved,
        // 冻结抓包记录的是“账在、txt 不在”的历史故障；假服务端把专用文本端点
        // 也做成可达，开发时才能走通预览 / 下载这条新增 UX。
        book_txt_on_disk: true,
        // 抓包日期（2026-08-17）早于成书改名，那份 fixture 里没有这个字段。
        // ⚠ 兼容推导只许留在**适配边界**（本文件），不许进屏幕组件
        // （`docs/LESSONS.md` 第 13 条）；真实服务端由
        // `novel/views/stage7.py` 从 `paths.book_txt_rel` 下发。
        book_filename: base.book_filename ?? base.title + '_rewriting.txt',
      }
    }),

  getStage7Chapter: (sid, o) =>
    chapterOnly(sid, o, `/novels/${sid}/stage7/${o}`, () => {
      const base = fx<Stage7Response>('stage7_ch3.json')
      const approved = lsRead<NonNullable<Stage7Response['approved']>>('names.' + sid)
      return { ...base, approved: approved ?? base.approved }
    }),

  getStage7Book: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage7/book.txt`, () =>
      '《mock 成书》\n\n这是最终成书的预览样例。\n'),

  downloadStage7Book: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage7/book.txt?download=true`, () =>
      new Blob(['《mock 成书》\n\n这是最终成书的下载样例。\n'], {
        type: 'text/plain;charset=utf-8',
      }),
    ),

  getCheck: (sid, kind: CheckKind) => {
    const path = `/novels/${sid}/check/${kind}`
    if (sid !== SAMPLE_SID) return noBook(path)
    // stage0/1 在样本书上没跑过（真后端答 404）—— null = 「还没跑」，不是「通过」。
    if (kind === 'stage0' || kind === 'stage1') return later(null)
    return later(mockCheck(kind))
  },

  getSampleCoverage: () => later({ sid: SAMPLE_SID, ordinals: [SAMPLE_ORDINAL] }),

  getJobs: (sid) => later(publicJobs(sid)),

  getJobLog: (id, offset) => {
    const j = JOBS.find((x) => x.id === id)
    if (!j) return fail(new ApiError('notfound', '/jobs/' + id + '/log'))
    const lines = j.log.slice(offset).map((text) => ({
      text,
      raw: null,
      details: null,
      kind: 'plain' as const,
    }))
    return later<JobLogResponse>({
      lines,
      offset: j.log.length,
      done: j.state !== 'running',
    })
  },

  streamJobLog: (id, offset, sink) => {
    let closed = false
    let cursor = offset
    let timer: ReturnType<typeof setTimeout> | undefined
    const encoder = new TextEncoder()
    const decoder = new TextDecoder()
    const pump = () => {
      if (closed) return
      const j = JOBS.find((x) => x.id === id)
      if (!j) {
        sink.onError(new ApiError('notfound', '/jobs/' + id + '/stream'))
        return
      }
      const bytes = encoder.encode(j.log.length ? j.log.join('\n') + '\n' : '')
      const text = decoder.decode(bytes.slice(cursor))
      cursor = bytes.length
      sink.onEvent({
        text,
        next_byte: cursor,
        done: j.state !== 'running',
        state: j.state === 'running' ? null : j.state,
      })
      if (!closed && j.state === 'running') timer = setTimeout(pump, 80)
    }
    timer = setTimeout(pump, 0)
    return () => {
      closed = true
      if (timer !== undefined) clearTimeout(timer)
    }
  },

  previewJob: (req) => later(mockJobPreview(req)),

  startJob: (req) => later(spawn(req)),

  /* agent 代跑：静态样例 —— 列表恒空（mock 不养 agent 进程），start 回一条 running 记录 */
  previewAgentRun: (req) => later(mockAgentRunPreview(req)),
  startAgentRun: (req) => later(mockAgentRunRecord(req)),
  getAgentRuns: () => later<AgentRunsResponse>({ runs: [] }),
  getAgentRunLog: () => later<AgentRunLogChunk>({ text: '', next_byte: 0 }),
  stopAgentRun: (runId) =>
    later<AgentRunRecord>({
      ...mockAgentRunRecord({ sid: SAMPLE_SID, authorized: false, egress_authorized: true }),
      run_id: runId,
      ended: Math.floor(Date.now() / 1000),
      state: 'unknown',
      note: 'mock：熄火后结局不明，不改判 ok',
    }),

  /* 批量：闸写死 3（真实模式由服务端下发）。前 3 本算在跑，其余按提交顺序排队。
     ⚠ mock 不养进程，所以这里只是**形状**正确的一份样例——它让批量入口的空/满/
     排队三态在离线开发时都渲染得出来，不假装真的起了 agent。 */
  startAgentBatch: (req) =>
    later<AgentQueueView>({
      running: req.sids.slice(0, 3).map((sid) =>
        mockAgentRunRecord({
          sid,
          authorized: req.authorized,
          egress_authorized: req.egress_authorized,
        }),
      ),
      pending: req.sids.slice(3).map((sid, i) => ({ sid, position: i + 1 })),
      rejected: [],
      max_concurrent: 3,
    }),
  getAgentQueue: () =>
    later<AgentQueueView>({ running: [], pending: [], rejected: [], max_concurrent: 3 }),

  getApprovals: (sid) => later(loadDoc(sid)),

  approveGate: (sid, key: GateKey) => {
    if (key === 's4') {
      return fail(new ApiError('server', '/novels/' + sid + '/approvals/gate/s4', {
        message: 'Stage4 只接受带完整 subject 与逐章理由的专用结构裁决',
      }))
    }
    const doc = loadDoc(sid)
    return later(saveDoc({ ...doc, gate: { ...doc.gate, [key]: true } }))
  },

  approveStructure: (sid, request: StructureApprovalRequest) => {
    const doc = loadDoc(sid)
    const subject = doc.structure.current_subject_sha256
    const expectedChapters = doc.structure.current_decision?.chapters
      .map((row) => row.output_ordinal) ?? [SAMPLE_ORDINAL]
    const submittedChapters = request.chapters.map((row) => row.output_ordinal)
    if (subject === null || request.subject_sha256 !== subject) {
      return fail(new ApiError('server', '/novels/' + sid + '/approvals/structure', {
        message: 'Stage4 结构审批对象已变化；请刷新后重审',
      }))
    }
    if (JSON.stringify(submittedChapters) !== JSON.stringify(expectedChapters)) {
      return fail(new ApiError('server', '/novels/' + sid + '/approvals/structure', {
        message: '结构裁决章集必须精确等于当前计划',
      }))
    }
    const accepted = enumAt(enums().structure_approval_decisions, 0, '结构审批决定')
    const allAccepted = request.chapters.every((row) => row.decision === accepted)
    const decision: StructureApprovalDecision = {
      ...request,
      chapters: request.chapters.map((row) => ({ ...row })),
      decided_at: new Date().toISOString(),
    }
    const gate = { ...doc.gate, s4: allAccepted }
    if (!allAccepted) {
      gate.s5 = false
      gate.s6 = false
      gate.s7 = false
    }
    return later(saveDoc({
      ...doc,
      gate,
      writing: allAccepted ? doc.writing : false,
      structure: {
        state: enumAt(
          enums().structure_approval_states,
          allAccepted ? 2 : 3,
          '结构审批状态',
        ),
        current_subject_sha256: subject,
        current_decision: decision,
        history: [...doc.structure.history, decision],
      },
    }))
  },

  backfillApprovals: (sid) => {
    const doc = loadDoc(sid)
    const backfillable = GATE_KEYS.slice(0, 4)
    const backfilled = backfillable.filter((key) => !doc.gate[key])
    const approvals = saveDoc({
      ...doc,
      gate: {
        ...doc.gate,
        ...Object.fromEntries(backfillable.map((key) => [key, true])),
      },
      seen: { read: true, global: true },
      writing: doc.gate.s4 ? doc.writing : false,
    })
    return later({ approvals, backfilled })
  },

  markSeen: (sid, key: SeenKey) => {
    const doc = loadDoc(sid)
    if (doc.seen[key]) return later(doc)
    return later(saveDoc({ ...doc, seen: { ...doc.seen, [key]: true } }))
  },

  startWriting: (sid) => {
    const doc = loadDoc(sid)
    return later(saveDoc({ ...doc, writing: true }))
  },

  resetBook: (sid) => later(saveDoc(freshDoc(sid))),

  saveDraftEdit: (sid, ordinal, req) => {
    lsWrite(`edits.${sid}.${ordinal}`, { segments: req.segments, at: Date.now() })
    return later(finish({
      kind: 'stage5.write',
      label: '章节改稿（人工稿落成新一轮）',
      extra: ['--apply-edit'],
      sid,
      ordinal,
    }))
  },

  retainPretrim: (sid, ordinal, req) => {
    lsWrite(`trim-resolution.${sid}.${ordinal}`, { ...req, action: 'retain_pretrim' })
    return later(finish({
      kind: 'stage5.review',
      label: '保留 pretrim 原稿',
      extra: [
        '--retain-pretrim', '--chapters', String(ordinal),
        '--actor', req.actor, '--reason', req.reason,
      ],
      sid,
      ordinal,
    }))
  },

  approveHook: (sid, req) => {
    lsWrite('hook.' + sid, {
      index: req.pick,
      actor: req.actor,
      reason: req.reason,
      subject_sha256: req.subject_sha256,
    })
    return later(finish({
      kind: 'stage6.approve',
      label: '文案定稿 --pick ' + req.pick,
      extra: [
        '--pick', String(req.pick),
        '--actor', req.actor,
        '--reason', req.reason,
        '--subject-sha256', req.subject_sha256,
      ],
      sid,
      ordinal: null,
    }))
  },

  approveNames: (sid, req) => {
    lsWrite('names.' + sid, { origin: 'human', ...req })
    return later(finish({
      kind: 'stage7.approve',
      label: '人名表定稿',
      extra: [
        '--actor', req.actor,
        '--reason', req.reason,
        '--subject-sha256', req.subject_sha256,
      ],
      sid,
      ordinal: null,
    }))
  },

  adoptResidual: (sid, req) => later(finish({
    kind: 'stage7.rename',
    label: '采纳 high 漏网叫法候选',
    extra: ['--adopt-residual', req.confidence],
    sid,
    ordinal: null,
  })),

  uploadCorpus: (file) => {
    // mock 里只有这一本真书。上传等于把它重置回第 0 步。
    saveDoc(freshDoc(SAMPLE_SID))
    const job = finish(
      {
        kind: 'stage0.remove',
        label: 'stage0 非正文剔除',
        extra: ['--source', 'corpus/' + file.name],
        sid: SAMPLE_SID,
        ordinal: null,
      },
      'stage0 番外扫描',
    )
    return later({ sid: SAMPLE_SID, job })
  },
  // 冻结样本不提供可恢复的目录移动，明确答 nosample，不伪装已经删除。
  trashNovel: (sid) => noBook('/novels/' + sid + '/trash'),
}
