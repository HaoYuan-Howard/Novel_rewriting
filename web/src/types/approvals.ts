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

export interface DesignBlindChapterTruth {
  chapter_ordinal: number
  chapter_function: string
  facts: DesignBlindFact[]
  causal_edges: { before_alias: string; after_alias: string }[]
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
  /** 复用旧 accept 时由服务端填；前端原样回传。 */
  source_decision_sha256: string | null
}

export interface DesignReviewRequest {
  actor: string
  reviewer_kind: string
  review_run_id: string
  general_reason: string
  candidate_sha256: string
  aggregate_subject_sha256: string
  /** 服务端只在当前 fresh B=infeasible 时给出；前端不得据候选或 concern 自行构造。 */
  binding_infeasibility: DesignBindingInfeasibility | null
  /** 当前候选的定向返修执行面；完整回传，不由前端按轮数重算。 */
  repair_capability: DesignReviewRepairCapability
  strategy: DesignStrategyReview
  chapters: DesignChapterReview[]
}

/** GET 返回的关闭式模板；形状与 POST 相同，但仍含待确认 placeholder/token。 */
export interface DesignReviewTemplate extends DesignReviewRequest {}

/** GET template 的四个必填审阅会话参数；reason 会投影为响应 general_reason。 */
export interface DesignReviewTemplateQuery {
  actor: string
  reviewer_kind: string
  review_run_id: string
  reason: string
}

export interface DesignReviewDecision extends DesignReviewRequest {
  decision_sha256: string
  decided_at: string
}

export interface DesignReviewView {
  /** 服务端状态是 current / expired / 可进入 B 的唯一结论，前端不复算 hash 或逐章决定。 */
  state: string
  candidate_sha256: string | null
  aggregate_subject_sha256: string | null
  current_decision: DesignReviewDecision | null
  history: DesignReviewDecision[]
  /** 当前、fresh 且由代码铸造的 Stage4B 不可行证据；其余状态一律为 null。 */
  binding_infeasibility: DesignBindingInfeasibility | null
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
  subject_sha256: string
  actor: string
  reason: string
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
  current_decision: StructureApprovalDecision | null
  history: StructureApprovalDecision[]
  /** Stage4B 下发的非阻断关注项；只展示给人审，不由前端改判。 */
  binding_concerns: StructureBindingConcern[]
}

export interface ApprovalDoc {
  v: 5
  sid: string
  gate: Record<GateKey, boolean>
  seen: Record<SeenKey, boolean>
  /** 是否已经批准全书闭卷开写。 */
  writing: boolean
  /** Stage4A 候选的正式 hash-bound 审阅；accepted 也不会打开最终 s4 闸。 */
  design_review: DesignReviewView
  /** 独立于 writing 的、绑定当前完整 Stage4 计划字节的逐章结构裁决。 */
  structure: StructureApprovalView
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
