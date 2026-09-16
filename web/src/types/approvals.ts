import type { Finding, RuleLine } from './common'

/**
 * 人工验收记录。
 *
 * 在真产品里这是服务端的东西（人点了哪些确认、是否已经批准开写）。
 * mock 版本里它由 mock.ts 存在 localStorage —— 但**取数路径和真实响应完全一样**：
 * 走 api.getApprovals() / api.approveGate()，
 * 屏与组件从不直接碰传输层。
 *
 * 这些字段合成一份文档而不是各存一张 map：一份文档、一次写、一个生命周期。
 * Stage2 语义审阅退役后（v9），stage2 的推进只认统一机械准入与后端 spawns；
 * 页面分别展示机械可用性与「Stage2 语义未审」，不再保存已读/逐节点接受记录。
 * Stage4 四道人工闸也已退役（2026-09-15 全自动裁决）：design/structure 的
 * workpack、模板与 POST 写面全部删除，这里只剩 GET /approvals 的只读投影。
 */

export type GateKey = 's0' | 's1' | 's4' | 's5' | 's6' | 's7'

export const GATE_KEYS: GateKey[] = ['s0', 's1', 's4', 's5', 's6', 's7']

/** 一条会被发起的动作。paid 不在这里 —— 它要去 /api/enums 里查，那才是权威。 */
export interface ActionRef {
  kind: string
  label: string
  extra: string[] | null
}

/**
 * Stage4A 正式设计审阅。
 *
 * decision / reviewer_kind / constraint.code 的取值面全部来自 `/api/enums`；
 * 这里刻意保留 string，不在 TypeScript 复制服务端枚举。
 */
export interface DesignReviewBasis {
  review_contract: string
  root_clean_sha1: string
  truth_sha256: string
  alias_algorithm: string
}

export interface DesignChapterWeight {
  chapter_ordinal: number
  weight: string
}

export interface DesignStrategySubject {
  basis: DesignReviewBasis
  reconstruction_strategy: string
  chapter_weights: DesignChapterWeight[]
}

export type DesignGoalSubject = import('./plot.generated').BlindLocalGoalResponse

export type DesignSceneSubject = import('./plot.generated').BlindCandidateScene

export type DesignChapterCandidate = import('./plot.generated').BlindCandidateChapter

export type DesignBlindChapterTruth = import('./plot.generated').DesignBlindChapterTruth

export interface DesignStructureEvidence {
  metric_contract: string
  state: string
  declared_change_kinds: string[]
  computed_change_kinds: string[]
  realized_declared: string[]
  unrealized_declared: string[]
  undeclared_actual: string[]
  /** 服务端复用正式 measure 现算的安全聚合标量；前端只逐项展示。 */
  readings: Record<string, string | number | boolean | null>
}

export interface DesignChapterSubject {
  strategy_subject_sha256: string
  blind_truth: DesignBlindChapterTruth
  chapter: DesignChapterCandidate
  structure_evidence: DesignStructureEvidence
}

export interface DesignRepairConstraint {
  code: string
  scene_refs: string[]
  opaque_aliases: string[]
}

/** 操作种类来自服务端 design_constraint_operation_kinds，页面不定义第二套枚举。 */
export interface DesignConstraintOperation {
  kind: string
  constraint_sha256: string
  reason: string
  replacements: DesignRepairConstraint[]
}

export interface DesignConstraintOperations {
  expected_constraint_set_sha256: string
  items: DesignConstraintOperation[]
}

export interface DesignStrategyReplanConstraint {
  kind: string
  subject_sha256: string
  chapter_ordinals: number[]
}

/**
 * 历史策略审阅行的只读解码形状。四道人工闸退役后不再有新的 GET 模板与
 * POST 决定；`placeholder` / `template_token` 只在存量历史决定上出现。
 */
export interface DesignStrategyReview {
  subject: DesignStrategySubject
  subject_sha256: string
  placeholder: boolean
  template_token: string | null
  decision: string
  reason: string
  replan_constraints: DesignStrategyReplanConstraint[]
  source_decision_sha256: string | null
}

/** 历史逐章审阅行的只读解码形状；生产者已随四道人工闸退役。 */
export interface DesignChapterReview {
  output_ordinal: number
  subject: DesignChapterSubject
  subject_sha256: string
  placeholder: boolean
  template_token: string | null
  decision: string
  reason: string
  constraints: DesignRepairConstraint[]
  constraint_operations?: DesignConstraintOperations
  source_decision_sha256: string | null
}

