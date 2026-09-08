/**
 * stage0 – stage7 的响应形状。字段名照抄后端契约（`contracts/schema/api.py`），
 * 抓包证据在 `src/fixtures/backend_v1/`。
 */
import type { Badge, Freshness, Severity, InboxKind } from '@/constants/vocabulary'
import type {
  Facts,
  Finding,
  NameRef,
  PlanNarration,
  Provenance,
  RuleLine,
  Sources,
} from './common'
import type { Job } from './jobs'
import type { CompactApprovalCheck } from './approvals'

/* ---------------------------------------------------------------- stage0 */

/**
 * 一条可疑项 = 产物里的**结构化证据** + 服务端现渲染的那句话。
 *
 * ⚠ `message` 只活在 HTTP 这一层（产物 2026-08-22 起只记证据）。**前端不许自己拼
 * 措辞**：那会变成同一句话的第二个落点，与 `novel/core/warnings.py` 必然分家，
 * 而分家之后界面上那句话按的是哪版阈值谁都说不清。
 * ⚠ 认告警仍然一律用 `warning.kind`，不许拿 `message` 做分支。
 */
export interface Stage0Warning {
  warning: { kind: string; [evidence: string]: unknown }
  message: string
}

export interface Stage0Report {
  original_filename: string
  total_code_points: number
  warnings: Stage0Warning[]
  /**
   * 当前 Stage0 此刻能不能放行 stage1 与付费链。**后端唯一落点的投影，前端不重算。**
   * ⚠ 别再从 `approval` 自己推：后端谓词收紧时改一处就够，两处各写一遍迟早漏一处，
   *   而漏掉的那处就是绕过闸门的复活口。
   * ⚠ 配套的 `block_reason` 已于 2026-08-22 删除：理由码只剩一个取值之后，
   *   它与本格是同一个 bit，而一个 bit 只该有一个投影。
   */
  executable: boolean
  /**
   * 当前这一版的内容身份，**同时是编辑/裁决路的乐观锁**。
   * ⚠ 它替掉了 `round`：revision id 由内容与 parent 一起派生，
   * 「基于哪一版」与「那一版长什么样」它一格就钉完了。
   */
  revision_id: string
  /**
   * 这一版怎么来的。⚠ 它替掉了 `edited_by_human`——那一格是
   * `origin === 'human_edit'` 的同义反复，而且说不出「人**采纳了机器新结果**」
   * 与「人自己改的」之间的差别，两者的正文含义完全相反。
   */
  revision_origin: Stage0RevisionOrigin
}

export type Stage0RevisionOrigin = 'machine' | 'human_edit' | 'human_adopt'

export type Stage0SegmentKind = 'keep' | 'remove' | 'end_marker'

export interface Stage0Segment {
  id: string
  kind: Stage0SegmentKind
  start: number
  end: number
  chars: number
  flagged: boolean
  text: string
  origin: 'machine' | 'human' | null
  removed_kind: string | null
  marker_line: string | null
}

export interface Stage0Segments {
  available: boolean
  reason: string | null
  segments: Stage0Segment[]
}

/** 批准前分章预览里逐章原始章号的异常分类（`contracts/invariants.py::CHAPTER_LABEL_ANOMALY`）。 */
export type ChapterLabelAnomaly = 'reset' | 'gap' | 'repeat'

export interface Stage0EditRequest {
  /** 乐观锁：页面看到的那一版（阶段 C 起取代 round + clean_sha1 两格） */
  expected_revision_id: string
  removed_segment_ids: string[]
  actor: string
  reason: string
}

export interface Stage0EditResult {
  job: Job
  /** 本次 edit 计划实际落成的版本；作业未落盘时为 null。 */
  revision_id: string | null
}

export interface Stage0ApprovalRequest {
  /** 乐观锁：必须等于本次实际审阅的 current revision。 */
  expected_revision_id: string
  actor: string
  reason: string
}

export interface TrimResolutionRequest {
  /** Self-reported audit label; not an authenticated identity. */
  actor: string
  reason: string
}

export interface TrimResolution {
  output_ordinal: number
  action: 'retain_pretrim'
  actor: string
  reason: string
  audit_sha256: string
  before_sha256: string
  after_sha256: string
  chapter_rel: string
  chapter_sha256: string
  _provenance: Provenance
}

/**
 * 链上不可变的一版。**内容整团一起变**——聚合根不是 `clean_text` 一个字段
 * （外部评审 P0-3：只对正文做 diff3 保证不了整个聚合一致）。
 */
export interface Stage0CandidateRevision {
  revision_id: string
  origin: Stage0RevisionOrigin
  clean_text: string
  body_code_points: number
  removed_count: number
}

