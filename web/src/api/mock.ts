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
  GATE_KEYS,
  type ActionRef,
  type ApprovalDoc,
  type ApprovalWorkpack,
  type BatchTrimResolutionRequest,
  type DesignReviewDecision,
  type DesignReviewRequest,
  type DesignReviewTemplate,
  type DesignReviewTemplateQuery,
  type GateKey,
  type Stage5ClosureDecision,
  type Stage5FindingGroup,
  type Stage5ClosureRequest,
  type Stage5ClosureTemplate,
  type Stage5ClosureWorkpack,
  type Stage6ApprovalWorkpack,
  type Stage7ApprovalWorkpack,
  type StructureApprovalDecision,
  type StructureApprovalRequest,
  type WritingApprovalRequest,
} from '@/types/approvals'
import type { EnumsResponse } from '@/types/enums'
import type { Job, JobLogResponse, JobPreview, JobRequest, JobsResponse } from '@/types/jobs'
import type { Artifact, NovelOverview, NovelsResponse } from '@/types/novels'
import type { RunProgress } from '@/types/run'
import type {
  CheckKind,
  CheckReport,
  DraftEditPreview,
  DraftEditPreviewRequest,
  DraftEditRequest,
  InboxResponse,
  ReviewResponse,
  Stage0EditRequest,
  Stage0Extras,
  Stage0Report,
  Stage0Segments,
  Stage1View,
  Stage2Global,
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
const RAW_V1 = import.meta.glob([
  '../fixtures/backend_v1/*.json',
  // 这些历史材料只作 Global 证据与拒绝旧结构的反例，不再作为页面响应打包。
  '!../fixtures/backend_v1/reading_ch3.json',
  '!../fixtures/backend_v1/check_stage2.json',
  '!../fixtures/backend_v1/global.json',
  '!../fixtures/backend_v1/stage6.json',
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
const MOCK_DESIGN_STRATEGY = 'c'.repeat(64)
const MOCK_DESIGN_CHAPTER = 'b'.repeat(64)
const MOCK_STAGE5_WORKPACK = '7'.repeat(64)
const MOCK_STAGE5_REVIEW = '6'.repeat(64)
const MOCK_DESIGN_REVIEW_TOKEN = 's4dr1_' + MOCK_DESIGN_AGGREGATE
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

function mockDesignReviewTemplate(query: DesignReviewTemplateQuery): DesignReviewTemplate {
  const sample = plotFlowFixture().design_template.chapters.find((c) => c.output_ordinal === SAMPLE_ORDINAL)!.subject
  return {
    actor: query.actor,
    reviewer_kind: query.reviewer_kind,
    review_run_id: query.review_run_id,
    general_reason: query.reason,
    candidate_sha256: MOCK_DESIGN_CANDIDATE,
    aggregate_subject_sha256: MOCK_DESIGN_AGGREGATE,
    review_token: MOCK_DESIGN_REVIEW_TOKEN,
    binding_infeasibility: null,
    preflight: {
      status: enumAt(enums().design_preflight_statuses, 0, 'A 预检状态'),
      candidate_sha256: MOCK_DESIGN_CANDIDATE,
      blueprint_sha256: '6'.repeat(64),
      review_contract: 'stage4-design-review-v2',
      code_version: 'mock-code-v1',
      blocked_owners: [],
      failures: [],
      infeasibility: [],
    },
    repair_capability: {
      repairable: true,
      reason_code: 'ready',
      chapters: [{
        output_ordinal: SAMPLE_ORDINAL,
        committed_rounds: 0,
        request_repair_available: true,
      }],
    },
    strategy: {
      subject: {
        basis: {
          review_contract: 'stage4-design-review-v2',
          root_clean_sha1: 'a'.repeat(40),
          truth_sha256: '1'.repeat(64),
          alias_algorithm: 'hmac-sha256-v1',
        },
        reconstruction_strategy: '打散原章顺序，以因果揭示次序重组场景。',
        chapter_weights: [{ chapter_ordinal: SAMPLE_ORDINAL, weight: 'medium' }],
      },
      subject_sha256: MOCK_DESIGN_STRATEGY,
      placeholder: true,
      template_token: '7'.repeat(64),
      decision: enumAt(enums().design_strategy_decisions, 1, 'A 策略审阅决定'),
      reason: '关闭式模板：尚未核实整书策略，保持 request_replan',
      replan_constraints: [],
      source_decision_sha256: null,
    },
    chapters: [{
      output_ordinal: SAMPLE_ORDINAL,
      subject: {
        strategy_subject_sha256: MOCK_DESIGN_STRATEGY,
        blind_truth: sample.blind_truth,
        chapter: sample.chapter,
        structure_evidence: sample.structure_evidence,
      },
      subject_sha256: MOCK_DESIGN_CHAPTER,
      placeholder: true,
      template_token: '6'.repeat(64),
      decision: enumAt(enums().design_chapter_decisions, 1, 'A 逐章审阅决定'),
      reason: '关闭式模板：尚未核实本章候选，保持 request_repair',
      constraints: [{
        code: enumAt(enums().design_repair_codes, 0, 'A 返修码'),
        scene_refs: [],
        // `structure_delta` 退役后首个 active 码是 entry_anchor（arity 0,1）：
        // 泛化重排指令不再可作为不带证据的默认脚手架。
        opaque_aliases: [sample.blind_truth.beats[0].alias],
      }],
      source_decision_sha256: null,
    }],
  }
}

function mockApprovalWorkpack(
  sid: string,
  kind: 'design' | 'structure',
): ApprovalWorkpack {
  const empty = (group: ApprovalWorkpack['groups'][number]['group'], scanned: string) => ({
    group,
    scanned,
    items: [],
  })
  return {
    v: 2,
    sid,
    kind,
    candidate_sha256: kind === 'design' ? MOCK_DESIGN_CANDIDATE : null,
    subject_sha256: kind === 'design' ? MOCK_DESIGN_AGGREGATE : MOCK_STRUCTURE_SUBJECT,
    review_token: kind === 'design'
      ? MOCK_DESIGN_REVIEW_TOKEN
      : MOCK_STRUCTURE_REVIEW_TOKEN,
    chapter_ordinals: [SAMPLE_ORDINAL],
    groups: [
      empty('blocking', '已扫描当前主体的全部 HARD 与预检见证'),
      {
        group: 'semantic_attention',
        scanned: '已扫描全章语义关注面',
        items: [{
          code: 'temporal_binding_unknown',
          rule: 'temporal_binding_unknown',
          sev: 'unver',
          status: 'unver',
          chapter_ordinal: SAMPLE_ORDINAL,
          scene_refs: ['c03s01'],
          refs: ['a_' + '1'.repeat(16)],
          repair_owner: 'human',
          detail: '第 3 章的倒叙入口仍需人工确认时间层绑定。',
          effect: 'attention',
          allowed_actions: [],
          changed: false,
          historical_accept_reusable: false,
          source_decision_sha256: null,
        }],
      },
      {
        group: 'disclosure',
        scanned: '已完整投影 INFO 与绑定关注项',
        items: [{
          code: 'stage_signature_conflict',
          rule: 'stage_signature_conflict',
          sev: 'info',
          status: 'hit',
          chapter_ordinal: SAMPLE_ORDINAL,
          scene_refs: ['c03s01'],
          refs: ['a_' + '2'.repeat(16)],
          repair_owner: null,
          detail: '同一材料在当前章存在多个时空签名，作为完整披露保留。',
          effect: 'disclose',
          allowed_actions: [],
          changed: false,
          historical_accept_reusable: false,
          source_decision_sha256: null,
        }],
      },
      empty('metric_only', '已扫描当前主体全部 metric 读数'),
      empty('phase_deferred', '已登记必须等 stage4.context 后才能判定的规则'),
      {
        group: 'coverage',
        scanned: '全章覆盖：1 / 1',
        items: [{
          code: 'chapter_coverage',
          rule: null,
          sev: null,
          status: null,
          chapter_ordinal: SAMPLE_ORDINAL,
          scene_refs: [],
          refs: [],
          repair_owner: null,
          detail: '第 3 章已进入本次工作包。',
          effect: 'coverage',
          allowed_actions: ['accept'],
          changed: false,
          historical_accept_reusable: true,
          source_decision_sha256: '9'.repeat(64),
        }],
      },
    ],
    disposition: kind === 'design'
      ? loadDoc(sid).design_review.state === 'accepted'
        ? 'already_current'
        : 'review_required'
      : loadDoc(sid).structure.state === 'accepted' ? 'already_current' : 'review_required',
    current_decision_sha256: kind === 'design'
      ? loadDoc(sid).design_review.current_decision?.decision_sha256 ?? null
      : loadDoc(sid).structure.current_decision ? '8'.repeat(64) : null,
  }
}

function seededDesignReviewDecision(): DesignReviewDecision {
  const request = mockDesignReviewTemplate({
    actor: 'mock-reviewer',
    reviewer_kind: enumAt(enums().design_reviewer_kinds, 0, 'A 审阅者类型'),
    review_run_id: '00000000-0000-4000-8000-000000000001',
    reason: 'mock 样本已核对 Stage4A 候选',
  })
  return {
    ...request,
    strategy: {
      ...request.strategy,
      placeholder: false,
      template_token: null,
      decision: enums().design_strategy_decisions[0]!,
      reason: 'mock 已逐项核对当前整书策略',
    },
    chapters: request.chapters.map((chapter) => ({
      ...chapter,
      placeholder: false,
      template_token: null,
      decision: enums().design_chapter_decisions[0]!,
      reason: 'mock 已逐项核对当前章设计',
      constraints: [],
    })),
    decision_sha256: '9'.repeat(64),
    decided_at: '2026-08-28T00:00:00+00:00',
  }
}

function mockDesignHistoryEntry(decision: DesignReviewDecision) {
  return {
    actor: decision.actor,
    reviewer_kind: decision.reviewer_kind,
    review_run_id: decision.review_run_id,
    general_reason: decision.general_reason,
    review_token: MOCK_DESIGN_REVIEW_TOKEN,
    strategy_decision: decision.strategy.decision,
    chapter_decisions: Object.fromEntries(
      decision.chapters.map((row) => [row.output_ordinal, row.decision]),
    ),
    decision_sha256: decision.decision_sha256,
    decided_at: decision.decided_at,
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
  const designDecision = seededDesignReviewDecision()
  return {
    v: 9,
    sid,
    prepare: freshDoc(sid).prepare,
    gate: { s0: true, s1: true, s4: true, s5: true, s6: false, s7: false },
    writing: true,
    design_review: {
      state: enumAt(enums().design_review_states, 4, 'A 审阅状态'),
      candidate_sha256: MOCK_DESIGN_CANDIDATE,
      aggregate_subject_sha256: MOCK_DESIGN_AGGREGATE,
      action_review_token: null,
      current_decision: designDecision,
      history: [mockDesignHistoryEntry(designDecision)],
      binding_infeasibility: null,
      preflight: designDecision.preflight,
      repair_capability: designDecision.repair_capability,
    },
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
  { node: 'stage4.design', label: 'stage4 盲场景设计', paid: true },
  { node: 'stage4.plan', label: 'stage4 重构计划', paid: true },
  { node: 'stage4.context', label: 'stage4 写作上下文组装', paid: false },
  { node: 'stage5.write', label: 'stage5 逐章写作', paid: true },
  { node: 'stage5.review', label: 'stage5 全书验收', paid: false },
  { node: 'stage6.hook', label: 'stage6 开篇文案', paid: true },
  { node: 'stage6.approve', label: 'stage6 文案定稿', paid: false },
  { node: 'stage7.rename', label: 'stage7 人名表', paid: true },
  { node: 'stage7.approve', label: 'stage7 人名表定稿', paid: false },
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
    backend.kind === 'agent'
      ? { kind: 'agent' as const, profile: { ...backend.profile } }
      : { kind: 'legacy' as const, profile: { ...backend.profile } }
  return {
    schema_version: 3 as const,
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

  editStage0: (sid, request) => {
    lsWrite('stage0.selection.' + sid, request)
    return later({
      job: finish({
        kind: 'stage0.edit',
        label: 'stage0 人工编辑',
        extra: null,
        sid,
        ordinal: null,
      }),
      revision_id: 'b'.repeat(64),
    })
  },

  approveStage0: (sid, request) => {
    const current = mockStage0Report(sid)
    if (request.expected_revision_id !== current.revision_id) {
      return fail(new ApiError('server', `/novels/${sid}/stage0/approve`, {
        status: 409,
        message: 'Stage0 页面已过期，请刷新后重新审阅',
        backendKind: 'HumanEditProtected',
      }))
    }
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
  getBlueprintEditTemplate: (sid) => fail(new ApiError('server', `/novels/${sid}/stage2/blueprint/edit/template`, {
    status: 501, message: '旧示例没有当前完整剧情身份，编辑请连接真实后端', backendKind: 'MockCapabilityUnavailable',
  })),
  previewBlueprintEdit: (sid) => fail(new ApiError('server', `/novels/${sid}/stage2/blueprint/edit/preview`, {
    status: 501, message: '示例不执行剧情编辑预检，请连接真实后端', backendKind: 'MockCapabilityUnavailable',
  })),
  getGlobalEditTemplate: (sid) => fail(new ApiError('server', `/novels/${sid}/stage2/global/edit/template`, {
    status: 501, message: '示例没有当前全局事实底座，编辑请连接真实后端', backendKind: 'MockCapabilityUnavailable',
  })),
  previewGlobalEdit: (sid) => fail(new ApiError('server', `/novels/${sid}/stage2/global/edit/preview`, {
    status: 501, message: '示例不执行全局编辑预检，请连接真实后端', backendKind: 'MockCapabilityUnavailable',
  })),
  applyGlobalEdit: (sid) => fail(new ApiError('server', `/novels/${sid}/stage2/global/edit`, {
    status: 501, message: '示例不写入全局订正，请连接真实后端', backendKind: 'MockCapabilityUnavailable',
  })),
  applyBlueprintEdit: (sid) => fail(new ApiError('server', `/novels/${sid}/stage2/blueprint/edit`, {
    status: 501, message: '示例不写入剧情蓝图，请连接真实后端', backendKind: 'MockCapabilityUnavailable',
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
  editAnalyzeDraft: (sid) => fail(new ApiError('server', `/novels/${sid}/stage2/analyze-draft`, {
    status: 501, message: '草稿订正请使用真实后端写回', backendKind: 'MockCapabilityUnavailable',
  })),
  editCharacters: (sid) => fail(new ApiError('server', `/novels/${sid}/stage2/characters/edit`, {
    status: 501, message: '人物订正请使用真实后端写回', backendKind: 'MockCapabilityUnavailable',
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

  getStage6Prepare: (sid) => plotRead(sid, 'stage6/prepare', true),
  getStage6PrepareWorkpack: (sid) => plotRead(sid, 'stage6/prepare/workpack', true),
  previewStage6PrepareEdit: (sid) => readonlyPlot('/novels/' + sid + '/stage6/prepare/edit/preview'),
  applyStage6PrepareEdit: (sid) => readonlyPlot('/novels/' + sid + '/stage6/prepare/edit'),
  reviewStage6Prepare: (sid) => readonlyPlot('/novels/' + sid + '/stage6/prepare/review'),
  getStage6: (sid) => plotRead(sid, 'stage6', true),
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

  getDesignApprovalWorkpack: (sid) => sid === PLOT_SAMPLE_SID
    ? plotRead(sid, 'approvals/design/workpack', true) : later(mockApprovalWorkpack(sid, 'design')),
  getStructureApprovalWorkpack: (sid) => sid === PLOT_SAMPLE_SID
    ? plotRead(sid, 'approvals/structure/workpack', true) : later(mockApprovalWorkpack(sid, 'structure')),
  getStage6ApprovalWorkpack: (sid) => plotRead(sid, 'approvals/stage6/workpack', true),
  getStage7ApprovalWorkpack: (sid) => plotRead(sid, 'approvals/stage7/workpack', true),

  getStage5ClosureTemplate: (sid): Promise<Stage5ClosureTemplate> => {
    const workpack = loadDoc(sid).closure.workpack
    if (!workpack) {
      return fail(new ApiError('server', '/novels/' + sid + '/approvals/stage5/template', {
        message: '当前盘面还不能生成 Stage5 闭合工作包',
      }))
    }
    const template: Stage5ClosureTemplate = {
      actor: '(填写执行人)',
      reason: '(填写整书闭合理由)',
      review_token: workpack.review_token,
      workpack_sha256: workpack.workpack_sha256,
      review_sha256: workpack.review_sha256,
      must_appear: workpack.must_appear.map((row) => ({
        chapter_ordinal: row.chapter_ordinal,
        evidence_key: row.evidence_key,
        finding_sha256: row.finding_sha256,
        chapter_sha256: row.chapter_sha256,
        ruling: '',
        reason: '',
        placeholder: true,
        template_token: workpack.workpack_sha256.slice(0, 8) + '-' + row.finding_sha256.slice(0, 8),
      })),
      group_acks: [workpack.fact_group, workpack.unver_group]
        .filter((group): group is Stage5FindingGroup => group !== null)
        .map((group) => ({
          group: group.group,
          findings_sha256: group.findings_sha256,
          items: group.items,
          placeholder: true,
          template_token: workpack.workpack_sha256.slice(0, 8) + '-' + group.group,
        })),
    }
    return later(template)
  },

  approveStage5Closure: (sid, request: Stage5ClosureRequest) => {
    const doc = loadDoc(sid)
    const workpack = doc.closure.workpack
    const invalid = !workpack
      || request.review_token !== workpack.review_token
      || request.must_appear.length !== workpack.must_appear.length
      || request.must_appear.some((row) => !workpack.must_appear.some((current) => (
        current.chapter_ordinal === row.chapter_ordinal
        && current.evidence_key === row.evidence_key
      )))
      || request.group_acks.some((row) => row.acknowledged !== true)
    if (invalid) {
      return fail(new ApiError('server', '/novels/' + sid + '/approvals/stage5', {
        message: 'Stage5 闭合模板已过期或仍含未确认 placeholder',
      }))
    }
    const decision: Stage5ClosureDecision = {
      ...request,
      decision_sha256: '4'.repeat(64),
      decided_at: new Date().toISOString(),
    }
    return later(saveDoc({
      ...doc,
      gate: { ...doc.gate, s5: true },
      closure: {
        state: 'closed',
        workpack,
        next_step: null,
        current_decision: decision,
        history: [...doc.closure.history, decision],
      },
    }))
  },

  getDesignReviewTemplate: (sid, query: DesignReviewTemplateQuery) => {
    const doc = loadDoc(sid)
    const template = mockDesignReviewTemplate(query)
    const current = doc.design_review.current_decision
    if (
      current
      && current.candidate_sha256 === template.candidate_sha256
      && current.aggregate_subject_sha256 === template.aggregate_subject_sha256
    ) {
      const acceptedStrategy = enumAt(
        enums().design_strategy_decisions, 0, 'A 策略审阅决定',
      )
      return later({
        ...template,
        strategy: current.strategy.decision === acceptedStrategy
          ? {
              ...template.strategy,
              decision: current.strategy.decision,
              reason: current.strategy.reason,
              placeholder: false,
              template_token: null,
              source_decision_sha256: current.decision_sha256,
            }
          : template.strategy,
        chapters: template.chapters.map((chapter) => {
          const prior = current.chapters.find((row) => row.output_ordinal === chapter.output_ordinal)
          return prior?.decision === enums().design_chapter_decisions[0]
            ? {
                ...chapter,
                decision: prior.decision,
                reason: prior.reason,
                constraints: [],
                placeholder: false,
                template_token: null,
                source_decision_sha256: current.decision_sha256,
              }
            : chapter
        }),
      })
    }
    return later(template)
  },

  approveDesignReview: (sid, request: DesignReviewRequest) => {
    const doc = loadDoc(sid)
    const materialized = mockDesignReviewTemplate({
      actor: request.actor,
      reviewer_kind: request.reviewer_kind,
      review_run_id: request.review_run_id,
      reason: request.general_reason,
    })
    if (request.review_token !== materialized.review_token) {
      return fail(new ApiError('server', '/novels/' + sid + '/approvals/design', {
        message: 'Stage4A 审阅对象已变化；请刷新关闭式模板后重审',
      }))
    }
    const acceptedStrategy = enumAt(enums().design_strategy_decisions, 0, 'A 策略审阅决定')
    const acceptedChapter = enumAt(enums().design_chapter_decisions, 0, 'A 逐章审阅决定')
    const requestRepair = enumAt(enums().design_chapter_decisions, 1, 'A 逐章审阅决定')
    const invalidChapter = request.chapters.some((chapter) => (
      (chapter.decision === acceptedChapter && (chapter.constraints.length > 0
        || chapter.constraint_operations?.items.some((item) => item.kind === 'supersede')))
      || (chapter.decision === requestRepair && chapter.constraints.length === 0
        && !chapter.constraint_operations?.items.some((item) => (
          item.kind === 'keep' || (item.kind === 'supersede' && item.replacements.length > 0)
        )))
      || (
        chapter.decision === requestRepair
        && !materialized.repair_capability.chapters.find(
          (row) => row.output_ordinal === chapter.output_ordinal,
        )?.request_repair_available
      )
    ))
    if (invalidChapter) {
      return fail(new ApiError('server', '/novels/' + sid + '/approvals/design', {
        message: 'accept 章不得带返修约束；request_repair 必须可执行且至少选择一个返修码',
      }))
    }
    const decision: DesignReviewDecision = {
      ...materialized,
      actor: request.actor,
      reviewer_kind: request.reviewer_kind,
      review_run_id: request.review_run_id,
      general_reason: request.general_reason,
      strategy: {
        ...materialized.strategy,
        placeholder: false,
        template_token: null,
        decision: request.strategy.decision,
        reason: request.strategy.reason,
        replan_constraints: request.strategy.replan_constraints.map((row) => ({
          ...row,
          subject_sha256: materialized.strategy.subject_sha256,
        })),
      },
      chapters: materialized.chapters.map((chapter) => {
        const ruling = request.chapters.find(
          (row) => row.output_ordinal === chapter.output_ordinal,
        )!
        return {
          ...chapter,
          placeholder: false,
          template_token: null,
          decision: ruling.decision,
          reason: ruling.reason,
          constraints: ruling.constraints,
          ...(ruling.constraint_operations ? { constraint_operations: ruling.constraint_operations } : {}),
        }
      }),
      decision_sha256: '8'.repeat(64),
      decided_at: new Date().toISOString(),
    }
    const stateIndex = request.strategy.decision !== acceptedStrategy
      ? 3
      : request.chapters.some((chapter) => chapter.decision !== acceptedChapter)
        ? 2
        : 4
    return later(saveDoc({
      ...doc,
      design_review: {
        state: enumAt(enums().design_review_states, stateIndex, 'A 审阅状态'),
        candidate_sha256: materialized.candidate_sha256,
        aggregate_subject_sha256: materialized.aggregate_subject_sha256,
        action_review_token: stateIndex === 4 ? null : 's4rr1_' + decision.decision_sha256,
        current_decision: decision,
        history: [...doc.design_review.history, mockDesignHistoryEntry(decision)],
        binding_infeasibility: materialized.binding_infeasibility,
        preflight: materialized.preflight,
        repair_capability: materialized.repair_capability,
      },
    }))
  },

  approveGate: (sid, key: GateKey) => {
    if (key === 's4' || key === 's5') {
      return fail(new ApiError('server', '/novels/' + sid + '/approvals/gate/s4', {
        message: key === 's4'
          ? 'Stage4 只接受带完整 subject 与逐章理由的专用结构裁决'
          : 'Stage5 只接受绑定当前工作包的 Commander 闭合回执',
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
    if (subject === null || request.review_token !== doc.structure.review_token) {
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
        review_token: MOCK_STRUCTURE_REVIEW_TOKEN,
        current_decision: decision,
        history: [...doc.structure.history, decision],
        binding_concerns: doc.structure.binding_concerns,
      },
    }))
  },

  backfillApprovals: (sid) => {
    const doc = loadDoc(sid)
    // 历史书缺当前 stage2 产物与 Core 证据，不能借独立合成书的材料补批；
    // stage4 入口已改由统一机械准入衔接，补录只覆盖 s0/s1 两个人工闸。
    const backfillable = GATE_KEYS.slice(0, 2)
    const backfilled = backfillable.filter((key) => !doc.gate[key])
    const approvals = saveDoc({
      ...doc,
      gate: {
        ...doc.gate,
        ...Object.fromEntries(backfillable.map((key) => [key, true])),
      },
      writing: doc.gate.s4 ? doc.writing : false,
    })
    return later({ approvals, backfilled })
  },

  startWriting: (sid, request: WritingApprovalRequest) => {
    const doc = loadDoc(sid)
    if (request.review_token !== doc.writing_approval.review_token) {
      return fail(new ApiError('server', '/novels/' + sid + '/approvals/writing', {
        message: 'Stage5 写作授权对象已变化；请刷新后重试',
      }))
    }
    const decision = {
      ...request,
      decided_at: new Date().toISOString(),
    }
    return later(saveDoc({
      ...doc,
      writing: true,
      writing_approval: {
        ...doc.writing_approval,
        current_decision: decision,
        history: [...doc.writing_approval.history, decision],
      },
    }))
  },

  resetBook: (sid) => later(saveDoc(freshDoc(sid))),

  previewDraftEdit: (sid, ordinal, req: DraftEditPreviewRequest) => {
    const current = currentMockReview().chapter.findings.filter((finding) => finding.sev === 'hard')
    const preview = {
      output_ordinal: ordinal,
      current_chapter_sha256: '3'.repeat(64),
      preview_subject_sha256: '4'.repeat(64),
      normalized_segments: req.segments.map((segment) => ({
        ...segment,
        text: segment.text.trim(),
      })),
      findings: currentMockReview().chapter.findings,
      current_hard_findings: current,
      introduced_hard_findings: [],
      resolved_hard_findings: [],
      required_confirmation: 'formal',
    } satisfies DraftEditPreview
    lsWrite(`edit-preview.${sid}.${ordinal}`, { ...preview, at: Date.now() })
    return later(preview)
  },

  saveDraftEdit: (sid, ordinal, req: DraftEditRequest) => {
    lsWrite(`edits.${sid}.${ordinal}`, { ...req, at: Date.now() })
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

  retainPretrimBatch: (sid, req: BatchTrimResolutionRequest) => {
    for (const item of req.items) {
      lsWrite(`trim-resolution.${sid}.${item.output_ordinal}`, {
        actor: req.actor,
        reason: item.reason.trim() || req.reason,
        action: 'retain_pretrim',
        audit_sha256: item.audit_sha256,
        chapter_sha256: item.chapter_sha256,
      })
    }
    return later(finish({
      kind: 'stage5.review',
      label: '批量保留 pretrim 原稿',
      extra: ['--retain-pretrim-batch', '(mock manifest)'],
      sid,
      ordinal: null,
    }))
  },

  approveHook: (sid) => readonlyPlot('/novels/' + sid + '/stage6/approve'),
  approveNames: (sid) => readonlyPlot('/novels/' + sid + '/stage7/approve'),
  adoptResidual: (sid) => readonlyPlot('/novels/' + sid + '/stage7/adopt-residual'),

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
  // service 生命周期维护只属于真实本地服务；mock 不伪造 preview token 或删除结果。
  previewRetention: () => noBook('/maintenance/retention/preview'),
  applyRetention: () => noBook('/maintenance/retention/apply'),
  previewTrashRestore: () => noBook('/maintenance/trash/restore/preview'),
  applyTrashRestore: () => noBook('/maintenance/trash/restore/apply'),
}
import plotReviewSample from '@/fixtures/plot_v1/review.json'
import plotFlowSample from '@/fixtures/plot_v1/flow.json'
/** sid 来自正式合成书，不能把历史小说的批准移植到这里。 */
export const PLOT_SAMPLE_SID = plotFlowSample['http:overview'].sid
export function plotFlowFixture() {
  return structuredClone(plotFlowSample) as unknown as {
    design_template: import('@/types/approvals').DesignReviewTemplate
    design_workpack: import('@/types/approvals').ApprovalWorkpack
    structure_workpack: import('@/types/approvals').ApprovalWorkpack
    'http:reading/1': import('@/types/stages').ReadingResponse
    'http:reading/2': import('@/types/stages').ReadingResponse
    'http:reading/3': import('@/types/stages').ReadingResponse
    'http:stage4/1': import('@/types/stages').Stage4Response
    'http:stage4/2': import('@/types/stages').Stage4Response
    'http:stage4/3': import('@/types/stages').Stage4Response
    'http:stage5/1': import('@/types/stages').Stage5Response
    'http:run': import('@/types/run').RunProgress
    metadata: { synthetic: true; sid: string; source: string; scope: string }
    'final:http:stage6/prepare': import('@/types/prepare').PrepareView
    'final:http:stage6/prepare/workpack': import('@/types/prepare').PrepareWorkpack
    'final:http:stage6': Stage6Response
    'final:http:stage7': Stage7Response
    'final:http:stage7/book.txt': string
    'final:http:approvals': ApprovalDoc
    'final:http:approvals/stage6/workpack': Stage6ApprovalWorkpack
    'final:http:approvals/stage7/workpack': Stage7ApprovalWorkpack
  }
}

/** 正式合成链录制的界面测试材料；不与历史小说或审批自动拼接。 */
export function plotReviewFixture() {
  return structuredClone(plotReviewSample) as unknown as {
    prepare: import('@/types/prepare').PrepareWorkpack
    hook: Stage6ApprovalWorkpack
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
