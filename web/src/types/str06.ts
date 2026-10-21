/** 当前 story-v1 三层只读监控；旧 Characters/Blueprint/Design/IR API 已退出。 */
import type { StoryArtifactStatus } from './backend.generated'

export interface StoryArtifactView<T> {
  candidate_id: string
  revision: string
  status: StoryArtifactStatus
  data: T | null
  errors: string[]
}

export interface StoryState {
  subject_ids: string[]
  dimension: string
  qualifier: string
  value: string | null
  op: 'set' | 'unset' | 'unknown'
  modality: string
}

export interface SourceStoryData {
  contract_version: 'story-v1'
  source_identity: string
  entities: Record<string, { kind: string; label: string; surfaces: { literal: string; evidence_refs: string[] }[] }>
  events: Record<string, { chapter_ordinal: number; order: number; summary: string; participants: string[]; scene_boundary: string; temporal: { frame: string; anchor_event: string | null }; evidence_refs: string[] }>
  propositions: Record<string, { statement: string; entity_refs: string[]; world_status: string; evidence_refs: string[] }>
  knowledge_updates: { update_id: string; holder: string; proposition_ref: string; mode: string; polarity: string; at: string; supersedes: string[]; evidence_refs: string[] }[]
  exposures: { event_ref: string; proposition_ref: string; mode: string; evidence_refs: string[] }[]
  state_slots: Record<string, { subjects: string[]; aspect: string; focus: string }>
  state_updates: { slot_ref: string; op: string; value: string | null; at: string; effective_time: string; modality: string; persistence: string }[]
  causal_links: { from_event: string; to_event: string; kind: string; support: string; evidence_refs: string[] }[]
  threads: Record<string, { question: string; updates: { event_ref: string; op: string; resolution: string | null }[] }>
  world_rules: Record<string, { kind: string; statement: string; evidence_refs: string[] }>
  expression: { person: string; focalizers: string[]; statement: string; mechanisms: string[] }
  conflicts: { kind: string; statement: string; status: string; source_refs: string[] }[]
  evidence: Record<string, { chapter_ordinal: number; paragraph_ordinal: number; text: string }>
}

export interface TargetStoryData {
  contract_version: 'story-v1'
  source_chapter_count: number
  total_length_budget: number
  premise: string
  narration: string
  style: string
  character_voices: Record<string, string>
  world_rules: { rule_id: string; statement: string }[]
  propositions: Record<string, { statement: string; status: string }>
  initial_state: StoryState[]
  initial_knowledge: { holder: string; proposition_ref: string; mode: string }[]
  initial_displays: Record<string, string>
  names: Record<string, { entity_id: string; primary: string; surfaces: Record<string, string> }>
  character_arcs: { entity_ref: string; starting_position: string; pressure: string; choice: string; event_refs: string[]; end_position: string }[]
  chapters: { chapter_ordinal: number; purpose: string; event_refs: string[]; entry: string; exit: string; length_budget: number }[]
  events: { event_id: string; summary: string; trigger: string; choice: string; result: string; temporal_frame: string; requires: string[]; after: string[] }[]
}

export interface ChapterPacketData {
  contract_version: 'story-v1'
  chapter_ordinal: number
  source_chapter_count: number
  length_budget: number
  min_chars: number
  max_chars: number
  participants: string[]
  names: Record<string, string>
  entry_state: string[]
  exit_state: string[]
  actions: string[]
  must_realize: string[]
  must_respect: string[]
  continuity: string[]
  creative_guidance: string[]
  background: string[]
  guard_only: string[]
}

export type HookPacketData = Omit<ChapterPacketData,
  'chapter_ordinal' | 'source_chapter_count' | 'participants' | 'entry_state' | 'exit_state' | 'actions'>

export interface StoryProseReceipt {
  contract_version: 'story-v1'
  kind: 'body' | 'hook'
  chapter_ordinal: number | null
  packet_sha256: string
  prior_tail_sha256: string
  original_prior_tail_sha256: string
  body_sha256: string
  original_body_sha256: string
  request_id: string
  response_id: string
  provider: string
  model: string
  system_sha256: string
  user_sha256: string
  finish_reason: string
  mechanical: string
  semantic: 'unver'
  patches: { request_id: string; response_id: string; base_body_sha256: string; result_body_sha256: string; changed_handles: string[] }[]
}

export interface StoryChapterData {
  chapter_ordinal: number
  packet: ChapterPacketData | null
  body: string | null
  receipt: StoryProseReceipt | null
}

export interface StoryHookData {
  packet: HookPacketData | null
  body: string | null
  receipt: StoryProseReceipt | null
}

export type Str06SourceStoryView = StoryArtifactView<SourceStoryData>
export type Str06TargetStoryView = StoryArtifactView<TargetStoryData>
export type Str06ChapterView = StoryArtifactView<StoryChapterData>
export type Str06HookView = StoryArtifactView<StoryHookData>
export interface Str06DeliveryChapter {
  chapter_ordinal: number
  prose_sha256: string | null
  length: Record<string, number> | null
  length_disclosed: boolean
}