/**
 * 机器重跑算出的、与当前稿不同的那一版 + **当前稿的读数**。
 *
 * ⚠ 「机器重跑改了什么」由服务端给读数，前端不自己算：各算一遍就是两个会分家的
 * 落点。⚠ 阶段 C 起页面上要决定的不再是「逐个冲突选 ours 还是 theirs」
 * （diff3 与 typed 冲突已整份下线），而是**整版留哪一版**。
 */
export interface Stage0CandidateView {
  candidate: Stage0CandidateRevision
  current_revision_id: string
  current_body_code_points: number
  current_removed_count: number
  current_origin: string
}

export interface Stage0CandidateDecisionRequest {
  candidate_revision_id: string
  /** 乐观锁：页面看到的当前稿。候选是相对某一版当前稿提出的 */
  expected_revision_id: string
  actor: string
  reason: string
}

export interface S0Block {
  kind: 'hook_copy' | 'chapter'
  label: string
  chars: number
  preview: string
  /** 文案段没有 ordinal —— 是「不存在这个字段」，不是 null */
  ordinal?: number
}

export interface S0Candidate {
  id: string
  marker: string
  kind: string
  from_block: string
  chars: number
  confidence: string
  head: string
  tail: string
}

export interface Stage0Extras {
  source_id: string
  original_filename: string
  normalized_source_sha1: string
  source_encoding: string
  total_code_points: number
  scanned_at: number
  markers: string[]
  end_marker_hit: string
  blocks: S0Block[]
  /** 真实检出结果。空数组 = 没扫到标记行，**不等于**没有番外。 */
  candidates: S0Candidate[]
  note: string
  demo_candidates: S0Candidate[]
  demo_note: string
}

/* ---------------------------------------------------------------- stage1 */

export type Stage1SegmentKind = 'hook' | 'chapter' | 'end_marker' | 'hole'

export interface Stage1Segment {
  id: string
  kind: Stage1SegmentKind
  start: number
  end: number
  chars: number
  text: string
  chapter_ordinal: number | null
  source_chapter_no: number | null
  label_delta: number | null
  anomaly: ChapterLabelAnomaly | null
}

export interface Stage1View {
  segments: Stage1Segment[]
  anomaly_count: number
}

/* ----------------------------------------- 产物处境（formal / INVALID 分栏） */

/** 一份产物此刻的处境（`contracts/schema/orchestration.py::ArtifactState`）。 */
export interface ArtifactState {
  rel: string
  exists: boolean
  bytes: number | null
  mtime: number | null
  freshness: Freshness
  reason: string
  ghosts: string[]
  invalid: boolean
  invalid_freshness: Freshness | null
  invalid_reason: string | null
  readable: boolean
}

/* ------------------------------------------------- stage2 · 原文↔骨架 */

/** 该章原文上的一个连续分区。`beat_id=null` ⇒ 空隙（hole）。服务端已切好 text。 */
export interface ReadingSegment {
  start: number
  end: number
  chars: number
  /** 前端绝不按 start/end 自己切字符串（Python 码点 ≠ JS 码元） */
  text: string
  beat_id: string | null
  /** 空隙且 ≥ uncovered_min_chars 才 true；beat 分区恒 false */
  flagged: boolean
}

export interface ReadingHinge {
  kind: string
  /** null = 未定位（INVALID 状态的 hinge 没有原文可引） */
  text: string | null
}

export interface ReadingFact {
  fact_id: string
  claim: string
  kind: string
  obligation: string
  evidence_text: string | null
  participant_char_ids: string[]
}

export interface ReadingBeat {
  beat_id: string
  located: boolean
  start: number | null
  end: number | null
  time_layer: string
  place: string | null
  present_char_ids: string[]
  fact_atoms: ReadingFact[]
  hinges: ReadingHinge[]
}

export interface ReadingNarration {
  grammatical_person: string
  narrator_char_id: string | null
  focalizer_char_id: string | null
  pronoun_surfaces: string[]
}

export interface ReadingResponse {
  sid: string
  title: string
  chapter_ordinal: number
  text_chars: number
  /** false = stage2 没跑（或蓝图里没有这一章 / 读不回）。左半边仍是 stage1 的原文。 */
  blueprint: boolean
  segments: ReadingSegment[]
  beats: ReadingBeat[]
  narration: ReadingNarration[]
  /** 没被任何 beat 覆盖的字数 */
  loose: number
  uncovered_min_chars: number
  /** blueprint=false 的两种来路（没跑 / 读不回）靠它分 */
  blueprint_state: ArtifactState
  _provenance: Provenance | null
}

/** Stage2 Blueprint 只读异常优先工作包；它不是 approval，也不改变任何 gate。 */
export interface BlueprintReviewCoverageSegment {
  beat_order: number | null
  chars: number
  text: string
  flagged: boolean
}

