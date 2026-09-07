import type { Finding, RuleLine } from './common'

/**
 * 人工验收记录。
 *
 * 在真产品里这是服务端的东西（人点了哪些确认、是否已经批准开写）。
 * mock 版本里它由 mock.ts 存在 localStorage —— 但**取数路径和真实响应完全一样**：
 * 走 api.getApprovals() / api.approveGate() / api.approveDesignReview() /
 * api.approveStructure()，
 * 屏与组件从不直接碰传输层。
 *
 * 这些字段合成一份文档而不是各存一张 map：若 gate.s2 为真而 seen 丢了，
 * 闸门会画出「✓ 已确认」旁边挂两个灰 chip 的自相矛盾状态。
 * 一份文档、一次写、一个生命周期。
 */

export type GateKey = 's0' | 's1' | 's2' | 's4' | 's5' | 's6' | 's7'

export const GATE_KEYS: GateKey[] = ['s0', 's1', 's2', 's4', 's5', 's6', 's7']

/** stage2 有两屏，两屏都要看过才能确认 */
export type SeenKey = 'read' | 'global'

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

export interface DesignGoalSubject {
  intent: string
  fact_aliases: string[]
}

export interface DesignSceneSubject {
  candidate_scene_id: string
  weight: string
  purpose: string
  role: string
  delivery: string
  transition: string
  land_fact_aliases: string[]
  context_fact_aliases: string[]
  turn_fact_alias: string
  entry_state_aliases: string[]
  exit_state_aliases: string[]
}

export interface DesignChapterCandidate {
  output_ordinal: number
  structure_decision: {
    mode: string
    reason: string
    declared_change_kinds: string[]
  }
  chapter_goal: DesignGoalSubject
  open_hook_goal: DesignGoalSubject
  end_hook_goal: DesignGoalSubject
  weight: string
  chapter_rewrite_brief: string
  scenes: DesignSceneSubject[]
}

export interface DesignBlindFact {
  fact_alias: string
  claim: string
  kind: string
  obligation: string
  participant_aliases: string[]
}

/** 后端当前章 typed 关系；端点按 kind 原样保留，页面不生成关系。 */
export interface DesignNarrativeRelation {
  kind: string
  enforcement: string
  before_alias?: string
  after_alias?: string
  stimulus_alias?: string
  response_alias?: string
  left_alias?: string
  right_alias?: string
  observer_alias?: string
  observed_alias?: string
  observation_alias?: string
}

export interface DesignBlindChapterTruth {
  chapter_ordinal: number
  chapter_function: string
  facts: DesignBlindFact[]
  causal_edges: { before_alias: string; after_alias: string }[]
  /** 历史材料可能缺席；缺席没有关系修订选择权。 */
  narrative_relations?: DesignNarrativeRelation[]
  entry_state_aliases: string[]
  ending_state_aliases: string[]
}

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
  effect?: unknown | null
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

export interface DesignConstraintCatalog {
  expected_constraint_set_sha256: string
  items: {
    constraint_sha256: string
    constraint: DesignRepairConstraint
    source_decision_sha256s: string[]
  }[]
}

export interface DesignStrategyReplanConstraint {
  kind: string
  subject_sha256: string
  chapter_ordinals: number[]
}

export interface DesignStrategyReplanRuling {
  kind: string
  chapter_ordinals: number[]
}

export interface DesignStrategyReview {
  subject: DesignStrategySubject
  subject_sha256: string
  /** true = 服务端关闭式模板尚未被审阅者逐项确认；正式决定必须为 false。 */
  placeholder: boolean
  /** GET 模板身份；显式确认后必须清空，正式决定永远为 null。 */
  template_token: string | null
  decision: string
  reason: string
  replan_constraints: DesignStrategyReplanConstraint[]
  /** 复用旧 accept 时由服务端填；前端原样回传，不自行寻找历史决定。 */
  source_decision_sha256: string | null
}