export interface DesignPreflightFailure {
  code: string
  rule: string
  chapter_ordinal: number | null
  scene_refs: string[]
  refs: string[]
  repair_owner: string
  detail: string
}

export interface DesignPreflightSummary {
  status: string
  candidate_sha256: string
  blueprint_sha256: string
  review_contract: string
  code_version: string
  blocked_owners: string[]
  failures: DesignPreflightFailure[]
  infeasibility: BindingInfeasibilityWitness[]
}

export interface DesignReviewMaterialized {
  actor: string
  reviewer_kind: string
  review_run_id: string
  general_reason: string
  candidate_sha256: string
  aggregate_subject_sha256: string
  /** 服务端只在当前 fresh B=infeasible 时给出；前端不得据候选或 concern 自行构造。 */
  binding_infeasibility: DesignBindingInfeasibility | null
  /** 服务端 A→formal 零调用预检结论；前端只呈现。 */
  preflight: DesignPreflightSummary | null
  /** 当前候选的恢复能力结论（历史决定只读解码）。 */
  repair_capability: DesignReviewRepairCapability
  strategy: DesignStrategyReview
  chapters: DesignChapterReview[]
}

/** 服务端已落盘的历史 A 决定（只读）；正式 POST 决定入口已随四道人工闸退役。 */
export interface DesignReviewDecision extends DesignReviewMaterialized {
  decision_sha256: string
  decided_at: string
}

export interface DesignReviewHistoryEntry {
  actor: string
  reviewer_kind: string
  review_run_id: string
  general_reason: string
  review_token: string
  strategy_decision: string
  chapter_decisions: Record<number, string>
  decision_sha256: string
  decided_at: string
}

export interface DesignReviewView {
  /** 服务端状态是 current / expired / 可进入 B 的唯一结论，前端不复算 hash 或逐章决定。 */
  state: string
  candidate_sha256: string | null
  aggregate_subject_sha256: string | null
  current_decision: DesignReviewDecision | null
  history: DesignReviewHistoryEntry[]
  /** 旧决定铸出的定向续作 token（历史账只读解码；无现役消费）。 */
  action_review_token?: string | null
  /** 当前、fresh 且由代码铸造的 Stage4B 不可行证据；其余状态一律为 null。 */
  binding_infeasibility: DesignBindingInfeasibility | null
  /** 当前候选的 A→formal 生产预检；不可用态为 null。 */
  preflight: DesignPreflightSummary | null
  /** unavailable 时为 null；其余状态是服务端当前候选的唯一恢复能力结论。 */
  repair_capability: DesignReviewRepairCapability | null
}

export interface BindingInfeasibilityWitness {
  chapter_ordinal: number
  scenes: string[]
  constraint_code: string
  closed_refs: string[]
  proof_basis: string
}

export interface DesignBindingInfeasibility {
  candidate_sha256: string
  binding_concerns: StructureBindingConcern[]
  infeasibility_proof: BindingInfeasibilityWitness[]
}

/**
 * 历史决定的恢复能力结论（只读解码）。v1/v2 人工返修布尔与 v3
 * agent/完整章替换两组字段按服务端下发原样呈现；前端不重算可用性。
 */
export interface DesignChapterRepairCapability {
  output_ordinal: number
  committed_rounds: number
  request_repair_available?: boolean | null
  agent_repair_available?: boolean | null
  full_chapter_edit_available?: boolean | null
}

export interface DesignReviewRepairCapability {
  repairable?: boolean | null
  reason_code?: string | null
  agent_repairable?: boolean | null
  agent_reason_code?: string | null
  full_chapter_editable?: boolean | null
  full_chapter_edit_reason_code?: string | null
  chapters: DesignChapterRepairCapability[]
}

/**
 * 历史结构裁决行的只读解码形状（取值面曾是
 * `/api/enums.structure_approval_decisions`）；不再产生新的结构决定。
 */
export interface StructureChapterApproval {
  output_ordinal: number
  decision: string
  reason: string
  source_decision_sha256?: string | null
}

/** 服务端已落盘的一次结构裁决（只读）；POST 写面已随四道人工闸退役。 */
export interface StructureApprovalDecision {
  actor: string
  reason: string
  review_token: string
  chapters: StructureChapterApproval[]
  decided_at: string
}

export interface StructureBindingConcern {
  constraint_code: string
  chapter_ordinal: number | null
  scenes: string[]
  reason: string
  closed_refs: string[]
}