export interface BlueprintReviewFact {
  fact_id: string
  claim: string
  kind: string
  obligation: string
  evidence_text: string | null
  participant_char_ids: string[]
}

export interface BlueprintReviewBeat {
  beat_id: string
  beat_order: number
  located: boolean
  time_layer: string
  place: string | null
  present_char_ids: string[]
  facts: BlueprintReviewFact[]
  hinges: ReadingHinge[]
}

export type BlueprintReviewNarrativeRelation =
  | {
      kind: 'story_before'
      enforcement: 'hard' | 'review'
      before_claim: string
      after_claim: string
    }
  | {
      kind: 'response_to'
      enforcement: 'hard' | 'review'
      stimulus_claim: string
      response_claim: string
    }
  | {
      kind: 'same_scene'
      enforcement: 'hard' | 'review'
      left_claim: string
      right_claim: string
    }
  | {
      kind: 'observes'
      enforcement: 'hard' | 'review'
      observer_char_id: string
      observed_claim: string
      observation_claim: string
    }

export interface BlueprintReviewChapter {
  chapter_ordinal: number
  anomalous: boolean
  attention_rules: string[]
  chapter_function: string
  coverage: {
    text_chars: number
    covered_chars: number
    loose_chars: number
    uncovered_min_chars: number
    segments: BlueprintReviewCoverageSegment[]
  }
  beats: BlueprintReviewBeat[]
  causal_links: { before_claim: string; after_claim: string }[]
  narrative_relations: BlueprintReviewNarrativeRelation[]
  entry_state_claims: string[]
  ending_state_claims: string[]
  narration: ReadingNarration[]
}

export interface BlueprintReviewGlobalChange {
  item_id: string
  subject_path: string
  change: 'added' | 'removed' | 'changed'
  changed_fields: string[]
  previous_subject_sha256: string | null
  current_subject_sha256: string | null
  affected_chapter_ordinals: number[]
  affected_references: string[]
  scope_reason: string
}

export interface BlueprintReviewDelta {
  mode: 'initial' | 'unchanged' | 'targeted' | 'full'
  baseline_review_subject_sha256: string | null
  current_review_subject_sha256: string
  changed_global_items: BlueprintReviewGlobalChange[]
  changed_blueprint_chapters: number[]
  affected_chapter_ordinals: number[]
  reusable_accepted_chapter_ordinals: number[]
  reason: string
}

export interface BlueprintReviewWorkpack {
  sid: string
  title: string
  read_only: true
  auto_decisions: false
  review_contract_version: 'stage2-blueprint-review-v5'
  review_identity_sha256: string
  validation_seal_sha256: string
  review_token: string
  global_review: Record<string, unknown>
  review_delta?: BlueprintReviewDelta | null
  subject_sha256: string
  binding_sha256: string
  binding: {
    blueprint_rel: string
    subject_kind: 'formal' | 'invalid'
    main_text_artifact_sha256: string
    blueprint_artifact_sha256: string
    check_report_sha256: string
    chapter_ordinals: number[]
  }
  check: CompactApprovalCheck
  accounting: CheckAccountingLine[]
  mechanical_fields_hidden: string[]
  /** 异常章在前；异常组和普通组内部各按章号升序。 */
  chapters: BlueprintReviewChapter[]
}

export interface BlueprintSeenRequest {
  review_token: string
}

/**
 * seen.global 的 hash-bound 阅读身份：`review_token` 由服务端从当前 Global 主体、
 * review 字节、正文主体与 blocking scope 现算；`actor` 是读完并确认的人/席位。
 * 页面不再“打开即已读”，只把服务端下发的 token 原样交回。
 */
export interface Stage2GlobalSeenRequest {
  review_token: string
  actor: string
}

/* ------------------------------------------------- stage2 · 全局设定 */

export interface GlobalSourceSpan {
  text: string
  start: number
  end: number
}

export interface GlobalFactEvidence {
  chapter_ordinal: number
  /** null 是正式的定位失败态，不等于没有 evidence 字段 */
  source: GlobalSourceSpan | null
}

export interface GlobalAddress {
  surface: string
  by: NameRef
  context: string
  from_chapter: number
}

export interface GlobalCharacter {
  char_id: string
  canonical: string
  name_distinctiveness: string
  role: string
  role_evidence: GlobalFactEvidence
  /** 全量下发；剧透遮蔽是前端的纯展示行为（R-11.13） */
  fate: string | null
  fate_evidence: GlobalFactEvidence | null
  address: GlobalAddress[]
}

export type GlobalRelationVisibility =
  | { kind: 'public'; from_chapter: number }
  | { kind: 'via_secret'; secret_id: string }

