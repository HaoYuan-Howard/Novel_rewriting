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
  type SeenKey,
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
  BlueprintReviewWorkpack,
  BlueprintSeenRequest,
  Stage2GlobalSeenRequest,
  DraftEditPreview,
  DraftEditPreviewRequest,
  DraftEditRequest,
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
    actor: 'mock-commander',
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
        blind_truth: {
          chapter_ordinal: SAMPLE_ORDINAL,
          chapter_function: '冲突升级并改写人物关系',
          facts: [
            {
              fact_alias: 'a_' + '1'.repeat(16),
              claim: '主角在公开场合取得关键证据。',
              kind: 'event',
              obligation: 'must_land',
              participant_aliases: ['a_' + '4'.repeat(16)],
            },
            {
              fact_alias: 'a_' + '2'.repeat(16),
              claim: '对手尚不知道证据已经转手。',
              kind: 'knowledge',
              obligation: 'context',
              participant_aliases: ['a_' + '5'.repeat(16)],
            },
            {
              fact_alias: 'a_' + '3'.repeat(16),
              claim: '章节结尾必须保留证据来源悬念。',
              kind: 'boundary',
              obligation: 'must_land',
              participant_aliases: [],
            },
          ],
          causal_edges: [{
            before_alias: 'a_' + '1'.repeat(16),
            after_alias: 'a_' + '3'.repeat(16),
          }],
          entry_state_aliases: ['a_' + '2'.repeat(16)],
          ending_state_aliases: ['a_' + '3'.repeat(16)],
        },
        chapter: {
          output_ordinal: SAMPLE_ORDINAL,
          structure_decision: {
            mode: 'reconstruct',
            reason: '改变入口与事实组合，避免逐段复述。',
            declared_change_kinds: ['entry_anchor', 'fact_grouping'],
          },
          chapter_goal: { intent: 'advance', fact_aliases: ['a_' + '1'.repeat(16)] },
          open_hook_goal: { intent: 'open', fact_aliases: ['a_' + '2'.repeat(16)] },
          end_hook_goal: { intent: 'withhold', fact_aliases: ['a_' + '3'.repeat(16)] },
          weight: 'medium',
          chapter_rewrite_brief: '由结果切入，再倒推人物动机。',
          scenes: [{
            candidate_scene_id: 'c03s01',
            weight: 'medium',
            purpose: '用结果现场建立冲突，再回收前因。',
            role: 'turn',
            delivery: 'dramatize',
            transition: 'flashback',
            land_fact_aliases: ['a_' + '1'.repeat(16)],
            context_fact_aliases: ['a_' + '2'.repeat(16)],
            turn_fact_alias: 'a_' + '1'.repeat(16),
            entry_state_aliases: ['a_' + '2'.repeat(16)],
            exit_state_aliases: ['a_' + '3'.repeat(16)],
          }],
        },
        structure_evidence: {
          metric_contract: 'atomic-structure-v2',
          state: 'adequate',
          declared_change_kinds: ['entry_anchor', 'fact_grouping'],
          computed_change_kinds: ['entry_anchor', 'fact_grouping'],
          realized_declared: ['entry_anchor', 'fact_grouping'],
          unrealized_declared: [],
          undeclared_actual: [],
          readings: {
            entry_anchor_changed: true,
            noncontiguous_groups: 1,
            source_order_correlation: 0.4,
          },
        },
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
        opaque_aliases: ['a_' + '1'.repeat(16)],
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
        : loadDoc(sid).design_review.state === 'suspended'
          ? 'suspended'
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
    v: 6,
    sid,
    gate: { s0: false, s1: false, s2: false, s4: false, s5: false, s6: false, s7: false },
    seen: { read: false, global: false },
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
    v: 6,
    sid,
    gate: { s0: true, s1: true, s2: true, s4: true, s5: true, s6: false, s7: false },
    seen: { read: true, global: true },
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
        reason: '两场共用同一事实材料，绑定可行但需要人工确认重复披露风险。',
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
  // spawns 是服务端的事；只接受当前公开 v6，旧本地记录按当前种子重建。
  if (got && typeof got === 'object' && (got as { v?: unknown }).v === 6) {
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
    generation_disposition: null,
  }
  // ⚠ `effect` / `group_key` 由服务端派生，mock 只是**转录**后端会下发的值——
  //   这里不许现算一份分类逻辑，那正是屏幕侧被禁止的第二个判官。
  return {
    source_sha256: MOCK_SHA.source,
    current_subject_sha256: MOCK_SHA.source,
    current: true,
    approved: true,
    generation_safe: true,
    quarantined_groups: [],
    review_token: MOCK_GLOBAL_SEEN_TOKEN,
    effect_counts: {
      blocking: { pending: 0, PASS: 4, FAIL: 0, UNVER: 0 },
      disclosure: { pending: 1, PASS: 0, FAIL: 0, UNVER: 0 },
    },
    items: [
      {
        item_id: 'character_set_complete:entities.characters:' + MOCK_SHA.characters.slice(0, 12),
        kind: 'character_set_complete',
        subject_path: 'entities.characters',
        subject_sha256: MOCK_SHA.characters,
        effect: 'blocking',
        group_key: 'set:characters',
        ...decided,
      },
      {
        item_id: 'relation_set_complete:entities.relations:' + MOCK_SHA.relations.slice(0, 12),
        kind: 'relation_set_complete',
        subject_path: 'entities.relations',
        subject_sha256: MOCK_SHA.relations,
        effect: 'blocking',
        group_key: 'set:relations',
        ...decided,
      },
      {
        item_id: 'world_rule_set_complete:canon.world_rules:' + MOCK_SHA.rules.slice(0, 12),
        kind: 'world_rule_set_complete',
        subject_path: 'canon.world_rules',
        subject_sha256: MOCK_SHA.rules,
        effect: 'blocking',
        group_key: 'set:world_rules',
        ...decided,
      },
      {
        item_id: 'secret_set_complete:canon.secrets:' + MOCK_SHA.secrets.slice(0, 12),
        kind: 'secret_set_complete',
        subject_path: 'canon.secrets',
        subject_sha256: MOCK_SHA.secrets,
        effect: 'blocking',
        group_key: 'set:secrets',
        ...decided,
      },
      // 一条**未决的披露项**：它不挡 Stage4，但界面必须把它画出来。
      {
        item_id: 'fate:entities.characters.char_001.fate:' + MOCK_SHA.characters.slice(0, 12),
        kind: 'fate',
        subject_path: 'entities.characters.char_001.fate',
        subject_sha256: MOCK_SHA.characters,
        effect: 'disclosure',
        group_key: 'character:char_001',
        state: 'pending',
        evidence: [],
        actor: null,
        decided_at: null,
        reason: null,
        generation_disposition: null,
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
    ['python', '-m', 'novel_refactor.cli', kind, '--sid', req.sid, '--check-only'].join(' ')
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
  { node: 'stage4.design', label: 'stage4 盲场景设计', paid: true },
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
    profiles: req.profiles ?? null,
  }
}

function mockAgentRunRecord(req: AgentRunRequest): AgentRunRecord {
  const preview = mockAgentRunPreview(req)
  const start = {
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
    schema_version: 2 as const,
    commander: { ...profiles.commander },
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

function mockBlueprintReviewWorkpack(sid: string): BlueprintReviewWorkpack {
  const reading = fx<ReadingResponse>('reading_ch3.json')
  const beatOrder = new Map(reading.beats.map((beat, index) => [beat.beat_id, index + 1]))
  const segments = reading.segments.map((segment) => ({
    beat_order: segment.beat_id === null ? null : (beatOrder.get(segment.beat_id) ?? null),
    chars: segment.chars,
    text: segment.text,
    flagged: segment.flagged,
  }))
  const covered = segments
    .filter((segment) => segment.beat_order !== null)
    .reduce((total, segment) => total + segment.chars, 0)
  const loose = segments
    .filter((segment) => segment.beat_order === null)
    .reduce((total, segment) => total + segment.chars, 0)
  const subject = 'b'.repeat(64)
  return {
    sid,
    title: reading.title,
    read_only: true,
    auto_decisions: false,
    review_contract_version: 'stage2-blueprint-review-v5',
    global_review: {},
    review_identity_sha256: 'e'.repeat(64),
    validation_seal_sha256: 'f'.repeat(64),
    review_token: 'bpr3_' + 'c'.repeat(64),
    subject_sha256: subject,
    binding_sha256: 'c'.repeat(64),
    binding: {
      blueprint_rel: 'stage2/chapter_blueprints.json',
      subject_kind: 'formal',
      main_text_artifact_sha256: 'a'.repeat(64),
      blueprint_artifact_sha256: subject,
      check_report_sha256: 'd'.repeat(64),
      chapter_ordinals: [reading.chapter_ordinal],
    },
    check: {
      rules_scanned: 0,
      counts: { ok: 0, hit: 0, unver: 0, skipped: 0 },
      provenance: {
        freshness: 'unknown',
        reason: 'mock_sample_only',
        ghosts: [],
        edges: [],
        invalid: false,
        revalidated: null,
        origin: null,
      },
      attention: [],
      metrics: [],
    },
    accounting: [],
    mechanical_fields_hidden: [
      'blueprints[].beats[].beat_id',
      'blueprints[].beats[].span.start/end',
      'blueprints[].fact_atoms[].fact_id',
      'blueprints[].fact_atoms[].source_beat_ref',
    ],
    chapters: [{
      chapter_ordinal: reading.chapter_ordinal,
      anomalous: false,
      attention_rules: [],
      chapter_function: 'mock 仅保留抓包章节；真实后端返回全书完整工作包',
      coverage: {
        text_chars: reading.text_chars,
        covered_chars: covered,
        loose_chars: loose,
        uncovered_min_chars: reading.uncovered_min_chars,
        segments,
      },
      beats: reading.beats.map((beat, index) => ({
        beat_id: beat.beat_id,
        beat_order: index + 1,
        located: beat.located,
        time_layer: beat.time_layer,
        place: beat.place,
        present_char_ids: beat.present_char_ids,
        facts: beat.fact_atoms.map((fact) => ({
          fact_id: fact.fact_id,
          claim: fact.claim,
          kind: fact.kind,
          obligation: fact.obligation,
          evidence_text: fact.evidence_text,
          participant_char_ids: fact.participant_char_ids,
        })),
        hinges: beat.hinges,
      })),
      causal_links: [],
      narrative_relations: [],
      entry_state_claims: [],
      ending_state_claims: [],
      narration: reading.narration,
    }],
  }
}

const BLUEPRINT_SEEN_FIELDS = [
  'review_token',
] as const satisfies readonly (keyof BlueprintSeenRequest)[]

function expectedBlueprintSeenRequest(sid: string): BlueprintSeenRequest {
  const workpack = mockBlueprintReviewWorkpack(sid)
  return {
    review_token: workpack.review_token,
  }
}

/** mock review 的 seen.global 凭证；测试用它模拟“读完当前 review 后确认”。 */
export const MOCK_GLOBAL_SEEN_TOKEN = 's2gr1_' + '5'.repeat(64)

function isCurrentGlobalSeenRequest(
  sid: string,
  request: BlueprintSeenRequest | Stage2GlobalSeenRequest | undefined,
): request is Stage2GlobalSeenRequest {
  if (!request || !('actor' in request)) return false
  const keys = Object.keys(request).sort()
  if (keys.join(',') !== 'actor,review_token') return false
  if (!request.actor.trim()) return false
  return request.review_token === loadGlobalReview(sid).review_token
}

function isCurrentBlueprintSeenRequest(
  sid: string,
  request: BlueprintSeenRequest | Stage2GlobalSeenRequest | undefined,
): request is BlueprintSeenRequest {
  if (!request) return false
  const keys = Object.keys(request)
  if (keys.length !== BLUEPRINT_SEEN_FIELDS.length
    || keys.some((key) => !BLUEPRINT_SEEN_FIELDS.includes(
      key as (typeof BLUEPRINT_SEEN_FIELDS)[number],
    ))) return false
  const expected = expectedBlueprintSeenRequest(sid)
  return BLUEPRINT_SEEN_FIELDS.every((field) => request[field] === expected[field])
}

/* ------------------------------------------------------------------ Api */

export const mockApi: Api = {
  approveStage2Review: async () => {
    throw new Error('离线样本未提供 Stage2 节点接受，请连接真实服务')
  },
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

  getStage1: (sid) => bookOnly(sid, `/novels/${sid}/stage1`, mockStage1View),

  getReading: (sid, o) =>
    chapterOnly(sid, o, `/novels/${sid}/reading/${o}`, () => fx<ReadingResponse>('reading_ch3.json')),

  getBlueprintReviewWorkpack: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage2/blueprint-review-workpack`, () =>
      mockBlueprintReviewWorkpack(sid)),

  getGlobal: (sid, revealSpoilers = false) =>
    bookOnly(sid, `/novels/${sid}/stage2/global`, () =>
      projectGlobalSpoilers(fx<Stage2Global>('global.json'), revealSpoilers),
    ),

  getGlobalReview: (sid) =>
    bookOnly(sid, `/novels/${sid}/stage2/global-review`, () => loadGlobalReview(sid)),

  decideGlobalReview: (sid, request: GlobalReviewDecisionSet) => {
    const path = `/novels/${sid}/stage2/global-review`
    if (sid !== SAMPLE_SID) return noBook(path)
    // Commander 的 compact scope 绑定 CLI workpack SHA；浏览器 mock 没有那份
    // 只读材料，不能忽略 scope 后假装提交成功。真实后端/API 原样支持该形状。
    if (request.blocking_scope) {
      return fail(new ApiError('server', path, {
        status: 501,
        message: 'mock 不模拟 hash-bound blocking workpack；请使用真实后端',
        backendKind: 'MockCapabilityUnavailable',
      }))
    }
    if (!request.actor.trim() || request.decisions.length === 0
      || request.decisions.some((row) => !row.reason.trim())) {
      return fail(new ApiError('server', path, {
        status: 422,
        message: 'actor/reason 不得只有空白，且逐项提交不能为空',
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
    const blocking = items.filter((item) => item.effect === 'blocking')
    const approved = review.current && blocking.length > 0
      && blocking.every((item) => item.state === 'PASS')
    saveGlobalReview(sid, {
      ...review,
      items,
      approved,
      generation_safe: approved,
      quarantined_groups: [],
    })
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
        workpack_sha256: string
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
            workpack_sha256: edit.workpack_sha256,
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

  getApprovals: (sid) => later(loadDoc(sid)),

  getDesignApprovalWorkpack: (sid) => later(mockApprovalWorkpack(sid, 'design')),
  getStructureApprovalWorkpack: (sid) => later(mockApprovalWorkpack(sid, 'structure')),
  getStage6ApprovalWorkpack: (sid) =>
    bookOnly(sid, `/novels/${sid}/approvals/stage6/workpack`, () => {
      const workpack_sha256 = '6'.repeat(64)
      const review_identity_sha256 = '8'.repeat(64)
      const current = lsRead<{
        index: number
        actor: string
        reason: string
        subject_sha256: string
        workpack_sha256: string
        review_identity_sha256: string
      }>('hook.' + sid)
      const view = fx<Stage6Response>('stage6.json')
      const decisionOf = (subject_sha256: string, text: string) => ({
        ability: 'reference_boundary',
        subject_key: subject_sha256,
        verdict: 'UNVER' as const,
        reason: 'mock 两名盲评证据交给人工终审，不自动放行',
        evidence: [{
          artifact: 'stage6/hook.json', subject_sha256, start: 0, end: text.length,
        }],
        judges: ['A', 'B'].map((judge) => ({
          judge,
          verdict: 'UNVER' as const,
          reason: `mock 判官 ${judge}：需人工结合开篇正文核验`,
          evidence: [{
            artifact: 'stage6/hook.json', subject_sha256, start: 0, end: text.length,
          }],
        })),
      })
      return {
        v: 3,
        kind: 'stage6',
        sid,
        review_contract_version: 'stage6-final-review-v3',
        review_identity_sha256,
        validation_seal_sha256: '9'.repeat(64),
        review_token: 's6r3_' + workpack_sha256,
        artifact_subject_sha256: 'a'.repeat(64),
        rationale: view.rationale,
        check: {
          rules_scanned: 0,
          counts: { ok: 0, hit: 0, unver: 0, skipped: 0 },
          provenance: {
            freshness: 'fresh', reason: 'match', ghosts: [], edges: [],
            invalid: false, revalidated: null, origin: null,
          },
          attention: [], metrics: [],
        },
        judge_rankings: {
          A: view.candidates.map((candidate) => 'c' + candidate.index),
          B: [...view.candidates].reverse().map((candidate) => 'c' + candidate.index),
        },
        candidates: view.candidates.map((candidate) => ({
          index: candidate.index,
          slot: 'c' + candidate.index,
          approach: candidate.approach,
          chars: candidate.chars,
          text: candidate.text,
          subject_sha256: candidate.subject_sha256,
          mech: candidate.mech,
          findings: candidate.findings,
          semantic_state: candidate.semantic_state,
          semantic_reason: candidate.semantic_reason,
          semantic_review_seq: candidate.semantic_state === 'unreviewed' ? null : 1,
          semantic_review: {
            label: 'c' + candidate.index,
            coverage: 'complete',
            uncertainty_reason: null,
            contradictions: candidate.contradictions,
            quality: {
              read_on_desire: 8,
              body_voice_match: 8,
              concrete_scene_and_specificity: 8,
              curiosity_and_payoff_promise: 8,
              hook_craft: 8,
              synopsis_penalty: 0,
            },
            concise_reason: candidate.semantic_reason,
            text_sha1: candidate.subject_sha256.slice(0, 10),
          },
          semantic_decision: decisionOf(candidate.subject_sha256, candidate.text),
          quality_score: candidate.quality_score,
          quality_rank: candidate.quality_rank,
        })),
        workpack_sha256,
        disposition: current?.review_identity_sha256 === review_identity_sha256
          ? 'already_current'
          : 'review_required',
        current_slot: current?.review_identity_sha256 === review_identity_sha256
          ? 'c' + current.index
          : null,
        current_approval: current?.review_identity_sha256 === review_identity_sha256
          ? {
              actor: current.actor,
              reason: current.reason,
              subject_sha256: current.subject_sha256,
              workpack_sha256: current.workpack_sha256,
              review_identity_sha256: current.review_identity_sha256,
            }
          : null,
      } satisfies Stage6ApprovalWorkpack
    }),
  getStage7ApprovalWorkpack: (sid) =>
    bookOnly(sid, `/novels/${sid}/approvals/stage7/workpack`, () => {
      const workpack_sha256 = '7'.repeat(64)
      const review_identity_sha256 = 'a'.repeat(64)
      const current = lsRead<{
        actor: string
        reason: string
        subject_sha256: string
        workpack_sha256: string
        review_identity_sha256: string
      }>('names.' + sid)
      const view = fx<Stage7Response>('stage7.json')
      return {
        v: 3,
        kind: 'stage7',
        sid,
        review_contract_version: 'stage7-final-review-v3',
        review_identity_sha256,
        validation_seal_sha256: 'b'.repeat(64),
        review_token: 's7r3_' + workpack_sha256,
        artifact_subject_sha256: 'b'.repeat(64),
        decision_subject_sha256: view.approval_subject_sha256,
        check: {
          rules_scanned: 0,
          counts: { ok: 0, hit: 0, unver: 0, skipped: 0 },
          provenance: {
            freshness: 'fresh', reason: 'match', ghosts: [], edges: [],
            invalid: false, revalidated: null, origin: null,
          },
          attention: [], metrics: [],
        },
        surname_map: view.surname_map,
        characters: view.characters,
        extra_subs: view.extra_subs,
        subs_count: view.subs_count,
        kept_count: view.kept_count,
        kept: Array.from(
          { length: view.kept_count },
          (_value, index) => `mock明确保留${index + 1}`,
        ),
        replacement_hits: Object.fromEntries(
          view.characters.map((character) => [character.old_canonical, character.hits]),
        ),
        residual: view.residual,
        baselines: view.baselines,
        book: view.book,
        identity: view.identity,
        workpack_sha256,
        disposition: current?.review_identity_sha256 === review_identity_sha256
          ? 'already_current'
          : 'review_required',
        current_approval: current?.review_identity_sha256 === review_identity_sha256
          ? {
              actor: current.actor,
              reason: current.reason,
              subject_sha256: current.subject_sha256,
              workpack_sha256: current.workpack_sha256,
              review_identity_sha256: current.review_identity_sha256,
            }
          : null,
      } satisfies Stage7ApprovalWorkpack
    }),

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
    // Stage2 必须由两个独立 seen 共同打开。盘上没有 seen 证据时，backfill
    // 只能补到 s1，不能替人制造 read/global 或越过它们补 s2/s4。
    const stage2Seen = doc.seen.read && doc.seen.global
    const backfillable = GATE_KEYS.slice(0, stage2Seen ? 3 : 2)
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

  markSeen: (sid, key: SeenKey, request: BlueprintSeenRequest | Stage2GlobalSeenRequest) => {
    const path = '/novels/' + sid + '/approvals/seen/' + key
    if (key === 'read' && !isCurrentBlueprintSeenRequest(sid, request)) {
      return fail(new ApiError('server', path, {
        status: 409,
        message: 'seen.read 必须逐项绑定当前 Blueprint 工作包；请刷新阅读页后重试',
        backendKind: 'ApprovalRequired',
      }))
    }
    if (key === 'global' && !isCurrentGlobalSeenRequest(sid, request)) {
      return fail(new ApiError('server', path, {
        status: 409,
        message: 'seen.global 必须携带当前 review 的 review_token 与非空确认人；主体变化后请重新通读',
        backendKind: 'ApprovalRequired',
      }))
    }
    const doc = loadDoc(sid)
    if (doc.seen[key]) return later(doc)
    return later(saveDoc({ ...doc, seen: { ...doc.seen, [key]: true } }))
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

  approveHook: (sid, req) => {
    const candidate = fx<Stage6Response>('stage6.json').candidates.find(
      (row) => row.index === req.pick)
    if (!candidate || req.review_token !== 's6r3_' + '6'.repeat(64)) {
      return fail(new ApiError('server', `/novels/${sid}/stage6/approve`, {
        message: 'review token 已过期',
      }))
    }
    lsWrite('hook.' + sid, {
      index: req.pick,
      actor: req.actor,
      reason: req.reason,
      subject_sha256: candidate.subject_sha256,
      workpack_sha256: '6'.repeat(64),
      review_identity_sha256: '8'.repeat(64),
    })
    return later(finish({
      kind: 'stage6.approve',
      label: '文案定稿 --pick ' + req.pick,
      extra: [
        '--pick', String(req.pick),
        '--actor', req.actor,
        '--reason', req.reason,
        '--review-token', req.review_token,
      ],
      sid,
      ordinal: null,
    }))
  },

  approveNames: (sid, req) => {
    const view = fx<Stage7Response>('stage7.json')
    if (req.review_token !== 's7r3_' + '7'.repeat(64)) {
      return fail(new ApiError('server', `/novels/${sid}/stage7/approve`, {
        message: 'review token 已过期',
      }))
    }
    lsWrite('names.' + sid, {
      origin: 'human', actor: req.actor, reason: req.reason,
      subject_sha256: view.approval_subject_sha256,
      workpack_sha256: '7'.repeat(64),
      review_identity_sha256: 'a'.repeat(64),
    })
    return later(finish({
      kind: 'stage7.approve',
      label: '人名表定稿',
      extra: [
        '--actor', req.actor,
        '--reason', req.reason,
        '--review-token', req.review_token,
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
  // service 生命周期维护只属于真实本地服务；mock 不伪造 preview token 或删除结果。
  previewRetention: () => noBook('/maintenance/retention/preview'),
  applyRetention: () => noBook('/maintenance/retention/apply'),
  previewTrashRestore: () => noBook('/maintenance/trash/restore/preview'),
  applyTrashRestore: () => noBook('/maintenance/trash/restore/apply'),
}
