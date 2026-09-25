/**
 * 假后端。
 *
 * 这是**整个前端里唯一 import src/mocks/*.json 的文件**，也是唯一碰 localStorage 的文件。
 * 它不是「fixture 加载器」，是一个真的会写盘（写 localStorage）、会跑作业、会拒绝
 * 「这一份不在样本里」的服务端替身 —— 所以屏与组件在 mock 模式下走的代码路径
 * 和接了真后端之后**完全一样**。
 *
 * 三件事故意做在这里而不是渲染层：
 *   1. 历史样例与当前完整剧情测试书的范围 —— 由假后端分别接入，不拼接身份
 *   2. 命令行的拼装 —— U-09 要求确认框里那条命令由服务端下发
 *   3. 花不花钱、会不会写盘 —— 前端一行判定都不许有
 */
import type { Api } from '@/types/api'
import type {
  AgentCapabilitiesResponse,
  AgentProfileSettingsView,
  AgentQueueView,
  AgentRunLogChunk,
  AgentRunPreview,
  AgentRunRecord,
  AgentRunRequest,
  AgentRunsResponse,
} from '@/types/agent'
import {
  type ActionRef,
  type ApprovalDoc,
  type GateKey,
  type Stage5ClosureDecision,
  type Stage5ClosureWorkpack,
  type Stage7ApprovalWorkpack,
  type StructureApprovalDecision,
} from '@/types/approvals'
import type { EnumsResponse } from '@/types/enums'
import type {
  Str06BlueprintView,
  Str06CandidateList,
  Str06CharactersView,
  Str06ChapterView,
  Str06DeliveryView,
  Str06DesignView,
  Str06HookView,
  Str06IrNavView,
  Str06NamingView,
  Str06RunView,
} from '@/types/str06'
import type {
  Job,
  JobLogResponse,
  JobPreview,
  JobRequest,
  JobsResponse,
  UploadCorpusOptions,
  UploadResponse,
} from '@/types/jobs'
import type { Artifact, NovelOverview, NovelsResponse } from '@/types/novels'
import type { RunProgress } from '@/types/run'
import type {
  CheckKind,
  CheckReport,
  InboxResponse,
  ReviewResponse,
  Stage0Extras,
  Stage0Report,
  Stage0Segments,
  Stage1View,
  Stage2Global,
  Stage5Response,
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

/* ------------------------------------------ 冻结原样（只加载仍消费的三份历史输入） */

const RAW = import.meta.glob(
  [
    '../mocks/novels.json',
    '../mocks/novel_overview.json',
    '../mocks/stage0_extras.json',
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
  'stage0', 'stage1', 'stage2', 'stage4', 'stage5', 'stage7',
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
const RAW_V1 = import.meta.glob([
  '../fixtures/backend_v1/*.json',
  // 这些历史材料只作 Global 证据与拒绝旧结构的反例，不再作为页面响应打包。
  '!../fixtures/backend_v1/reading_ch3.json',
  '!../fixtures/backend_v1/check_stage2.json',
  '!../fixtures/backend_v1/global.json',
  '!../fixtures/backend_v1/stage7.json',
  '!../fixtures/backend_v1/stage7_ch3.json',
], {
  eager: true,
  import: 'default',
}) as Record<string, unknown>

function fx<T>(file: string): T {
  const hit = RAW_V1['../fixtures/backend_v1/' + file]
  if (hit === undefined) throw new ApiError('malformed', '/fixtures/' + file)
  return hit as T
}

/* --------------------------------------------- STR-06 候选链监控 fixture（F7d） */

/** 录制的合成候选根（fake transport 全链产物，非真实书正文；与真后端同形状）。 */
interface Str06Fixture {
  candidates: Str06CandidateList
  run: Str06RunView
  characters: Str06CharactersView
  blueprint: Str06BlueprintView
  design: Str06DesignView
  ir: Str06IrNavView
  chapter1: Str06ChapterView
  hook: Str06HookView
  naming: Str06NamingView
  delivery: Str06DeliveryView
  book: string
}

/** 样本候选根名：与 fixture 的 candidates[0].candidate 对账（写死两份必然分家）。 */
const STR06_FIXTURE_CANDIDATE = 'cand'
/** 样本只录了第 1 章；别的章号答 nosample，不编一章空数据。 */
const STR06_FIXTURE_CHAPTER = 1

function str06Fixture(): Str06Fixture {
  return fx<Str06Fixture>('str06_candidate.json')
}

function str06Read<K extends keyof Omit<Str06Fixture, 'book'>>(
  candidate: string, key: K,
): Promise<Str06Fixture[K]> {
  if (candidate !== STR06_FIXTURE_CANDIDATE) {
    return noBook('/str06/candidates/' + candidate + '/' + key)
  }
  return later(structuredClone(str06Fixture()[key]))
}

function str06ReadBook(candidate: string): string {
  if (candidate !== STR06_FIXTURE_CANDIDATE) {
    throw new ApiError('nosample', '/str06/candidates/' + candidate + '/book')
  }
  return str06Fixture().book
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

/** 完整合成书只读快照：缺键不回退其他小说或历史批准。 */
function plotRead<T>(sid: string, suffix: string, final = false,
  scope: 'book' | 'chapter' = 'book'): Promise<T> {
  const path = `/novels/${sid}/${suffix}`
  if (sid !== PLOT_SAMPLE_SID) return noBook(path)
  const data = plotFlowSample as Record<string, unknown>
  const key = suffix || 'overview'
  const value = data['final:http:' + key] ?? (final ? undefined : data['http:' + key])
  return value === undefined
    ? fail(new ApiError('nosample', path, { scope }))
    : later(structuredClone(value) as T)
}

function readonlyPlot(path: string): Promise<never> {
  return fail(new ApiError('server', path, { status: 501,
    message: '此样例只录制正式读回，不执行编辑、批准或作业；请连接真实后端。',
    backendKind: 'MockCapabilityUnavailable' }))
}

async function plotNovel(): Promise<NovelOverview> {
  // 复用真实 HTTP 适配器；延后加载避免 endpoints 选择 mock 时的初始化循环。
  const { adaptNovel } = await import('./endpoints')
  return adaptNovel(await plotRead<Parameters<typeof adaptNovel>[0]>(PLOT_SAMPLE_SID, 'overview', true))
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
const MOCK_DESIGN_CANDIDATE = 'e'.repeat(64)
const MOCK_DESIGN_AGGREGATE = 'd'.repeat(64)
const MOCK_STAGE5_WORKPACK = '7'.repeat(64)
const MOCK_STAGE5_REVIEW = '6'.repeat(64)
const MOCK_STRUCTURE_REVIEW_TOKEN = 's4sr1_' + MOCK_STRUCTURE_SUBJECT
const MOCK_WRITING_REVIEW_TOKEN = 's5wr1_' + MOCK_STRUCTURE_SUBJECT
const MOCK_STAGE5_REVIEW_TOKEN = 's5cr1_' + MOCK_STAGE5_WORKPACK

function mockStage5Workpack(): Stage5ClosureWorkpack {
  return {
    review_seq: 1,
    review_rel: 'stage5/review_1.json',
    review_sha256: MOCK_STAGE5_REVIEW,
    planned_chapters: [SAMPLE_ORDINAL],
    missing_chapters: [],
    chapter_subjects: [{
      output_ordinal: SAMPLE_ORDINAL,
      chapter_sha256: '3'.repeat(64),
    }],
    invalid_chapters: [],
    hard_findings: [],
    unresolved_trim_chapters: [],
    unresolved_trim_items: [],
    trim_subjects: [],
    wontfix_sha256: null,
    must_appear: [],
    fact_group: null,
    unver_group: null,
    disclosure: {},
    resolved: {},
    workpack_sha256: MOCK_STAGE5_WORKPACK,
    review_token: MOCK_STAGE5_REVIEW_TOKEN,
  }
}

function mockStage5ClosureDecision(): Stage5ClosureDecision {
  return {
    actor: 'mock-reviewer',
    reason: 'mock 样本已完成整书闭合复核',
    review_token: MOCK_STAGE5_REVIEW_TOKEN,
    must_appear: [],
    group_acks: [],
    decision_sha256: '5'.repeat(64),
    decided_at: '2026-08-29T00:00:00+00:00',
  }
}

function enumAt(values: string[], index: number, label: string): string {
  const value = values[index]
  if (!value) throw new ApiError('malformed', '/fixtures/enums.json', {
    message: label + ' 取值面缺少第 ' + (index + 1) + ' 项',
  })
  return value
}

function seededStructureDecision(): StructureApprovalDecision {
  return {
    actor: 'mock-reviewer',
    reason: 'mock 样本已核对全书结构',
    review_token: MOCK_STRUCTURE_REVIEW_TOKEN,
    chapters: [{
      output_ordinal: SAMPLE_ORDINAL,
      decision: enumAt(enums().structure_approval_decisions, 0, '结构审批决定'),
      reason: 'mock 第 3 章结构已核对',
    }],
    decided_at: '2026-08-25T00:00:00+00:00',
  }
}

/**
 * Stage4A 审阅投影的现役形状（与录制样本一致）：历史账只读、无新决定。
 * workpack/模板与 typed 决定写面已随 2026-09-15 全自动裁决退役。
 */
function mockDesignReviewProjection() {
  return {
    state: enumAt(enums().design_review_states, 1, 'A 审阅状态'),
    candidate_sha256: MOCK_DESIGN_CANDIDATE,
    aggregate_subject_sha256: MOCK_DESIGN_AGGREGATE,
    action_review_token: null,
    current_decision: null,
    history: [],
    binding_infeasibility: null,
    preflight: null,
    repair_capability: {
      repairable: null,
      reason_code: null,
      agent_repairable: false,
      agent_reason_code: 'legacy_or_untracked_candidate',
      full_chapter_editable: true,
      full_chapter_edit_reason_code: 'current_typed_decision',
      chapters: [{
        output_ordinal: SAMPLE_ORDINAL,
        committed_rounds: 0,
        request_repair_available: null,
        agent_repair_available: false,
        full_chapter_edit_available: true,
      }],
    },
  }
}

function freshDoc(sid: string): ApprovalDoc {
  return {
    v: 9,
    sid,
    prepare: { state: 'unknown', reason: '此历史样例未记录独立 Prepare，不能继承为已接受。',
      subject_sha256: null, review_token: null, prepare: null, review: null, pending_commit: false },
    gate: { s0: false, s1: false, s4: false, s5: false, s6: false, s7: false },
    writing: false,
    design_review: {
      state: enumAt(enums().design_review_states, 0, 'A 审阅状态'),
      candidate_sha256: null,
      aggregate_subject_sha256: null,
      action_review_token: null,
      current_decision: null,
      history: [],
      binding_infeasibility: null,
      preflight: null,
      repair_capability: null,
    },
    structure: {
      state: enumAt(enums().structure_approval_states, 0, '结构审批状态'),
      current_subject_sha256: null,
      review_token: null,
      current_decision: null,
      history: [],
      binding_concerns: [],
    },
    writing_approval: {
      available: false,
      current_subject_sha256: null,
      review_token: null,
      current_decision: null,
      history: [],
    },
    closure: {
      state: 'missing',
      workpack: null,
      next_step: null,
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
    v: 9,
    sid,
    prepare: freshDoc(sid).prepare,
    gate: { s0: true, s1: true, s4: true, s5: true, s6: false, s7: false },
    writing: true,
    // Stage4A 人工审阅已退役：样本按现役投影呈现无决定、无历史的历史账。
    design_review: mockDesignReviewProjection(),
    structure: {
      state: enumAt(enums().structure_approval_states, 2, '结构审批状态'),
      current_subject_sha256: MOCK_STRUCTURE_SUBJECT,
      review_token: MOCK_STRUCTURE_REVIEW_TOKEN,
      current_decision: decision,
      history: [decision],
      binding_concerns: [{
        constraint_code: 'material_conflict',
        chapter_ordinal: SAMPLE_ORDINAL,
        scenes: ['c03s01'],
        reason: '两场共用同一段证据，需审阅联合呈现是否遗漏或重复；证据重叠不直接判重复。',
        closed_refs: ['a_' + '1'.repeat(16)],
      }],
    },
    writing_approval: {
      available: true,
      current_subject_sha256: MOCK_STRUCTURE_SUBJECT,
      review_token: MOCK_WRITING_REVIEW_TOKEN,
      current_decision: null,
      history: [],
    },
    closure: {
      state: 'closed',
      workpack: mockStage5Workpack(),
      next_step: null,
      current_decision: mockStage5ClosureDecision(),
      history: [mockStage5ClosureDecision()],
    },
    spawns: {},
  }
}

function loadDoc(sid: string): ApprovalDoc {
  const got = lsRead<unknown>('approvals.' + sid)
  // spawns 是服务端的事；只接受当前公开 v9，旧本地记录（含已退役 seen/s2）不沿用审批。
  if (got && typeof got === 'object' && (got as { v?: unknown }).v === 9) {
    return withMockSpawns(got as ApprovalDoc)
  }
  if (!got && sid === SAMPLE_SID) {
    // 样本书的 stage2 两份产物已在盘上（历史录制），对应「已发起」的 s1 续跑状态。
    lsWrite('stage2.global.issued.' + sid, true)
    lsWrite('stage2.blueprint.issued.' + sid, true)
  }
  const seed = !got && sid === SAMPLE_SID ? seededDoc(sid) : freshDoc(sid)
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

/* Stage2 语义审阅已退役：mock 不再保存全局复核项、已读记录或逐节点接受。 */

/* ------------------------------------------------------------ 命令行拼装 */

/** 枚举取值直接透传当前后端生成的样例；仅补历史显示辅助字段。 */
function enums(): EnumsResponse {
  const frozen = fx<EnumsResponse>('enums.json')
  if (frozen.spoiler_disclosure) return frozen
  return {
    ...frozen,
    spoiler_disclosure: {
      mode: 'display_only',
      security_boundary: false,
      server_sends_full_data: true,
      hidden_paths: ['arc_secrets'],
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
  const parts = ['python', '-m', 'novel.cli', req.kind, '--sid', req.sid]
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
    executor_backend: null,
    egress_authorized: null,
    steps: previewStepsOf(planned),
  }
}

function previewStepsOf(req: JobRequest): JobPreview['steps'] {
  const first = {
    label: req.label,
    paid: paidOf(req),
    writes: 'mock 产物',
    argv: argvOf(req),
    executor_backend: null,
    egress_authorized: null,
  }
  const checkArgv = (kind: string) =>
    ['python', '-m', 'novel.cli', kind, '--sid', req.sid, '--check-only'].join(' ')
  if (req.kind === 'stage5.write') {
    return [
      first,
      {
        label: 'stage5 成稿 · 追跑全量校验',
        paid: false,
        writes: '',
        argv: checkArgv('stage5.write'),
        executor_backend: null,
        egress_authorized: null,
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
    executor_backend: null,
    egress_authorized: null,
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
    executor_backend: j.executor_backend ?? null,
    egress_authorized: j.egress_authorized ?? null,
  }
}

function publicJobs(sid: string): JobsResponse {
  const mine = JOBS.filter((j) => j.sid === sid).map(stripLog)
  return { jobs: mine, running: mine.find((j) => j.state === 'running') ?? null }
}

/** 确认之后自动发起的下游。谁发起什么由服务端决定，不由界面决定。 */
function withMockSpawns(doc: ApprovalDoc): ApprovalDoc {
  // mock 用「已发起」标记近似后端 resume 谓词：s1 分相下发 stage2.global →
  // stage2.blueprint（骨架由统一机械准入衔接）；两份都发起后不再有下一相。
  const s1: ActionRef[] = !doc.gate.s1 ? []
    : !lsRead<boolean>('stage2.global.issued.' + doc.sid)
      ? [{ kind: 'stage2.global', label: '生成 stage2 全局底座', extra: null }]
      : !lsRead<boolean>('stage2.blueprint.issued.' + doc.sid)
        ? [{ kind: 'stage2.blueprint', label: '机械准入通过后生成 stage2 逐章骨架', extra: null }]
        : []
  const spawns: Partial<Record<GateKey, ActionRef[]>> = {
    s1,
    // 历史样本未经过现役独立 Prepare，不能提供本轮 Hook/改名/交付动作。
    s5: [], s6: [], s7: [],
  }
  return { ...doc, spawns }
}

function codePoints(text: string): number {
  return [...text].length
}

function mockStage0Segments(_sid: string): Stage0Segments {
  const data = raw<Stage0Extras>('stage0_extras.json')
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
    // stage0 人工选择已随 2026-09-15 全自动裁决退役：mock 只呈现机器结论。
    const initial = piece.machine
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
    revision_id: 'a'.repeat(64),
    revision_origin: 'machine' as const,
  }
}

function mockStage1View(): Stage1View {
  return fx<Stage1View>('stage1.json')
}

/* ---------------------------------------------------------- 跑书进度样例 */

/** mock 的静态镜像；真实响应由后端直接从 NODES 派生。 */
const PIPELINE_STEPS: { node: string; label: string; paid: boolean }[] = [
  { node: 'stage0.remove', label: 'stage0 非正文剔除', paid: false },
  { node: 'stage1.partition', label: 'stage1 分章', paid: false },
  { node: 'stage2.global', label: 'stage2 全书级抽取', paid: true },
  { node: 'stage2.blueprint', label: 'stage2 逐章抽取', paid: true },
  { node: 'stage4.design', label: 'stage4 盲场景设计', paid: true },
  { node: 'stage4.plan', label: 'stage4 重构计划', paid: true },
  { node: 'stage4.context', label: 'stage4 写作上下文组装', paid: false },
  { node: 'stage5.write', label: 'stage5 逐章写作', paid: true },
  { node: 'stage5.review', label: 'stage5 全书验收', paid: false },
  { node: 'stage7.rename', label: 'stage7 人名表', paid: true },
  { node: 'stage7.assemble', label: 'stage7 替换与成书', paid: false },
]

/** 静态样例：全节点 pending + 空事件流 + 全 null 的 stage_done。 */
function mockRunProgress(sid: string): RunProgress {
  return {
    sid,
    resume_frontier: {
      state: 'ready',
      node: 'stage0.remove',
      seq: 1,
      label: 'stage0 非正文剔除',
      resume_verdict: 'run',
      why: 'mock：stage0 产物尚未形成',
      waiting_on: null,
      paid: false,
      writes: 'artifact',
      next_handler: 'code',
      verified_skipped_nodes: [],
      verified_skipped_count: 0,
    },
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
      stage7: null,
    },
    events_tail: [],
  }
}

/* ---------------------------------------------------------- agent 代跑样例 */

/** 真 argv / prompt 由后端同一构造函数产出；这里是形状样例（拼装只许住在数据层）。 */
function mockAgentRunPreview(req: AgentRunRequest): AgentRunPreview {
  return {
    pause_before_node: req.pause_before_node ?? null,
    argv: ['agent-cli', '--sid', req.sid, '--base', '/mock/base'],
    prompt:
      '由自动流水线完成本书并校验交付条件（sid ' + req.sid + '）。\n' +
      (req.authorized
        ? '付费授权已授予：付费命令不再逐条停。'
        : '付费授权未授予：到第一条付费命令前停下。') +
      '\n' +
      (req.egress_authorized
        ? '正文外发授权已授予：允许发送正文及派生上下文。'
        : '正文外发授权未授予：不得发送正文及派生上下文。'),
    authorized: req.authorized,
    egress_authorized: req.egress_authorized,
    profiles: req.profiles ?? null,
  }
}

function mockAgentRunRecord(req: AgentRunRequest): AgentRunRecord {
  const preview = mockAgentRunPreview(req)
  const start = {
    ...(preview.pause_before_node ? { pause_before_node: preview.pause_before_node } : {}),
    sid: req.sid,
    base: '/mock/base',
    argv: preview.argv,
    prompt: preview.prompt,
    authorized: preview.authorized,
    egress_authorized: preview.egress_authorized,
    code_version: 'mock-code-version',
    skill_sha256: 'a'.repeat(64),
    profiles: preview.profiles,
  }
  return {
    run_id: 'agentrun_mock',
    start,
    contract: {
      wire_version: 2,
      ...(preview.pause_before_node ? { pause_before_node: preview.pause_before_node } : {}),
      sid: start.sid,
      base: start.base,
      authorized: start.authorized,
      egress_authorized: start.egress_authorized,
      profiles: req.profiles,
    },
    epochs: [],
    active_epoch_id: null,
    started: Math.floor(Date.now() / 1000),
    ended: null,
    exit_code: null,
    state: 'running',
    note: null,
    outcome: null,
    allowed_controls: ['pause', 'stop'],
  }
}

let mockAgentSettingsSequence = 0
let mockAgentProfileSettings: AgentProfileSettingsView = {
  state: 'unset',
  profiles: null,
  revision: '0'.repeat(64),
  updated_at: null,
  issue: null,
}

function cloneMockProfiles(profiles: AgentProfileSettingsView['profiles']) {
  if (profiles === null) return null
  const cloneBackend = (backend: typeof profiles.executor_default) =>
    ({ kind: 'legacy' as const, profile: { ...backend.profile } })
  return {
    schema_version: 4 as const,
    executor_default: cloneBackend(profiles.executor_default),
    executor_overrides: Object.fromEntries(
      Object.entries(profiles.executor_overrides).map(([node, backend]) => [
        node,
        cloneBackend(backend),
      ]),
    ),
  }
}

function mockControlledAgentRun(runId: string, state: 'paused' | 'unknown'): AgentRunRecord {
  const profiles = fx<AgentCapabilitiesResponse>('agent_capabilities.json').default_profiles
  if (profiles === null) throw new Error('mock agent capability 必须提供 default_profiles')
  const record = mockAgentRunRecord({
    sid: SAMPLE_SID,
    authorized: false,
    egress_authorized: true,
    profiles,
  })
  return {
    ...record,
    run_id: runId,
    state,
    note: state === 'unknown' ? 'mock：熄火后结局不明，不改判 ok' : 'mock：安全暂停',
    allowed_controls: state === 'paused' ? ['resume', 'stop'] : [],
    ended: state === 'unknown' ? Math.floor(Date.now() / 1000) : null,
  }
}

function mockInbox(): InboxResponse {
  const legacy = fx<InboxResponse>('inbox.json')
  const items = legacy.items.map((item) => ({
    ...item,
    attention: item.attention ?? ('pending' as const),
    delivery_effect:
      item.delivery_effect ??
      (item.kind === 'human_only'
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

/** 历史 seen token 常量已随 Stage2 语义审阅退役删除；mock 不再提供已读/接受面。 */

/* ------------------------------------------------------------------ Api */

export const mockApi: Api = {
  getNovels: async () => {
    const legacy = mockNovels()
    const data = plotFlowSample as Record<string, unknown>
    return data['final:http:overview']
      ? { ...legacy, novels: [await plotNovel(), ...legacy.novels] } : legacy
  },

  getNovel: (sid) => sid === PLOT_SAMPLE_SID ? plotNovel() :
    bookOnly(sid, `/novels/${sid}`, () =>
      currentMockNovel(raw<LegacyNovel>('novel_overview.json')),
    ),

  // 进度屏在 stage0.remove 前就要能开（书目录可能还没建）——任何 sid 都给静态样例，不走 bookOnly
  getRunProgress: (sid) => sid === PLOT_SAMPLE_SID ? plotRead(sid, 'run', true) : later(mockRunProgress(sid)),
  getRunMetrics: (sid, runId) => runId ? noBook(`/novels/${sid}/run/metrics?run_id=${runId}`)
    : plotRead(sid, 'run/metrics', true),
  previewWorkUnitGrant: async () => { throw new Error('离线样本未提供工作单元额度授权，请连接真实服务') },
  grantWorkUnit: async () => { throw new Error('离线样本未提供工作单元额度授权，请连接真实服务') },

  getEnums: () => later(enums()),

  getStage0Report: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage0/report`, () => mockStage0Report(sid)),

  getStage0Segments: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage0/segments`, () => mockStage0Segments(sid)),

  // stage0 人工写面与手动 partition 已退役；mock 只提供只读展示。

  getStage1: (sid) => sid === PLOT_SAMPLE_SID ? plotRead(sid, 'stage1')
    : bookOnly(sid, `/novels/${sid}/stage1`, mockStage1View),

  getReading: (sid, o) => sid === PLOT_SAMPLE_SID ? plotRead(sid, 'reading/' + o, false, 'chapter') :
    fail(new ApiError('server', `/novels/${sid}/reading/${o}`, {
      status: 501, message: '此历史示例使用已退役的事实蓝图。完整剧情阅读请连接当前后端。',
      backendKind: 'MockCapabilityUnavailable',
    })),

  getBlueprintReadingWorkpack: (sid, scope = 'cross_chapter') => sid === PLOT_SAMPLE_SID
    ? plotRead(sid, 'stage2/blueprint-reading-workpack?scope=' + encodeURIComponent(scope), true)
    : fail(new ApiError('server', `/novels/${sid}/stage2/blueprint-reading-workpack`, {
    status: 501,
    message: '阅读工作包需要当前源文与 Blueprint 的完整只读材料，请连接真实后端',
    backendKind: 'MockCapabilityUnavailable',
  })),

  getGlobal: (sid, revealSpoilers = false) => {
    if (sid === PLOT_SAMPLE_SID) return plotRead<Stage2Global>(sid, 'stage2/global')
      .then((value) => projectGlobalSpoilers(value, revealSpoilers))
    const current = stage2CoreFixture().global
    return sid === current.sid
      ? later(projectGlobalSpoilers(current, revealSpoilers))
      : fail(new ApiError('nosample', `/novels/${sid}/stage2/global`, { scope: 'book' }))
  },

  getCharacters: (sid) => {
    if (sid === PLOT_SAMPLE_SID) return plotRead(sid, 'stage2/characters')
    const current = stage2CoreFixture().characters
    return sid === current.sid ? later(current)
      : fail(new ApiError('nosample', `/novels/${sid}/stage2/characters`, { scope: 'book' }))
  },
  getAnalyzeDraft: (sid) => fail(new ApiError('nosample', `/novels/${sid}/stage2/analyze-draft`, {
    scope: 'book', message: '冻结样本不含待修复联合草稿，请连接真实后端读取。',
  })),
  getStage4: (sid, o) => sid === PLOT_SAMPLE_SID ? plotRead(sid, 'stage4/' + o, false, 'chapter') :
    fail(new ApiError('server', `/novels/${sid}/stage4/${o}`, {
      status: 501, message: '此历史示例没有现役分场呈现与章级上下文，请连接当前后端。',
      backendKind: 'MockCapabilityUnavailable',
    })),

  getStage5: (sid, o) => sid === PLOT_SAMPLE_SID ? plotRead(sid, 'stage5/' + o, true, 'chapter') :
    chapterOnly(sid, o, `/novels/${sid}/stage5/${o}`, currentMockStage5),

  getReview: (sid, o) => sid === PLOT_SAMPLE_SID ? plotRead(sid, 'review/' + o, true, 'chapter') :
    chapterOnly(sid, o, `/novels/${sid}/review/${o}`, currentMockReview),

  getInbox: (sid) => sid === PLOT_SAMPLE_SID ? plotRead(sid, 'inbox', true)
    : bookOnly(sid, `/novels/${sid}/inbox`, mockInbox),

  getStage7: (sid) => plotRead(sid, 'stage7', true),
  getStage7Chapter: (sid, ordinal) => plotRead(sid, 'stage7/' + ordinal, true, 'chapter'),
  getStage7Book: (sid) => plotRead(sid, 'stage7/book.txt', true),
  downloadStage7Book: async (sid) => new Blob(
    [await plotRead<string>(sid, 'stage7/book.txt', true)], { type: 'text/plain;charset=utf-8' }),

  getCheck: (sid, kind: CheckKind) => {
    const path = `/novels/${sid}/check/${kind}`
    if (sid === PLOT_SAMPLE_SID) return plotRead(sid, 'check/' + kind, true)
    if (sid !== SAMPLE_SID) return noBook(path)
    // stage0/1 在样本书上没跑过（真后端答 404）—— null = 「还没跑」，不是「通过」。
    if (kind === 'stage0' || kind === 'stage1') return later(null)
    if (kind === 'stage2') return fail(new ApiError('server', path, {
      status: 501, message: '此历史样本没有当前完整剧情蓝图，不能用旧事实检查作为本轮结论。',
      backendKind: 'MockCapabilityUnavailable',
    }))
    return later(mockCheck(kind))
  },

  getSampleCoverage: () => later({ sid: PLOT_SAMPLE_SID, ordinals: Object.keys(plotFlowSample)
    .filter((key) => /^final:http:stage7\/\d+$/.test(key))
    .map((key) => Number(key.split('/').at(-1))).sort((a, b) => a - b) }),

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

  previewJob: (req) => req.sid === PLOT_SAMPLE_SID || /^stage[67]\./.test(req.kind)
    ? readonlyPlot('/jobs/preview') : later(mockJobPreview(req)),

  startJob: (req) => {
    if (req.sid === PLOT_SAMPLE_SID || /^stage[67]\./.test(req.kind)) {
      return readonlyPlot('/jobs')
    }
    // stage2 两份付费生成各自发起；发起即记录，供 s1 spawns 的下一相判断。
    if (req.kind === 'stage2.global' || req.kind === 'stage2.blueprint') {
      lsWrite(req.kind + '.issued.' + req.sid, true)
    }
    return later(spawn(req))
  },

  /* agent 代跑：静态样例 —— 列表恒空（mock 不养 agent 进程），start 回一条 running 记录 */
  getAgentCapabilities: () => later(fx<AgentCapabilitiesResponse>('agent_capabilities.json')),
  getAgentProfileSettings: () => later({
    ...mockAgentProfileSettings,
    profiles: cloneMockProfiles(mockAgentProfileSettings.profiles),
  }),
  saveAgentProfileSettings: (req) => {
    if (req.expected_revision !== mockAgentProfileSettings.revision) {
      return fail(new ApiError('server', '/settings/agent-profiles', {
        status: 409,
        backendKind: 'AgentProfileSettingsConflict',
        message: '常用 Agent 配置已被另一个页面更新；请重读后再保存',
      }))
    }
    mockAgentSettingsSequence += 1
    mockAgentProfileSettings = {
      state: req.profiles === null ? 'unset' : 'configured',
      profiles: cloneMockProfiles(req.profiles),
      revision: mockAgentSettingsSequence.toString(16).padStart(64, '0'),
      updated_at: Date.now() / 1000,
      issue: null,
    }
    return later({
      ...mockAgentProfileSettings,
      profiles: cloneMockProfiles(mockAgentProfileSettings.profiles),
    })
  },
  getAgentRunContinuation: async () => { throw new Error('离线样本没有历史运行停止证明；请连接真实服务。') },
  previewAgentRun: (req) => later(mockAgentRunPreview(req)),
  startAgentRun: (req) => later(mockAgentRunRecord(req)),
  getAgentRuns: () => later<AgentRunsResponse>({ runs: [] }),
  getAgentRunLog: () => later<AgentRunLogChunk>({ text: '', next_byte: 0 }),
  stopAgentRun: (runId) =>
    later<AgentRunRecord>(mockControlledAgentRun(runId, 'unknown')),
  pauseAgentRun: (runId) =>
    later<AgentRunRecord>(mockControlledAgentRun(runId, 'paused')),
  resumeAgentRun: (runId) =>
    later<AgentRunRecord>({
      ...mockControlledAgentRun(runId, 'paused'),
      state: 'running',
      note: 'mock：沿同一 execution epoch 精确续会',
      allowed_controls: ['pause', 'stop'],
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
          profiles: req.profiles,
        }),
      ),
      pending: req.sids.slice(3).map((sid, i) => ({
        sid,
        position: i + 1,
        profiles: cloneMockProfiles(req.profiles)!,
      })),
      rejected: [],
      max_concurrent: 3,
    }),
  getAgentQueue: () =>
    later<AgentQueueView>({ running: [], pending: [], rejected: [], max_concurrent: 3 }),

  getApprovals: (sid) => sid === PLOT_SAMPLE_SID ? plotRead(sid, 'approvals', true) : later(loadDoc(sid)),

  // stage4 design/structure workpack、模板与 typed 决定写面已随 2026-09-15 全自动
  // 裁决退役：mock 不再模拟这些读/写端点，Stage4 人工闸只剩只读审批投影。
  // Stage6 整层（prepare / 三候选改选 / 定稿写面）已随 STR-06 切换退役，无对应端点。

  getStage7ApprovalWorkpack: (sid) => plotRead(sid, 'approvals/stage7/workpack', true),
  resetBook: (sid) => later(saveDoc(freshDoc(sid))),
  uploadCorpus: (file, options: UploadCorpusOptions): Promise<UploadResponse> => {
    // mock 里只有这一本真书。上传等于把它重置回第 0 步；授权只回显，不真开跑。
    void options.profiles
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
    return later({
      sid: SAMPLE_SID,
      job,
      run: null,
      // mock 不真开跑：授权路径回显 started，未授权保持 null。
      autostart:
        options.authorized && options.egressAuthorized ? 'started' : null,
      idempotency_key: options.idempotencyKey,
    })
  },

  // 冻结样本不提供可恢复的目录移动，明确答 nosample，不伪装已经删除。
  trashNovel: (sid) => noBook('/novels/' + sid + '/trash'),
  // service 生命周期维护只属于真实本地服务；mock 不伪造 preview token 或删除结果。
  previewRetention: () => noBook('/maintenance/retention/preview'),
  applyRetention: () => noBook('/maintenance/retention/apply'),
  previewTrashRestore: () => noBook('/maintenance/trash/restore/preview'),
  applyTrashRestore: () => noBook('/maintenance/trash/restore/apply'),

  /* STR-06 候选链只读监控（F7d）：录制的合成候选根响应，真实后端同形状。
     只读——mock 不伪造任何候选链写动作（那属于 CLI 子进程，不属于服务）。 */
  getStr06Candidates: () => later(str06Fixture().candidates),
  getStr06Run: (candidate) => str06Read(candidate, 'run'),
  getStr06Characters: (candidate) => str06Read(candidate, 'characters'),
  getStr06Blueprint: (candidate) => str06Read(candidate, 'blueprint'),
  getStr06Design: (candidate) => str06Read(candidate, 'design'),
  getStr06Ir: (candidate) => str06Read(candidate, 'ir'),
  getStr06Chapter: (candidate, o) =>
    o === STR06_FIXTURE_CHAPTER ? str06Read(candidate, 'chapter1')
      : noChapter('/str06/candidates/' + candidate + '/chapters/' + o),
  getStr06Hook: (candidate) => str06Read(candidate, 'hook'),
  getStr06Naming: (candidate) => str06Read(candidate, 'naming'),
  getStr06Delivery: (candidate) => str06Read(candidate, 'delivery'),
  getStr06Book: (candidate) => later(str06ReadBook(candidate)),
}
import plotReviewSample from '@/fixtures/plot_v1/review.json'
import plotFlowSample from '@/fixtures/plot_v1/flow.json'
/** sid 来自正式合成书，不能把历史小说的批准移植到这里。 */
export const PLOT_SAMPLE_SID = plotFlowSample['http:overview'].sid
export function plotFlowFixture() {
  return structuredClone(plotFlowSample) as unknown as {
    'http:reading/1': import('@/types/stages').ReadingResponse
    'http:reading/2': import('@/types/stages').ReadingResponse
    'http:reading/3': import('@/types/stages').ReadingResponse
    'http:stage4/1': import('@/types/stages').Stage4Response
    'http:stage4/2': import('@/types/stages').Stage4Response
    'http:stage4/3': import('@/types/stages').Stage4Response
    'http:stage5/1': import('@/types/stages').Stage5Response
    'http:run': import('@/types/run').RunProgress
    metadata: { synthetic: true; sid: string; source: string; scope: string }
    'final:http:stage7': Stage7Response
    'final:http:stage7/book.txt': string
    'final:http:approvals': ApprovalDoc
    'final:http:approvals/stage7/workpack': Stage7ApprovalWorkpack
  }
}

/** 正式合成链录制的界面测试材料；不与历史小说或审批自动拼接。 */
export function plotReviewFixture() {
  return structuredClone(plotReviewSample) as unknown as {
    rename: Stage7ApprovalWorkpack
  }
}

/** 当前 Core 真实只读响应，独立于历史小说及完整剧情链的审批材料。 */
import stage2CoreSample from '@/fixtures/plot_v1/core.json'
export function stage2CoreFixture() {
  return structuredClone(stage2CoreSample) as unknown as {
    metadata: { synthetic: true; source: string; schemas: Record<string, number>; scope: string }
    global: Stage2Global
    characters: import('@/types/characters').CharacterRegistryView
  }
}