export interface GlobalRelation {
  relation_id: string
  subject: NameRef
  predicate: string
  predicate_evidence: GlobalFactEvidence
  object: NameRef
  visibility: GlobalRelationVisibility
  valid_from_chapter: number
  valid_through_chapter: number | null
}

export interface GlobalWorldRule {
  rule_id: string
  kind: string
  versions: {
    effective_from_chapter: number
    desc: string
    desc_evidence: GlobalFactEvidence
  }[]
}

export interface GlobalKnowledgeEvent {
  subject_kind: 'character' | 'group'
  subject: NameRef
  known_from_chapter: number
  evidence: GlobalFactEvidence
}

export interface GlobalSecret {
  secret_id: string
  fact: string
  fact_evidence: GlobalFactEvidence
  knowledge_events: GlobalKnowledgeEvent[]
  /** ⚠ 必须连它一起读：cold 与「foreshadowed 但章号被裁空」在 setup_chapters 上同形 */
  setup_kind: 'cold' | 'foreshadowed'
  setup_chapters: number[]
  /** 逐章剧透闸门按它开 */
  first_reveal_chapter: number
}

export interface Stage2Global {
  sid: string
  title: string
  setting: {
    时代背景: string
    era_evidence: GlobalFactEvidence
    世界观: string
    worldview_evidence: GlobalFactEvidence
  }
  characters: GlobalCharacter[]
  relations: GlobalRelation[]
  world_rules: GlobalWorldRule[]
  /** 空数组 = 这本书没抽到秘密。那**可能是漏抽了**，不是「无」。 */
  secrets: GlobalSecret[]
  arc: {
    conflict_stages: { stage: string; chapters: number[] }[]
    climax: string
    ending: string
    arc_tracks: {
      subject: NameRef
      trajectory: string
      trajectory_evidence: GlobalFactEvidence
    }[]
  }
  /** 全屏解引用失败的总数（读数，不是判定） */
  unresolved_refs: number
  invalid_exists: boolean
  freshness: Freshness
  reason: string
  ghosts: string[]
  _provenance: Provenance
}

export interface EvidenceRef {
  artifact: string
  subject_sha256: string
  start: number | null
  end: number | null
}

export type GlobalReviewState = 'pending' | 'PASS' | 'FAIL' | 'UNVER'

export type GlobalReviewKind =
  | 'world_rule_version'
  | 'secret'
  | 'reveal'
  | 'known_by'
  | 'name_distinctiveness'
  | 'address_context'
  | 'world_rule_kind'
  | 'character_set_complete'
  | 'relation_set_complete'
  | 'world_rule_set_complete'
  | 'secret_set_complete'
  | 'role'
  | 'fate'
  | 'relation'
  | 'knowledge_event'
  | 'setting_fact'
  | 'secret_setup'
  | 'conflict_partition'
  | 'arc_analysis'

/** Server-side verdict: does this item hold Stage4 closed, or is it audit-only? */
export type GlobalReviewEffect = 'blocking' | 'disclosure'

export interface GlobalReviewGenerationDisposition {
  action: 'quarantine_group'
  reason: string
}

export interface GlobalReviewItem {
  item_id: string
  kind: GlobalReviewKind
  subject_path: string
  subject_sha256: string
  state: GlobalReviewState
  evidence: EvidenceRef[]
  actor: string | null
  decided_at: string | null
  reason: string | null
  /** Explicit audit decision for generation; UNVER remains UNVER. */
  generation_disposition: GlobalReviewGenerationDisposition | null
  /**
   * Derived by the server. Never recompute it here: a local copy of the
   * classification table becomes a second judge, and a newly added
   * high-impact kind would keep rendering as audit-only with nothing
   * making a sound.
   */
  effect: GlobalReviewEffect
  /** Derived by the server; every item of one subject shares this key. */
  group_key: string
}

export interface GlobalReviewResponse {
  /** Hash persisted with the review artifact. */
  source_sha256: string
  /** Hash recomputed from the current complete GlobalGrounding. */
  current_subject_sha256: string
  current: boolean
  /** Blocking surface only — the strict truth result, not the generation gate. */
  approved: boolean
  /** Current downstream view is safe after deterministic quarantine, if any. */
  generation_safe: boolean
  /** Complete fact groups omitted from generation; their raw UNVER rows remain below. */
  quarantined_groups: string[]
  /** seen.global 的阅读凭证；主体不 current 时为 null，此时不得提供确认动作。 */
  review_token: string | null
  /** `{effect: {state: n}}`; both key levels always present, zeros included. */
  effect_counts: Record<GlobalReviewEffect, Record<GlobalReviewState, number>>
  items: GlobalReviewItem[]
}

