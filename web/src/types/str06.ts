/**
 * STR-06 候选链只读监控面的前端类型（F7d）。
 *
 * 与 `contracts/schema/str06/api.py` 的响应模型一一对应；候选链在 F8 激活前
 * 不进现役产品面，这一层也不伪装成已切换——页面顶部常驻「候选链」标注。
 *
 * 两条纪律随类型一起搬过来：
 *  · 没有 badge / passed / counts：判定只有现役 check 报告与回执的机械项名单；
 *  · present=false 与「合法空值」是两件事：半程候选根用 present 表达，空数组
 *    只在盘上真的有空列表时出现。
 */

/** 一个称谓的时间窗（源事实）：从 from_chapter 起可解析到该人物。 */
export interface Str06SurfaceWindow {
  surface: string
  from_chapter: number
  until_chapter: number | null
}

export interface Str06Character {
  char_id: string
  canonical: string
  surfaces: Str06SurfaceWindow[]
}

export interface Str06NameStructure {
  families: Record<string, string>
  people: Record<string, Record<string, unknown>>
}

export interface Str06CharactersView {
  present: boolean
  contract_version: string | null
  characters: Str06Character[]
  name_structure: Str06NameStructure | null
}

export interface Str06BlueprintBeat {
  beat_id: string
  event: string
  participants: string[]
  effects: Record<string, unknown>[]
  /** v13：认知转移（命题＋持有者＋模式），取代 reveals */
  cognition: Record<string, unknown>[]
  /** v13：悬念转移（open/close） */
  loops: Record<string, unknown>[]
  scene_boundary: string | null
  temporal_mode: string | null
}

export interface Str06BlueprintChapter {
  chapter: number
  beats: Str06BlueprintBeat[]
  /** v13：compiler 生成的场景分段（跨章场景在续章同样出现） */
  scenes: Record<string, unknown>[]
  causal_edges: Record<string, unknown>[]
}

export interface Str06BlueprintView {
  present: boolean
  world_constraints: Record<string, unknown>[]
  chapters: Str06BlueprintChapter[]
}

export interface Str06DesignScene {
  scene_id: string
  driver: Record<string, string>
  realization_brief: string
  pov: string
  voice: string
  avoid: string[]
  beats: Record<string, unknown>[]
}

export interface Str06DesignChapter {
  chapter_ordinal: number
  purpose: string
  scene_ids: string[]
}

export interface Str06DesignHook {
  person: string
  focalizers: string[]
  materials: string[]
  concealed: string[]
  forbidden_reveals: number
  design_brief: string
}

export interface Str06DesignBinding {
  obligations: number
  entries: Record<string, unknown>[]
  unbound: string[]
}

export interface Str06DesignView {
  present: boolean
  graph_id: string | null
  scenes: Str06DesignScene[]
  chapters: Str06DesignChapter[]
  hook: Str06DesignHook | null
  binding: Str06DesignBinding | null
}

export interface Str06IrChapter {
  chapter_ordinal: number
  purpose: string
  length_budget: number
  scene_ids: string[]
  scene_lines: string[]
}

export interface Str06IrNavView {
  present: boolean
  book_title: string
  target_chapter_count: number | null
  total_length_budget: number | null
  length_band_low: number | null
  length_band_high: number | null
  chapters: Str06IrChapter[]
  release_schedule: Record<string, unknown>[]
}

export interface Str06ChapterReceipt {
  status: string
  mechanical_checks: string[]
  semantic: string
  length: Record<string, number>
  length_disclosed: boolean
  request_identity: string
  protocol_version: string
  renderer_version: string
  validator_version: string
}

export interface Str06ChapterView {
  ordinal: number
  present: boolean
  prose: string | null
  receipt: Str06ChapterReceipt | null
}

export interface Str06HookView {
  present: boolean
  prose: string | null
  receipt: Record<string, unknown> | null
}

export interface Str06NamingEntry {
  char_ref: string
  family: string
  given: string | null
  full: string
  aliases: Record<string, string>
  keep_reason: string | null
}

export interface Str06NamingView {
  present: boolean
  entries: Str06NamingEntry[]
}

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