export interface DesignChapterReview {
  output_ordinal: number
  subject: DesignChapterSubject
  subject_sha256: string
  /** true = 服务端关闭式模板尚未被审阅者逐章确认；正式决定必须为 false。 */
  placeholder: boolean
  /** GET 模板身份；显式确认后必须清空，正式决定永远为 null。 */
  template_token: string | null
  decision: string
  reason: string
  constraints: DesignRepairConstraint[]
  constraint_operations?: DesignConstraintOperations
  /** 复用旧 accept 时由服务端填；前端原样回传。 */
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
  /** 服务端 A→formal 零调用预检结论；前端只呈现并原样回传。 */
  preflight: DesignPreflightSummary | null
  /** 当前候选的定向返修执行面；完整回传，不由前端按轮数重算。 */
  repair_capability: DesignReviewRepairCapability
  strategy: DesignStrategyReview
  chapters: DesignChapterReview[]
}

export interface DesignStrategyRuling {
  decision: string
  reason: string
  replan_constraints: DesignStrategyReplanRuling[]
}

export interface DesignChapterRuling {
  output_ordinal: number
  decision: string
  reason: string
  constraints: DesignRepairConstraint[]
  constraint_operations?: DesignConstraintOperations
}

/** POST 只搬一枚 opaque token 与 typed 裁决；subject 由服务端现场重建。 */
export interface DesignReviewRequest {
  actor: string
  reviewer_kind: string
  review_run_id: string
  general_reason: string
  review_token: string
  strategy: DesignStrategyRuling
  chapters: DesignChapterRuling[]
}

/** GET 返回完整只读材料；它不是可原样 POST 的写入载荷。 */
export interface DesignReviewTemplate extends DesignReviewMaterialized {
  review_token: string
  constraint_catalog?: Record<string, DesignConstraintCatalog>
}

/** GET template 的四个必填审阅会话参数；reason 会投影为响应 general_reason。 */
export interface DesignReviewTemplateQuery {
  actor: string
  reviewer_kind: string
  review_run_id: string
  reason: string
}

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
  /** 新服务端必给；可选只为读取旧前端缓存/fixture，动作仍关闭式要求真 token。 */
  action_review_token?: string | null
  /** 当前、fresh 且由代码铸造的 Stage4B 不可行证据；其余状态一律为 null。 */
  binding_infeasibility: DesignBindingInfeasibility | null
  /** 当前候选的 A→formal 生产预检；不可用态为 null。 */
  preflight: DesignPreflightSummary | null
  /** unavailable 时为 null；其余状态是服务端当前候选的唯一返修能力结论。 */
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

export interface DesignChapterRepairCapability {
  output_ordinal: number
  committed_rounds: number
  /** 服务端给出的唯一可执行性结论；前端不得从轮次数值重算。 */
  request_repair_available: boolean
}

export interface DesignReviewRepairCapability {
  repairable: boolean
  reason_code: string
  chapters: DesignChapterRepairCapability[]
}

/**
 * Stage4 结构审批的取值面来自 `/api/enums.structure_approval_decisions`。
 * 这里刻意用 string，不在 TypeScript 再抄一份服务端枚举。
 */
export interface StructureChapterApproval {
  output_ordinal: number
  decision: string
  reason: string
}

export interface StructureApprovalRequest {
  actor: string
  reason: string
  review_token: string
  chapters: StructureChapterApproval[]
}

export interface StructureApprovalDecision extends StructureApprovalRequest {
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
  /** 新服务端必给；旧只读快照缺失时按 unavailable 处理。 */
  review_token?: string | null
  current_decision: StructureApprovalDecision | null
  history: StructureApprovalDecision[]
  /** Stage4B 下发的非阻断关注项；只展示给人审，不由前端改判。 */
  binding_concerns: StructureBindingConcern[]
}