export interface GlobalReviewDecision {
  item_id: string
  expected_subject_sha256: string
  state: Exclude<GlobalReviewState, 'pending'>
  reason: string
  evidence: EvidenceRef[]
  generation_disposition?: GlobalReviewGenerationDisposition | null
}

export interface GlobalReviewBlockingScopeDecision {
  expected_source_sha256: string
  expected_blocking_scope_sha256: string
  expected_workpack_sha256: string
  expected_item_count: number
  reviewed_chapter_ordinals: number[]
  /** Emitted templates are UNVER; the reviewer must explicitly change this to PASS. */
  state: 'PASS' | 'UNVER'
  reason: string
  evidence: EvidenceRef[]
}

export interface GlobalReviewDecisionSet {
  /** Self-reported audit label; not an authenticated identity. */
  actor: string
  decisions: GlobalReviewDecision[]
  /** Optional complete blocking-surface default; explicit decisions are exceptions. */
  blocking_scope?: GlobalReviewBlockingScopeDecision | null
}

/* ---------------------------------------------------------------- stage4 */

export interface FactRef {
  kind:
    | 'character'
    | 'fact_atom'
    | 'world_rule'
    | 'secret'
    | 'relation'
    | 'setting'
    | 'chapter_boundary'
  ref: string
}

export interface PlanGoal {
  intent: 'establish' | 'advance' | 'open' | 'close' | 'withhold' | 'resolve'
  fact_refs: FactRef[]
}

/** Writer-facing goal: every planning ID has already been resolved by Stage4. */
export interface CtxGoal {
  intent: PlanGoal['intent']
  facts: string[]
}

export interface SemanticContract {
  chapter_function: string
  information_delta: string[]
  character_state_delta: string[]
  relationship_delta: string[]
  reader_knowledge_delta: string[]
  must_land_fact_refs: string[]
  context_fact_refs: string[]
  causal_edges: { before: string; after: string }[]
  entry_state_refs: string[]
  ending_state_refs: string[]
}

export interface PlanScene {
  i: number
  /** producer-owned 稳定槽位；模型不得重排或自造。 */
  slot: string
  budget_chars: number
  purpose: string
  role: string
  delivery: string
  transition: string
  land_fact_refs: string[]
  context_fact_refs: string[]
  turn_fact_ref: string
  entry_state_refs: string[]
  exit_state_refs: string[]
}

/** 持久化声明 + API 从当前计划现场复算的只读向量；取值域由 `/api/enums` 发布。 */
export interface StructurePlanDecision {
  mode: string
  reason: string
  declared_change_kinds: string[]
  computed_change_kinds: string[]
}

export interface PlanChapterLine {
  output_ordinal: number
  from_source_ordinals: number[]
  chapter_goal: PlanGoal
  open_hook_goal: PlanGoal
  end_hook_goal: PlanGoal
  structure_decision: StructurePlanDecision
  semantic_contract: SemanticContract
  chapter_rewrite_brief: string
  scenes: PlanScene[]
}

/** 章索引一行。⚠ 没有 in_band/style_hits —— 那是判据结论，去 join /check/stage4。 */
export interface Stage4IndexRow {
  output_ordinal: number
  chapter_goal: string
  structure_decision: StructurePlanDecision
  budget_chars: number | null
  scenes: number
}

export interface CtxSemanticContract {
  chapter_function: string
  information_delta: string[]
  character_state_delta: string[]
  relationship_delta: string[]
  reader_knowledge_delta: string[]
  must_land_facts: string[]
  context_facts: string[]
  causal_edges: { before: string; after: string }[]
  entry_state: string[]
  ending_state: string[]
}

/** 写手直接执行的新场景任务（`CtxScene`）。 */
export interface CtxScene {
  slot: string
  purpose: string
  role: string
  delivery: string
  transition: string
  budget_chars: number
  required_facts: string[]
  context_facts: string[]
  turn_fact: string
  entry_state: string[]
  exit_state: string[]
  stages: { time_layer: string; place: string | null; present: string[] }[]
  verbatim?: string[] | null
  keep_value?: string[] | null
  must_appear?: string[] | null
  must_hold?: string[] | null
}

/** 一个输出章的完整任务书（`CtxChapter`）。 */
export interface CtxChapter {
  output_ordinal: number
  from_source_ordinals: number[]
  chapter_goal: CtxGoal
  open_hook_goal: CtxGoal
  end_hook_goal: CtxGoal
  semantic_contract: CtxSemanticContract
  chapter_rewrite_brief: string
  budget_chars: number
  source_chars: number
  scenes: CtxScene[]
  characters: { char_id: string; display_name: string; role: string }[]
  narration: PlanNarration[]
  facts: Facts
}