export interface Str06DeliveryView {
  present: boolean
  delivery_identity: string | null
  final_text_sha256: string | null
  chapters: Str06DeliveryChapter[]
  length_reading: Record<string, number> | null
  /** 后端当前交付读数；空值保持未提供，不由界面补造。 */
  name_residue: Record<string, Record<string, number>> | null
  disclosure: string | null
}

export interface Str06Node {
  node: string
  stage: string
  owner: string
  paid: boolean
  produced: boolean
}

export interface Str06LedgerNodeCost {
  node: string
  rows: number
  new_paid_rows: number
  cached_rows: number
  prompt_tokens: number | null
  completion_tokens: number | null
  reasoning_tokens: number | null
}

export interface Str06LedgerCost {
  present: boolean
  rows: number
  new_paid_rows: number
  cached_rows: number
  prompt_tokens: number | null
  completion_tokens: number | null
  reasoning_tokens: number | null
  unknown_cost_rows: number
  per_node: Str06LedgerNodeCost[]
}

export interface Str06Epoch {
  epoch_id: string
  ordinal: number
  reason: string
  session_run_id: string | null
}

/** 章级局部失败留证（B4）：章序 → 类别与详情直投影，item 是章序。 */
export interface Str06BodyFailureChapter {
  chapter_ordinal: number
  failure_kind: string
  detail: string
}

/**
 * 逐章失败留证（D3/W2）：「最近一次完成执行」语义，章观察与正文共用同一形状。
 * requested＝本次请求的章范围；attempted＝实际进入逐章执行器的章（正文不含
 * 已提交章）；chapters 是 attempted 的失败子集；recorded_at 是记录时间。
 * 缺席（null）＝该节点还没有一次完成执行——与合法空清单是两件事（I-42）。
 */
export interface Str06ChapterFailuresView {
  present: boolean
  requested: number[]
  attempted: number[]
  chapters: Str06BodyFailureChapter[]
  recorded_at: number | null
}

/** 章就绪五态（I-42）：unknown 不作零；stale（回执在、绑定不成立）不是 missing。 */
export interface Str06BodyAccess {
  accepted: number[]
  missing: number[]
  rejected: number[]
  unknown: number[]
  stale: number[]
}

export interface Str06AssemblyOutcome {
  status: string | null
  reason: string | null
  delivery_identity: string | null
}

/** runtime/delivery/assembly_report.json 的只读投影（Stage7 集合核对）。 */
export interface Str06AssemblyReportView {
  present: boolean
  accepted_body: boolean | null
  body_access: Str06BodyAccess | null
  chain_problems: string[]
  assembly: Str06AssemblyOutcome | null
}

/** 一条 typed 机械问题（R3 自愈）：code／item／message 与路由 context 直投影。 */
export interface Str06OwnerIssueItem {
  code: string
  item: string
  message: string
  context: Record<string, unknown>
}

/**
 * 一个 owner 的跨节点回派账（runtime/issues/<owner>.json，R3）。
 * rounds_total 是终身已发起的修订轮数（resolve 不复位）；status 取值面归后端
 * 产物契约，前端不抄第二份字面量。
 */
export interface Str06OwnerIssueRecord {
  owner: string
  node: string
  raised_by: string
  status: string
  rounds_total: number
  fingerprint: string
  issues: Str06OwnerIssueItem[]
  updated_at: number
}

/**
 * 修订轮的只读投影（R3）：open 与 resolved 都在（resolved 是历史留账）。
 * rounds_cap 是每 owner 终身修订轮上限的后端投影，前端不抄契约常量；
 * 缺席（null）＝ 从未起过修订轮。
 */
export interface Str06OwnerIssuesView {
  present: boolean
  rounds_cap: number
  records: Str06OwnerIssueRecord[]
}

export interface Str06RunView {
  contract_version: 'story-v1'
  revision: string
  candidate: string
  present: boolean
  status: string | null
  failed_node: string | null
  accepted_body: boolean | null
  /** 失败形状直投影（workflow_status.json）：任何失败带 kind/detail；ExecutionError 另带 error_* 六字段。 */
  failure_kind: string | null
  detail: string | null
  error_code: string | null
  error_stage: string | null
  error_node: string | null
  error_item: string | null
  error_phase: string | null
  error_context: Record<string, unknown> | null
  body_failures: Str06ChapterFailuresView | null
  /** 章观察的逐章失败留证（D3/W2）：与正文同形、互不冒充。 */
  chapter_observation_failures: Str06ChapterFailuresView | null
  assembly_report: Str06AssemblyReportView | null
  /** 修订轮（R3）：runtime/issues/ 的回派账；停止原因在失败形状里，两面并排。 */
  owner_issues: Str06OwnerIssuesView | null
  epoch: Str06Epoch | null
  nodes: Str06Node[]
  cost: Str06LedgerCost | null
}

export interface Str06CandidateSummary {
  candidate: string
  present: boolean
  status: string | null
  updated_at: number | null
}

export interface Str06CandidateList {
  candidates: Str06CandidateSummary[]
}