export interface StructureApprovalView {
  /** 取值面来自 `/api/enums.structure_approval_states`。 */
  state: string
  current_subject_sha256: string | null
  /** 历史账只读解码；写面已随四道人工闸退役。 */
  review_token?: string | null
  current_decision: StructureApprovalDecision | null
  history: StructureApprovalDecision[]
  /** Stage4B 下发的非阻断关注项；只展示，不由前端改判。 */
  binding_concerns: StructureBindingConcern[]
}

/** Stage6/7 终审包公共绑定面；屏只提交 opaque review token。 */
export interface FinalApprovalWorkpack<V extends number = 3> {
  v: V
  sid: string
  review_contract_version: string
  review_identity_sha256: string
  validation_seal_sha256: string
  review_token: string
  artifact_subject_sha256: string
  workpack_sha256: string
  disposition: 'review_required' | 'already_current'
}

export interface ApprovalEvidenceRef {
  artifact: string
  subject_sha256: string
  start: number | null
  end: number | null
}

export interface CompactApprovalCheck {
  rules_scanned: number
  counts: { ok: number; hit: number; unver: number; skipped: number }
  provenance: {
    freshness: string
    reason: string
    ghosts: string[]
    edges: unknown[]
    invalid: boolean
    revalidated: boolean | null
    origin: string | null
  }
  attention: RuleLine[]
  metrics: RuleLine[]
}


export interface Stage6WorkpackCandidate {
  index: number
  slot: string
  approach: string
  chars: number
  text: string
  subject_sha256: string
  mech: { punct_fixed: number; quotes_fixed: number; paras_fixed: number }
  findings: Finding[]
}

export interface Stage6ApprovalWorkpack extends FinalApprovalWorkpack<4> {
  kind: 'stage6'
  check_basis: 'candidate_without_approval'
  rationale: string
  materials: {
    prepare_subject_sha256: string
    source_hook: string
    source_blurb: import('./stages').SourceBlurbMaterials
    rewritten_chapters: { chapter_ordinal: number; text: string }[]
  }
  check: CompactApprovalCheck
  candidates: Stage6WorkpackCandidate[]
  current_slot: string | null
  current_approval: {
    actor: string
    reason: string
    subject_sha256: string
    basis?: 'default_policy' | 'human_selection'
    workpack_sha256: string | null
    review_identity_sha256: string | null
  } | null
}

export interface Stage7ApprovalWorkpack extends FinalApprovalWorkpack {
  kind: 'stage7'
  check_basis: 'name_map_without_approval_or_delivery'
  decision_subject_sha256: string
  check: CompactApprovalCheck
  surname_map: Record<string, string>
  characters: {
    char_id: string
    old_canonical: string
    new_canonical: string
    renamed: boolean
    not_renamed_reason: string | null
    reason: string
    aliases: { old: string; new: string }[]
    hits: number
  }[]
  extra_subs: { old: string; new: string; origin: string; note?: string | null }[]
  subs_count: number
  kept_count: number
  kept: string[]
  replacement_hits: Record<string, number>
  residual: {
    stem: string
    where: string
    hit: string
    context: string
    confidence: string
    unit: string | null
    suggest: string | null
  }[]
  baselines: Record<string, { total: number; covered: number; residual: number; ratio: number | null }>
  book: Finding[]
  identity: {
    roundtrip_ok: boolean
    chapters: number
    mismatches: string[]
    blind: string[]
  } | null
  current_approval: {
    actor: string
    reason: string
    subject_sha256: string
    basis?: 'default_policy' | 'human_selection'
    workpack_sha256: string | null
    review_identity_sha256: string | null
  } | null
}

/** Stage5 写作授权的 typed 投影；`writing` 仍是服务端给出的最终可执行结论。 */
export interface WritingApprovalRequest {
  actor: string
  reason: string
  review_token: string
}

export interface WritingApprovalDecision extends WritingApprovalRequest {
  decided_at: string
}

export interface WritingApprovalView {
  available: boolean
  current_subject_sha256: string | null
  /** 新服务端必给；旧只读快照缺失时不得发授权。 */
  review_token?: string | null
  current_decision: WritingApprovalDecision | null
  history: WritingApprovalDecision[]
}

/** 一条 current trim audit 的批量提交身份；两枚 SHA 都由服务端下发。 */
export interface BatchTrimResolutionItem {
  output_ordinal: number
  audit_sha256: string
  chapter_sha256: string
  /** 空串表示沿用请求级共享 reason。 */
  reason: string
}

export interface BatchTrimResolutionRequest {
  actor: string
  reason: string
  items: BatchTrimResolutionItem[]
}