export interface Stage4Response {
  sid: string
  title: string
  /** true = 恒等基线（没跑过 stage4，现算的「如果什么都不改」），不是真计划 */
  identity_plan: boolean
  /** 与 index / plan_chapter 同一份正式 plan 的结构主体；恒等基线为 null */
  structure_subject_sha256: string | null
  chapters: number[]
  ordinal: number
  index: Stage4IndexRow[]
  plan_chapter: PlanChapterLine
  /** null = 计划在而 ctx 不在盘上（右半边缺席，ctx_state 说明处境） */
  ctx_chapter: CtxChapter | null
  /** 与写手同一把尺子（prompts.target_band）；null ⇔ ctx_chapter=null */
  writer_target: [number, number] | null
  plan_state: ArtifactState
  ctx_state: ArtifactState
  _provenance: Provenance | null
  _ctx_provenance: Provenance | null
}

/* ---------------------------------------------------------------- stage5 */

/** 写手拿到的下发面 —— 与写手同一份（build_material 的 blocks），不是重组的第二份。 */
export interface Materials {
  blocks: string[]
  chars: number
  writer_target: [number, number]
}

/** 原文↔成稿的一个对齐块。anchored=false = 本块并入了无锚的拍。 */
export interface AlignRow {
  beats: number[]
  l: string
  r: string
  anchored: boolean
}

/** 对齐的账。锚点覆盖率必须常驻显示（「锚 3/8 拍」）。 */
export interface DraftAlign {
  beats: number
  anchors: number
  rows: number
  inversions: number
  coverage: number
  /** 旧服务缺字段时屏幕必须显示 unknown，不能按 coverage 自行补判。 */
  coverage_threshold?: number
  coverage_state?: 'low' | 'adequate'
  /** 常驻：这是对应关系，不是判定 */
  note: string
}

export interface ChapterSegment {
  /** producer-owned stable slot; the edit API must return the same complete slot set. */
  slot: string
  text: string
}

/** Stage5 改稿 preview 给出的唯一合法提交语义；前端不按 finding 自行选择。 */
export type DraftEditConfirmation = 'formal' | 'working_draft'

/** 只读预检只提交完整 typed 槽位，不带人工审计或落盘意图。 */
export interface DraftEditPreviewRequest {
  segments: ChapterSegment[]
}

/** 与正式 apply 共用归一、判据和 subject 构造的只读服务端投影。 */
export interface DraftEditPreview {
  output_ordinal: number
  current_chapter_sha256: string
  preview_subject_sha256: string
  normalized_segments: ChapterSegment[]
  findings: Finding[]
  current_hard_findings: Finding[]
  introduced_hard_findings: Finding[]
  resolved_hard_findings: Finding[]
  required_confirmation: DraftEditConfirmation
}

/** 正式 apply 必须原样回传 preview 的归一稿、两枚 SHA 与确认语义。 */
export interface DraftEditRequest {
  segments: ChapterSegment[]
  actor: string
  reason: string
  current_chapter_sha256: string
  preview_subject_sha256: string
  confirmation: DraftEditConfirmation
}

export interface Draft {
  chars: number
  budget_chars: number | null
  overlap_excl_verbatim: number | null
  /** schema-v2 disk authority; consumers deterministically project whole-chapter text. */
  segments: ChapterSegment[]
  /** null = 本章无锚可对（对齐辅助诚实缺席，不编分界线） */
  align: DraftAlign | null
  align_rows: AlignRow[]
  freshness: Freshness
  reason: string
  _provenance: Provenance
}

export interface DraftIndexRow {
  output_ordinal: number
  budget_chars: number | null
  has_draft: boolean
  invalid: boolean
  chars: number | null
  overlap_excl_verbatim: number | null
  freshness: Freshness | null
}

export interface Stage5TrimAudit {
  output_ordinal: number
  reason: string
  context: string
}

export interface Stage5Response {
  sid: string
  title: string
  /** false = 写作上下文还没建。给「先跑 stage4」，**不显示假材料**。 */
  ctx_exists: boolean
  /** null = 计划读不回来，「该有几章」判不了（与 0 是两件事） */
  planned: number | null
  written: number
  invalid: number
  missing: number[]
  /** 旧 deep/shallow 路径只报兼容性，不混入 canonical 单臂稿。 */
  legacy_incompatible: boolean
  legacy_paths: string[]
  chapters: number[]
  ordinal: number
  index: DraftIndexRow[]
  source_text: string | null
  source_chars_raw: number | null
  materials: Materials | null
  /** 一章至多一份现役正式稿；旧轮次在服务端归档。 */
  drafts: Draft[]
  invalid_drafts: Draft[]
  /** 本章仍待人工确认“保留未裁剪正文”的超上限事实；null = 无待销账项。 */
  trim_audit: Stage5TrimAudit | null
  ctx_state: ArtifactState
}