/** Stage6/7 终审包公共绑定面；屏只提交 opaque review token。 */
export interface FinalApprovalWorkpack {
  v: 3
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

export interface Stage6HookQualityScores {
  read_on_desire: number
  body_voice_match: number
  concrete_scene_and_specificity: number
  curiosity_and_payoff_promise: number
  hook_craft: number
  synopsis_penalty: number
}

export interface Stage6HookContradiction {
  assertion: string
  evidence: string
  reason: string
}

export interface Stage6HookCandidateReview {
  label: string
  coverage: 'complete' | 'uncertain'
  uncertainty_reason: string | null
  contradictions: Stage6HookContradiction[]
  quality: Stage6HookQualityScores
  concise_reason: string
  text_sha1: string
}

export interface Stage6SemanticDecision {
  ability: string
  subject_key: string
  verdict: 'UNVER'
  reason: string
  evidence: ApprovalEvidenceRef[]
  judges: {
    judge: string
    verdict: 'PASS' | 'FAIL' | 'UNVER'
    reason: string
    evidence: ApprovalEvidenceRef[]
  }[]
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
  semantic_state: string
  semantic_reason: string
  semantic_review_seq: number | null
  semantic_review: Stage6HookCandidateReview | null
  semantic_decision: Stage6SemanticDecision | null
  quality_score: number | null
  quality_rank: number | null
}

export interface Stage6ApprovalWorkpack extends FinalApprovalWorkpack {
  kind: 'stage6'
  rationale: string
  check: CompactApprovalCheck
  judge_rankings: Record<'A' | 'B', string[]> | null
  candidates: Stage6WorkpackCandidate[]
  current_slot: string | null
  current_approval: {
    actor: string
    reason: string
    subject_sha256: string
    workpack_sha256: string | null
    review_identity_sha256: string | null
  } | null
}

export interface Stage7ApprovalWorkpack extends FinalApprovalWorkpack {
  kind: 'stage7'
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
  v: 6
  sid: string
  gate: Record<GateKey, boolean>
  seen: Record<SeenKey, boolean>
  /** 是否已经批准全书闭卷开写。 */
  writing: boolean
  /** Stage4A 候选的正式 hash-bound 审阅；accepted 也不会打开最终 s4 闸。 */
  design_review: DesignReviewView
  /** 独立于 writing 的、绑定当前完整 Stage4 计划字节的逐章结构裁决。 */
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

/** Commander 审阅工作包固定的异常优先分组；顺序由服务端响应保证。 */
export type ApprovalWorkpackGroupKind =
  | 'blocking'
  | 'semantic_attention'
  | 'disclosure'
  | 'metric_only'
  | 'phase_deferred'
  | 'coverage'

/** 条目对当前审批的影响只照画服务端结论，不由前端按 rule/severity 重算。 */
export type ApprovalWorkpackEffect =
  | 'block_b'
  | 'block_context'
  | 'attention'
  | 'disclose'
  | 'read_only'
  | 'phase_deferred'
  | 'coverage'

export type ApprovalWorkpackSeverity = 'hard' | 'info' | 'metric' | 'unver'
export type ApprovalWorkpackStatus = 'ok' | 'hit' | 'unver' | 'skipped'
export type ApprovalWorkpackAction = 'accept' | 'request_repair' | 'request_replan'

export interface ApprovalWorkpackItem {
  code: string
  rule: string | null
  sev?: ApprovalWorkpackSeverity | null
  status?: ApprovalWorkpackStatus | null
  chapter_ordinal: number | null
  scene_refs: string[]
  refs: string[]
  repair_owner: string | null
  detail: string
  effect: ApprovalWorkpackEffect
  /** typed 入口名，只展示；空数组明确表示服务端没有给可执行动作。 */
  allowed_actions: ApprovalWorkpackAction[]
  changed: boolean
  historical_accept_reusable: boolean
  source_decision_sha256?: string | null
  /** Stage4A 完整章 edit 的单一 TOCTOU 票；只在当前 repair 决定行出现。 */
  review_token?: string | null
  /** v2：coverage 行的逐章裁决材料，服务端确定性渲染；页面只折叠呈现，不自算。 */
  reading?: WorkpackChapterReading | null
}

/** 本章 facts 表的一行：claim 只在这里出现一次，其余处只引用 ref。 */
export interface WorkpackFactReading {
  ref: string
  claim: string
  kind: string | null
  obligation: string | null
}

export interface WorkpackStageReading {
  ref: string
  found: boolean
  time_layer: string | null
  place: string | null
  present: string[]
}

export interface WorkpackMaterialReading {
  group: string
  count: number
  chars: number
  excerpt: string
}

export interface WorkpackGoalReading {
  intent: string
  refs: string[]
}

export interface WorkpackSceneReading {
  scene_ref: string
  weight: string | null
  budget_chars: number | null
  purpose: string
  role: string
  delivery: string
  transition: string
  land: string[]
  context: string[]
  turn: string | null
  entry_state: string[]
  exit_state: string[]
  stages: WorkpackStageReading[]
  materials: WorkpackMaterialReading[]
  missing_material_refs: string[]
}

export interface WorkpackClosedRefReading {
  ref: string
  kind: 'fact' | 'beat' | 'hinge' | 'unknown'
  text: string | null
}

export interface WorkpackConcernReading {
  constraint_code: string
  chapter_ordinal: number | null
  scenes: string[]
  reason: string
  closed_refs: WorkpackClosedRefReading[]
}

export interface WorkpackCausalEdgeReading {
  before_alias: string
  after_alias: string
}

export interface StructureChapterReading {
  kind: 'stage4_structure_chapter_reading_v1'
  chapter_function: string
  chapter_rewrite_brief: string
  structure_decision: Record<string, unknown>
  facts: WorkpackFactReading[]
  goals: Record<string, WorkpackGoalReading>
  scenes: WorkpackSceneReading[]
  structure_metrics: Record<string, unknown>
  binding_concerns: WorkpackConcernReading[]
  signature_conflict_codes: string[]
}

export interface DesignBlindFactReading {
  fact_alias: string
  claim: string
  kind: string
  obligation: string
  participant_aliases: string[]
}

export interface DesignChapterReading {
  kind: 'stage4_design_chapter_reading_v1'
  chapter_function: string
  weight: string
  chapter_rewrite_brief: string
  structure_decision: Record<string, unknown>
  facts: DesignBlindFactReading[]
  goals: Record<string, WorkpackGoalReading>
  causal_edges: WorkpackCausalEdgeReading[]
  narrative_relations: Array<Record<string, unknown>>
  entry_state: string[]
  ending_state: string[]
  scenes: WorkpackSceneReading[]
}

export type WorkpackChapterReading = DesignChapterReading | StructureChapterReading

/** 工作包顶层只出现一次的字段说明与整书材料。 */
export interface WorkpackReading {
  kind: 'stage4_workpack_reading_v1'
  legend: Record<string, string>
  reconstruction_strategy: string | null
  chapter_weights: Array<Record<string, unknown>> | null
  global_binding_concerns: WorkpackConcernReading[]
}

export interface ApprovalWorkpackGroup {
  group: ApprovalWorkpackGroupKind
  scanned: string
  items: ApprovalWorkpackItem[]
}

/** Stage4 A/B 共用的只读、hash-bound 审阅工作包 façade v2（coverage 行携 reading）。 */
export interface ApprovalWorkpack {
  v: 2
  sid: string
  kind: 'design' | 'structure'
  candidate_sha256?: string | null
  subject_sha256: string
  review_token: string
  chapter_ordinals: number[]
  groups: ApprovalWorkpackGroup[]
  disposition: 'review_required' | 'already_current' | 'suspended'
  current_decision_sha256: string | null
  reading?: WorkpackReading | null
}