export interface Stage5MustAppearDecision {
  chapter_ordinal: number
  evidence_key: string
  finding_sha256: string
  chapter_sha256: string
  /** 取值面来自 `/api/enums.stage5_must_appear_rulings`。 */
  ruling: string
  reason: string
  placeholder: boolean
  template_token: string | null
}

export interface Stage5FindingGroupAck {
  /** 取值面来自 `/api/enums.stage5_closure_groups`。 */
  group: string
  findings_sha256: string
  items: number
  placeholder: boolean
  template_token: string | null
}

export interface Stage5ClosureMaterialized {
  actor: string
  reason: string
  workpack_sha256: string
  review_sha256: string
  must_appear: Stage5MustAppearDecision[]
  group_acks: Stage5FindingGroupAck[]
}

export interface Stage5MustAppearRulingInput {
  chapter_ordinal: number
  evidence_key: string
  ruling: string
  reason: string
}

export interface Stage5FindingGroupRuling {
  group: string
  acknowledged: true
}

export interface Stage5ClosureRequest {
  actor: string
  reason: string
  review_token: string
  must_appear: Stage5MustAppearRulingInput[]
  group_acks: Stage5FindingGroupRuling[]
}

/** GET 返回完整只读材料；POST 只投影 token 与 typed rulings。 */
export interface Stage5ClosureTemplate extends Stage5ClosureMaterialized {
  review_token: string
}

export interface Stage5ClosureDecision extends Stage5ClosureRequest {
  decision_sha256: string
  decided_at: string
}

export interface Stage5MustAppearRow {
  chapter_ordinal: number
  evidence_key: string
  detail: string
  finding_sha256: string
  chapter_sha256: string
}

export interface Stage5FindingGroup {
  group: string
  findings_sha256: string
  items: number
  rules: string[]
}

export interface Stage5ClosureWorkpack {
  review_seq: number
  review_rel: string
  review_sha256: string
  planned_chapters: number[]
  missing_chapters: number[]
  /** 全部 current canonical 计划章的服务端文件身份；预览/apply 不从正文重算。 */
  chapter_subjects: { output_ordinal: number; chapter_sha256: string }[]
  invalid_chapters: number[]
  hard_findings: string[]
  unresolved_trim_chapters: number[]
  /** 批量 trim 的唯一合法 identity 来源；前端不得重算 audit/chapter SHA。 */
  unresolved_trim_items: Omit<BatchTrimResolutionItem, 'reason'>[]
  /** 全部 current audit 与可选当前 resolution 的完整字节身份。 */
  trim_subjects: (Omit<BatchTrimResolutionItem, 'reason'> & {
    resolution_sha256: string | null
  })[]
  /** 当前 wontfix 完整文件身份；缺文件与空账分开。 */
  wontfix_sha256: string | null
  must_appear: Stage5MustAppearRow[]
  fact_group: Stage5FindingGroup | null
  unver_group: Stage5FindingGroup | null
  disclosure: Record<string, number>
  resolved: Record<string, number>
  workpack_sha256: string
  review_token: string
}

export interface Stage5ClosureView {
  /** 取值面来自 `/api/enums.stage5_closure_states`；页面只展示，不从 counts 重判。 */
  state: string
  workpack: Stage5ClosureWorkpack | null
  next_step: string | null
  current_decision: Stage5ClosureDecision | null
  history: Stage5ClosureDecision[]
}

export interface ApprovalDoc {
  v: 9
  sid: string
  gate: Record<GateKey, boolean>
  prepare: import('./prepare').PrepareState
  /** 是否已经批准全书闭卷开写。 */
  writing: boolean
  /** Stage4A 候选的历史审阅投影（只读；写面已随四道人工闸退役）。 */
  design_review: DesignReviewView
  /** 当前完整 Stage4 计划的历史结构裁决投影（只读）。 */
  structure: StructureApprovalView
  /** 当前写作主体与 typed 授权历史；旧匿名记录不会被补造成人工决定。 */
  writing_approval: WritingApprovalView
  /** Stage5 唯一、强制、hash-bound 的整书 Commander 闭合回执。 */
  closure: Stage5ClosureView
  /**
   * 确认某一道闸门之后服务端会发起什么。
   * 由服务端下发，界面据此决定按钮是不是橙的 —— 免得「谁会花钱」在前后端各存一份。
   */
  spawns: Partial<Record<GateKey, ActionRef[]>>
}

export interface ApprovalBackfillResult {
  approvals: ApprovalDoc
  /** 这些章来自盘面补录，不伪装成用户逐屏点击。 */
  backfilled: GateKey[]
}