/* ------------------------------------------------------ stage5 · 验收 */

export interface ReviewIndexRow {
  output_ordinal: number
  /** 四 sev 键恒在（0 也写） */
  finding_counts: Record<Severity, number>
  unresolved: number
}

export interface ReviewChapter {
  output_ordinal: number
  findings: Finding[]
}

export interface ReviewResponse {
  sid: string
  title: string
  review_seq: number
  review_state: 'fresh' | 'stale' | 'unknown' | 'legacy_incompatible'
  review_reasons: string[]
  /** 全书按 sev 现数，四键恒在 —— 不读产物的稀疏 counts */
  finding_counts: Record<Severity, number>
  book: Finding[]
  chapters: number[]
  ordinal: number
  index: ReviewIndexRow[]
  chapter: ReviewChapter
}

/* ---------------------------------------------------------------- stage6 */

export interface HookMech {
  punct_fixed: number
  quotes_fixed: number
  paras_fixed: number
}

export interface HookContradiction {
  assertion: string
  evidence: string
  reason: string
}

export interface HookQualityScores {
  read_on_desire: number
  body_voice_match: number
  concrete_scene_and_specificity: number
  curiosity_and_payoff_promise: number
  hook_craft: number
  synopsis_penalty: number
}

export interface HookCandidate {
  index: number
  approach: string
  /** measure() 那把尺子的去空白字数（服务端现算，与判据同源） */
  chars: number
  text: string
  subject_sha256: string
  mech: HookMech
  findings: Finding[]
  /**
    * `unreviewed` = 没有可用的当前审判；`uncertain` = 判过、有证据、但服务端不替人盖章。
    * ⚠ 曾有 `clear` / `contradiction` 两个取值，它们只由服务端的自动放行门产生，
    * 而那套装置已整批下线 ⇒ 两个都不会再出现。
    * ⚠ 随之删掉的还有两格布尔：`selectable`（「可直接定稿」，收成两态后恒 false）与
    * `human_resolvable`（「可由人工消解」，恒 true）——一条永远走不通的捷径与一道
    * 永远开着的闸。**每一条候选都要人显式核验**；要判还用不用人看，读这一格本身。
    */
  semantic_state: 'uncertain' | 'unreviewed'
  semantic_reason: string
  contradictions: HookContradiction[]
  quality: HookQualityScores | null
  quality_score: number | null
  quality_rank: number | null
  /** 这一条是不是当前定稿 */
  approved: boolean
}

export interface HookApproved {
  /**
    * 从**哪一条候选**来的（位置身份 `c1`/`c2`/`c3`）；`null` = 没有前身（人手写）。
    * ⚠ 这里曾是策略名 + 一个 `human_edit`。同一种策略可以有不止一条候选 ⇒
    * 策略名答不出「是哪一条」，而定稿要回答的正是这个。
    */
  slot: string | null
  /** 那条候选用的什么策略（服务端现算）；`slot` 为 null 时同为 null。 */
  approach: string | null
  chars: number
  text: string
  origin: string
  mech: HookMech
  findings: Finding[]
  resolution: {
    actor: string
    reason: string
    subject_sha256: string
    /** 恒 `human_verification`：`calibrated_pass` 是被服务端裁决掉的取值。 */
    basis: 'human_verification'
    /** 人当时读的是哪一份审判。`null` = 定稿时没有可用审判，人是在零证据下拍的板。 */
    semantic_review_seq: number | null
    /** null = 旧批准；下一次终审后才可用于 already_current。 */
    workpack_sha256: string | null
  }
}

export interface JudgedDraft {
  output_ordinal: number
  round: number
  sha1: string
  consumed: { output_ordinal: number; text_sha1: string; has_budget_chars: boolean | null } | null
}

export interface Stage6Response {
  sid: string
  title: string
  source_hook_text: string
  lead_upto: number
  rationale: string
  candidates: HookCandidate[]
  /** null = 还没定稿 */
  approved: HookApproved | null
  /** 键是成稿完整相对路径 */
  judged: Record<string, JudgedDraft>
  sources: Sources
  invalid_exists: boolean
  freshness: Freshness
  reason: string
  ghosts: string[]
  _provenance: Provenance
}

/* ---------------------------------------------------------------- stage7 */

export interface NameChar {
  char_id: string
  old_canonical: string
  new_canonical: string
  renamed: boolean
  /** renamed=true 时恒 null */
  not_renamed_reason: string | null
  reason: string
  aliases: { old: string; new: string }[]
  /** 这个角色全部字面在 applied 记账里的替换总次数（按人求和的读数） */
  hits: number
}

/** 漏网叫法候选 —— 唯一能发现人名表本身漏了人的地方。顺序是承重的。 */
export interface Residual {
  stem: string
  where: string
  /** 命中处起 3 个字（后端是字符串，不是数字） */
  hit: string
  context: string
  confidence: string
  unit: string | null
  suggest: string | null
}

export interface StemBaseline {
  total: number
  covered: number
  residual: number
  /** total=0 时是 null（那种 stem 原文里一次都没出现过），不是 0.0 */
  ratio: number | null
}

/** 逐章对照一行。changed 是 bool（不是 kind 字符串）。 */
export interface NameRow {
  l: string
  r: string
  changed: boolean
}

export interface IdentityBaseline {
  roundtrip_ok: boolean
  chapters: number
  mismatches: string[]
  blind: string[]
}

export interface Stage7Response {
  sid: string
  title: string
  surname_map: Record<string, string>
  characters: NameChar[]
  extra_subs: { old: string; new: string; origin: string; note?: string | null }[]
  subs_count: number
  kept_count: number
  residual: Residual[]
  baselines: Record<string, StemBaseline>
  approved: {
    origin: string
    actor: string
    reason: string
    subject_sha256: string
    /** null = 旧批准；下一次终审后才可用于 already_current。 */
    workpack_sha256: string | null
  } | null
  /** Canonical hash of surname_map + characters + extra_subs; approval is locked to this value. */
  approval_subject_sha256: string
  book: Finding[]
  /** null = 当前流程从没跑过恒等基线 */
  identity: IdentityBaseline | null
  /** 组装时的账；book_txt_on_disk 才是盘上此刻的实况（两者可分家） */
  book_txt: { sha1: string; chars: number; chapters: number } | null
  book_txt_on_disk: boolean
  /**
   * 成书文件名（`<书名>_rewriting.txt`），由服务端下发。
   * ⚠ 另存名、按钮文案都用它，**不要拿 title 再拼一次**：那是同一条命名规则的第二个
   * 落点（`R-11.4`），与后端的 `novel/paths.py::book_txt_rel` 分家时，盘上那份和
   * 下载那份会叫两个名字。
   */
  book_filename: string
  sources: Sources
  chapters: number[]
  /** 逐章对照时才有；与 rows 同进同出 */
  ordinal: number | null
  rows: NameRow[] | null
  /** null = 文案没定稿（对照的左边没有来源） */
  hook: { before: string; after: string } | null
  invalid_exists: boolean
  freshness: Freshness
  reason: string
  ghosts: string[]
  _provenance: Provenance
}

/* ----------------------------------------------------------------- 其他 */

export interface InboxItem {
  kind: InboxKind
  stage: string
  /** 输出章口径；null = 书级 / 整份产物 */
  chapter: number | null
  rule: string
  what: string
  how: string
  attention: 'pending' | 'reviewed_retained'
  delivery_effect: 'blocking' | 'disclosure'
  decision: {
    actor: string
    reason: string
    subject_sha256: string
    state: string
    decided_at: string
  } | null
}

export interface InboxResponse {
  items: InboxItem[]
  /** 八键全出（0 也要出） */
  counts: Record<InboxKind, number>
  attention_counts: Record<'pending' | 'reviewed_retained', number>
  delivery_counts: Record<'blocking' | 'disclosure', number>
  total: number
  wontfix: number
}

/* ------------------------------------------------------------ 校验报告 */

export interface CheckAccountingLine {
  key: string
  item: string
  recorded: boolean
  count: number
  detail: unknown[]
}

export interface CheckProvenance {
  freshness: Freshness
  reason: string
  ghosts: string[]
  edges: unknown[]
  invalid: string[]
  revalidated: boolean
  origin: string | null
  [k: string]: unknown
}

/**
 * 一次校验的完整输出（`contracts/schema/report.py::CheckReport`）。
 * 分组按 `rules[].status` 现做（groupby 是展示不是判定）；
 * `badge`/`passed`/`counts` 是**服务端折好的判定**，前端不再数。
 */
export interface CheckReport {
  sid: string
  node: string
  stage: string
  provenance: CheckProvenance
  rules: RuleLine[]
  accounting: CheckAccountingLine[]
  badge: Badge
  passed: boolean
  counts: { ok: number; hit: number; unver: number; skipped: number }
}

/** 与服务端 `STAGES` 逐值相等；Stage3 已退役，编号保留空洞。 */
export type CheckKind =
  | 'stage0'
  | 'stage1'
  | 'stage2'
  | 'stage4'
  | 'stage5'
  | 'stage6'
  | 'stage7'

export const CHECK_KINDS: CheckKind[] = [
  'stage0',
  'stage1',
  'stage2',
  'stage4',
  'stage5',
  'stage6',
  'stage7',
]
